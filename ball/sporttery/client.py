"""竞彩网官方 API 客户端：获取每周足球 / 篮球竞猜赛程与固定奖金。

数据源：中国体育彩票竞彩网官方接口
  https://webapi.sporttery.cn/gateway/uniform/{football|basketball}/getMatchCalculatorV1.qry?channel=c

返回结构中每场包含中文队名、联赛名、北京时间、以及各玩法（胜平负 / 让球 /
总进球 / 比分 / 半全场 / 大小分 / 让分）的固定奖金。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://webapi.sporttery.cn/gateway/uniform"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.sporttery.cn/",
}


def _bj_to_utc(date_str: str, time_str: str) -> datetime:
    """竞彩时间为北京时间（UTC+8），转为 UTC naive datetime。"""
    ts = f"{date_str} {(time_str or '00:00:00')[:8]}"
    bj = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone(timedelta(hours=8))
    )
    return bj.astimezone(timezone.utc).replace(tzinfo=None)


def _num(d: Optional[dict], key: str) -> Optional[float]:
    if not d:
        return None
    v = d.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


@dataclass
class SportteryMatch:
    sport: str                     # football | basketball
    match_num: str                # 周二101
    league_name: str              # 世界杯 / 美国女子篮球联盟
    league_code: str              # WCC / WBA（竞彩自有编码）
    home_name: str                # 中文全名
    away_name: str
    home_abbr: str
    away_abbr: str
    match_date: str               # 2026-07-15
    match_time: str               # 03:00:00
    dt_utc: datetime
    # 足球玩法
    had: dict = field(default_factory=dict)    # 胜平负 {h,d,a}
    hhad: dict = field(default_factory=dict)   # 让球胜平负 {h,d,a,line}
    ttg: dict = field(default_factory=dict)    # 总进球
    crs: dict = field(default_factory=dict)    # 猜比分
    hafu: dict = field(default_factory=dict)   # 半全场
    # 篮球玩法
    mnl: dict = field(default_factory=dict)    # 胜负 {h,a}
    hdc: dict = field(default_factory=dict)    # 让分胜负 {h,a,line}
    hilo: dict = field(default_factory=dict)   # 大小分 {h,l,line}
    match_id: Optional[int] = None             # 竞彩自有赛事 ID
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return asdict(self)


def fetch(sport: str, timeout: int = 20) -> list[SportteryMatch]:
    """抓取某运动的竞猜赛程。sport ∈ {football, basketball}。"""
    url = f"{API_BASE}/{sport}/getMatchCalculatorV1.qry?channel=c"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.error("竞彩[%s] 请求失败：%s", sport, exc)
        return []
    data = resp.json()
    info = (data.get("value", {}) or {}).get("matchInfoList") or []
    if not info:
        logger.info("竞彩[%s] 当前无在售赛事", sport)
        return []
    matches = info[0].get("subMatchList") or []
    out: list[SportteryMatch] = []
    for m in matches:
        try:
            out.append(_parse(sport, m))
        except Exception as exc:  # noqa: BLE001
            logger.warning("竞彩[%s] 解析失败 %s：%s",
                          sport, m.get("matchNumStr"), exc)
    logger.info("竞彩[%s] 获取 %d 场", sport, len(out))
    return out


def _parse(sport: str, m: dict) -> SportteryMatch:
    md = m.get("matchDate") or ""
    mt = m.get("matchTime") or "00:00:00"
    dt = _bj_to_utc(md, mt)
    had = m.get("had") or {}
    hhad = m.get("hhad") or {}
    base: dict[str, Any] = dict(
        sport=sport,
        match_num=m.get("matchNumStr", ""),
        league_name=m.get("leagueAllName") or m.get("leagueAbbName") or "",
        league_code=str(m.get("leagueCode") or m.get("leagueId") or ""),
        home_name=m.get("homeTeamAllName") or m.get("homeTeamAbbName") or "",
        away_name=m.get("awayTeamAllName") or m.get("awayTeamAbbName") or "",
        home_abbr=m.get("homeTeamAbbName") or "",
        away_abbr=m.get("awayTeamAbbName") or "",
        match_date=md,
        match_time=mt,
        dt_utc=dt,
        match_id=m.get("matchId"),
        raw=m,
    )
    if sport == "football":
        base.update(
            had={"h": _num(had, "h"), "d": _num(had, "d"), "a": _num(had, "a")},
            hhad={
                "h": _num(hhad, "h"), "d": _num(hhad, "d"), "a": _num(hhad, "a"),
                "line": (hhad.get("goalLine") or "").strip(),
            },
            ttg=m.get("ttg") or {},
            crs=m.get("crs") or {},
            hafu=m.get("hafu") or {},
        )
    else:
        mnl = m.get("mnl") or {}
        hdc = m.get("hdc") or {}
        hilo = m.get("hilo") or {}
        base.update(
            mnl={"h": _num(mnl, "h"), "a": _num(mnl, "a")},
            hdc={
                "h": _num(hdc, "h"), "a": _num(hdc, "a"),
                "line": (hdc.get("goalLine") or "").strip(),
            },
            hilo={
                "h": _num(hilo, "h"), "l": _num(hilo, "l"),
                "line": (hilo.get("goalLine") or "").strip(),
            },
        )
    return SportteryMatch(**base)
