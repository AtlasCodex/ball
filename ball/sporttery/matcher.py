"""将竞彩场次匹配到本地 ESPN 赛程（matches 表）。

匹配键：联赛（经 mapping.LEAGUE_MAP）+ 比赛日期（北京时间转 UTC，±1 天容差）
+ 双队名（经 mapping.TEAM_ALIASES 把中文队名映射到 ESPN 英文队名）。

未能映射的联赛 / 队名 / 本地缺赛程，都会透明地写进结果，便于补全桥接表。
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from ball.db.engine import session_scope
from ball.db.models import Match
from ball.sporttery.client import SportteryMatch
from ball.sporttery.mapping import resolve_league, resolve_team

logger = logging.getLogger(__name__)

# 匹配时忽略的标点/装饰字符
_PUNCT = set(" 　.-_/&'’.·,()")


def _norm(s: str) -> str:
    """折叠大小写、空格、重音与常见标点，用于宽松比较。

    例：'Brighton & Hove Albion' -> 'brightonhovealbion'；
       'São Paulo' -> 'saopaulo'；'F.C. København' -> 'fckobenhavn'。
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c not in _PUNCT)


def _team_eq(a: str, b: str) -> bool:
    """队名宽松相等：归一化后完全相等，或一方是另一方的子串（长度≥4）。

    可容忍 ESPN displayName 与别名英文侧的差异，如
    'Bournemouth' ⊂ 'AFC Bournemouth'、'Lakers' ⊂ 'Los Angeles Lakers'。
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return True
    return False


def match_one(sm: SportteryMatch) -> dict:
    """匹配单场，返回带 match_id（命中时）的字典。"""
    lg = resolve_league(sm.league_name) or resolve_league(sm.league_code)
    if not lg:
        return {"matched": False,
                "reason": f"未知联赛（未在 LEAGUE_MAP）：{sm.league_name}"}
    sport, code = lg

    home_espn = resolve_team(sport, code, sm.home_name)
    away_espn = resolve_team(sport, code, sm.away_name)
    if not home_espn or not away_espn:
        miss = sm.home_name if not home_espn else sm.away_name
        return {"matched": False, "sport": sport, "league_code": code,
                "reason": f"队名未映射（请在 TEAM_ALIASES 补全）：{miss}"}

    lo = sm.dt_utc - timedelta(days=1)
    hi = sm.dt_utc + timedelta(days=1)
    with session_scope() as s:
        rows = s.execute(
            select(Match)
            .where(Match.league_code == code)
            .where(Match.start_time.between(lo, hi))
        ).scalars().all()
        for m in rows:
            ht, at = m.home_team, m.away_team
            if not ht or not at:
                continue
            # 允许主客颠倒（竞彩与 ESPN 的主客判定偶有不同）
            same = _team_eq(home_espn, ht.name) and _team_eq(away_espn, at.name)
            swap = _team_eq(home_espn, at.name) and _team_eq(away_espn, ht.name)
            if same or swap:
                return {
                    "matched": True, "sport": sport, "league_code": code,
                    "match_id": m.id,
                    "home_espn": ht.name, "away_espn": at.name,
                }
    return {"matched": False, "sport": sport, "league_code": code,
            "reason": f"本地无该日期赛程（需 --sync 抓取 {code}）："
                       f"{sm.home_name} vs {sm.away_name}"}


def match_all(sms: list[SportteryMatch]) -> list[dict]:
    out: list[dict] = []
    for sm in sms:
        r = match_one(sm)
        r["match"] = sm.to_dict()
        out.append(r)
    return out


def distinct_leagues(matched: list[dict]) -> set[tuple[str, str]]:
    """返回出现过的 (sport, league_code)，用于 --sync 抓取。"""
    seen: set[tuple[str, str]] = set()
    for r in matched:
        if r.get("league_code"):
            seen.add((r.get("sport"), r["league_code"]))
    return seen
