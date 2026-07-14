"""ESPN 公共 API 客户端与通用爬虫（覆盖足球与篮球）。

ESPN 未公开文档的站点 API（site.api.espn.com）无需密钥，返回结构在
足球(soccer)与篮球(basketball)间高度一致，因此这里实现统一的客户端与爬虫，
足球/NBA 仅通过 sport 参数区分。
"""
from __future__ import annotations

import calendar
import json
import logging
import time
from datetime import date, datetime
from typing import Any, Optional

import requests
from sqlalchemy import select

from ball.config import get
from ball.db.engine import session_scope
from ball.db.models import Injury, League, Match, MatchDetail, Player, Team

logger = logging.getLogger(__name__)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class ESPNClient:
    BASE = "https://site.api.espn.com/apis/site/v2/sports"

    def __init__(self, sport: str, league: str):
        self.sport = sport
        self.league = league
        self.delay = float(get("crawler.request_delay", 1.0))
        self.timeout = int(get("crawler.timeout", 20))
        self.retries = int(get("crawler.retries", 3))
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": get("crawler.user_agent", "Mozilla/5.0")}
        )

    def _url(self, path: str) -> str:
        return f"{self.BASE}/{self.sport}/{self.league}/{path}"

    def get_json(self, path: str, params: Optional[dict] = None, _attempt: int = 0) -> dict:
        url = self._url(path)
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            if _attempt < self.retries:
                time.sleep(min(2 ** _attempt, 10))
                return self.get_json(path, params, _attempt + 1)
            logger.error("ESPN 请求失败: %s params=%s -> %s", url, params, exc)
            raise


class Crawler:
    """通用爬虫：联赛 / 球队 / 赛程 / 球员 / 伤病 / 比赛详情。"""

    sport = "football"  # 子类覆盖为 "soccer" / "basketball"

    def __init__(self, league_code: str, season: Optional[str] = None):
        self.league_code = league_code
        self.season = season
        self.client = ESPNClient(self.sport, league_code)

    # ------------------------- 基础 upsert -------------------------
    def _get_league(self, session, name: str) -> League:
        league = session.scalar(
            select(League).where(League.code == self.league_code)
        )
        if league is None:
            league = League(
                code=self.league_code,
                name=name,
                sport="basketball" if self.sport == "basketball" else "football",
                season=self.season,
            )
            session.add(league)
            session.flush()
        return league

    def _get_team(self, session, team_obj: dict) -> Optional[Team]:
        source_id = str(team_obj.get("id"))
        if not source_id:
            return None
        team = session.scalar(
            select(Team).where(
                Team.league_code == self.league_code, Team.source_id == source_id
            )
        )
        if team is None:
            team = Team(
                source_id=source_id,
                league_code=self.league_code,
                name=team_obj.get("displayName", team_obj.get("name", "Unknown")),
                short_name=team_obj.get("shortDisplayName"),
                abbreviation=team_obj.get("abbreviation"),
                logo_url=team_obj.get("logo"),
            )
            session.add(team)
            session.flush()
        else:
            team.name = team_obj.get("displayName", team.name)
            team.logo_url = team_obj.get("logo", team.logo_url)
        return team

    # ------------------------- 联赛 / 球队 -------------------------
    def sync_league_and_teams(self) -> None:
        data = self.client.get_json("teams")
        sports = data.get("sports", [])
        league_name = self.league_code
        teams = []
        for sp in sports:
            for lg in sp.get("leagues", []):
                league_name = lg.get("name", league_name)
                teams = lg.get("teams", [])
        with session_scope() as session:
            self._get_league(session, league_name)
            for t in teams:
                team_obj = t.get("team", t) if isinstance(t, dict) else t
                self._get_team(session, team_obj)
        logger.info("[%s] 同步球队 %d 支", self.league_code, len(teams))

    # ------------------------- 赛程 -------------------------
    def sync_schedule(self, dates: Optional[str] = None) -> int:
        """同步赛程。dates 形如 '2024'、'20240101'、'20240101-20240131'。"""
        params = {}
        if dates:
            params["dates"] = dates
        if self.season:
            params.setdefault("seasontype", 2)
        data = self.client.get_json("scoreboard", params)
        events = data.get("events", [])
        count = 0
        with session_scope() as session:
            for ev in events:
                comp = (ev.get("competitions") or [{}])[0]
                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home or not away:
                    continue
                home_team = self._get_team(session, home["team"])
                away_team = self._get_team(session, away["team"])
                if not home_team or not away_team:
                    continue
                status_type = comp.get("status", {}).get("type", {})
                state = status_type.get("state", "pre")
                status = {"pre": "scheduled", "in": "in", "post": "final"}.get(
                    state, "scheduled"
                )
                venue = (comp.get("venue") or {}).get("fullName")
                match = session.scalar(
                    select(Match).where(
                        Match.league_code == self.league_code,
                        Match.source_id == str(ev["id"]),
                    )
                )
                if match is None:
                    match = Match(
                        source_id=str(ev["id"]),
                        league_code=self.league_code,
                        sport="basketball" if self.sport == "basketball" else "football",
                    )
                    session.add(match)
                match.start_time = _parse_dt(ev.get("date")) or match.start_time
                match.status = status
                match.season = self.season
                match.home_team_id = home_team.id
                match.away_team_id = away_team.id
                match.venue = venue
                match.home_score = _to_int(home.get("score"))
                match.away_score = _to_int(away.get("score"))
                if self.sport == "basketball":
                    match.home_period_scores = _linescores(home)
                    match.away_period_scores = _linescores(away)
                count += 1
        logger.info("[%s] 同步赛程 %d 场 (dates=%s)", self.league_code, count, dates)
        return count

    def _season_months(self, year: int) -> list[tuple[int, int]]:
        """返回某赛季覆盖的 (年, 月) 列表。

        足球赛季跨年（8 月至次年 6 月）；NBA 赛季 10 月至次年 6 月。
        ESPN scoreboard 单次上限约 100 场，故必须按月分批才能拿全赛季。
        """
        if self.sport == "basketball":
            spans = [(year, m) for m in range(10, 13)] + [(year + 1, m) for m in range(1, 7)]
        else:
            spans = [(year, m) for m in range(8, 13)] + [(year + 1, m) for m in range(1, 7)]
        return spans

    def sync_schedule_seasons(self, seasons: list[str]) -> int:
        """按赛季逐月抓取历史赛程，扩充样本。

        seasons 形如 ['2022', '2023', '2024']；'2023' 表示 2023-24 赛季。
        """
        total = 0
        for season in seasons:
            try:
                year = int(str(season)[:4])
            except (ValueError, TypeError):
                logger.warning("[%s] 跳过非法赛季: %s", self.league_code, season)
                continue
            season_total = 0
            for y, m in self._season_months(year):
                last = calendar.monthrange(y, m)[1]
                rng = f"{y}{m:02d}01-{y}{m:02d}{last:02d}"
                try:
                    season_total += self.sync_schedule(rng)
                except Exception:  # noqa: BLE001
                    logger.warning("[%s] 月度赛程抓取失败 %s", self.league_code, rng)
            logger.info("[%s] 赛季 %s 抓取 %d 场", self.league_code, season, season_total)
            total += season_total
        return total

    # ------------------------- 球员 -------------------------
    @staticmethod
    def _normalize_athletes(athletes: list) -> list:
        """ESPN roster 两种结构都兼容：
        - 扁平：athletes = [player, player, ...]
        - 分组：athletes = [{position, items:[player,...]}, ...]
        """
        out: list = []
        for entry in athletes or []:
            if not isinstance(entry, dict):
                continue
            items = entry.get("items")
            if isinstance(items, list):
                out.extend(x for x in items if isinstance(x, dict))
            else:
                out.append(entry)
        return out

    def sync_players(self) -> int:
        data = self.client.get_json("teams")
        teams = []
        for sp in data.get("sports", []):
            for lg in sp.get("leagues", []):
                teams = lg.get("teams", [])
        count = 0
        injury_count = 0
        for t in teams:
            team_obj = t.get("team", t) if isinstance(t, dict) else t
            tid = team_obj.get("id") if isinstance(team_obj, dict) else None
            if not tid or str(tid) == "None":
                continue
            tid = str(tid)
            try:
                roster = self.client.get_json(f"teams/{tid}/roster")
            except Exception:  # noqa: BLE001
                continue
            athletes = self._normalize_athletes(roster.get("athletes"))
            with session_scope() as session:
                team = session.scalar(
                    select(Team).where(
                        Team.league_code == self.league_code, Team.source_id == tid
                    )
                )
                team_id = team.id if team else None
                team_name = team.name if team else None
                for a in athletes:
                    self._upsert_player(session, a, team_id, team_name)
                    count += 1
                    for inj in (a.get("injuries") or []):
                        self._upsert_roster_injury(
                            session, a, inj, team_id, team_name
                        )
                        injury_count += 1
        logger.info("[%s] 同步球员 %d 名（含伤病 %d 条）",
                    self.league_code, count, injury_count)
        return count

    def _upsert_player(self, session, a: dict, team_id: Optional[int],
                       team_name: Optional[str] = None) -> None:
        aid = str(a.get("id"))
        if not aid:
            return
        player = session.scalar(
            select(Player).where(
                Player.league_code == self.league_code, Player.source_id == aid
            )
        )
        pos = (a.get("position") or {}).get("abbreviation")
        status = (a.get("status") or {}).get("type")
        if player is None:
            player = Player(
                source_id=aid,
                league_code=self.league_code,
                name=a.get("displayName", "Unknown"),
                position=pos,
                jersey=_to_int(a.get("jersey")),
                team_id=team_id,
                headshot_url=(a.get("headshot") or {}).get("href"),
                status=status,
            )
            session.add(player)
        else:
            player.position = pos
            player.team_id = team_id
            player.status = status

    # ------------------------- 伤病 -------------------------
    def _upsert_roster_injury(self, session, a: dict, inj: dict,
                              team_id: Optional[int], team_name: Optional[str]) -> None:
        aid = str(a.get("id") or a.get("displayName") or "")
        name = a.get("displayName", "Unknown")
        injury = session.scalar(
            select(Injury).where(
                Injury.league_code == self.league_code, Injury.source_id == aid
            )
        )
        injury_type = _txt(inj.get("type"))
        status = _txt(inj.get("status")) or _txt(inj.get("details"))
        comment = inj.get("shortComment") or inj.get("longComment")
        if injury is None:
            injury = Injury(
                source_id=aid,
                league_code=self.league_code,
                sport="basketball" if self.sport == "basketball" else "football",
                player_name=name,
            )
            session.add(injury)
        injury.player_id = None
        injury.team_id = team_id
        injury.team_name = team_name
        injury.injury_type = injury_type
        injury.status = status
        injury.description = comment
        injury.updated = date.today()

    def sync_injuries(self) -> int:
        """优先用 roster 内的 injuries 字段；并兼容顶层 injuries 接口。"""
        try:
            data = self.client.get_json("injuries")
        except Exception:  # noqa: BLE001
            logger.warning("[%s] 暂无伤病数据接口", self.league_code)
            return 0
        # 顶层 injuries 接口可能是 {"injuries":[...]}
        blocks = data.get("teams") or []
        top = data.get("injuries") or []
        count = 0
        with session_scope() as session:
            for team_block in blocks:
                team_obj = team_block.get("team", {})
                tname = team_obj.get("displayName")
                tid = str(team_obj.get("id"))
                team = session.scalar(
                    select(Team).where(
                        Team.league_code == self.league_code, Team.source_id == tid
                    )
                ) if tid else None
                team_id = team.id if team else None
                for item in team_block.get("items", []):
                    athlete = item.get("athlete", {})
                    status = _txt(item.get("status")) or _txt(item.get("details"))
                    self._upsert_injury(
                        session, athlete=athlete, status=status,
                        injury_type=_txt(item.get("type")),
                        comment=item.get("shortComment") or item.get("longComment"),
                        team_id=team_id, team_name=tname,
                    )
                    count += 1
            for item in top:
                athlete = item.get("athlete") or item
                tid = str((item.get("team") or {}).get("id") or "")
                team = session.scalar(
                    select(Team).where(
                        Team.league_code == self.league_code, Team.source_id == tid
                    )
                ) if tid else None
                self._upsert_injury(
                    session, athlete=athlete,
                    status=_txt(item.get("status")),
                    injury_type=_txt(item.get("type")),
                    comment=item.get("shortComment") or item.get("longComment"),
                    team_id=team.id if team else None,
                    team_name=(item.get("team") or {}).get("displayName"),
                )
                count += 1
        logger.info("[%s] 同步伤病 %d 条", self.league_code, count)
        return count

    def _upsert_injury(
        self, session, athlete, status, injury_type, comment, team_id, team_name
    ) -> None:
        aid = str(athlete.get("id") or athlete.get("displayName") or "")
        name = athlete.get("displayName", "Unknown")
        injury = session.scalar(
            select(Injury).where(
                Injury.league_code == self.league_code, Injury.source_id == aid
            )
        )
        if injury is None:
            injury = Injury(
                source_id=aid,
                league_code=self.league_code,
                sport="basketball" if self.sport == "basketball" else "football",
                player_name=name,
            )
            session.add(injury)
        injury.player_id = None
        injury.team_id = team_id
        injury.team_name = team_name
        injury.injury_type = injury_type
        injury.status = status
        injury.description = comment
        injury.updated = date.today()

    # ------------------------- 比赛详情（技术数据） -------------------------
    def _detail_parts(self, data: dict) -> dict[str, Any]:
        """从 summary 中拆出结构化技术数据，按运动区分。

        足球：boxscore(球队统计) / events(进球红黄牌) / rosters(阵容)。
        篮球：boxscore(含球队+球员逐项统计) / plays(回合).
        """
        parts: dict[str, Any] = {}
        box = data.get("boxscore")
        if box:
            parts["boxscore"] = box
        lead = data.get("leaders")
        if lead:
            parts["leaders"] = lead
        if self.sport == "basketball":
            if data.get("plays"):
                parts["plays"] = data["plays"]
            if data.get("injuries"):
                parts["injuries"] = data["injuries"]
        else:
            if data.get("keyEvents"):
                parts["events"] = data["keyEvents"]
            if data.get("rosters"):
                parts["rosters"] = data["rosters"]
        return parts

    def _upsert_detail(self, session, match_id: int, kind: str, obj: Any) -> None:
        detail = session.scalar(
            select(MatchDetail).where(
                MatchDetail.match_id == match_id, MatchDetail.kind == kind
            )
        )
        if detail is None:
            detail = MatchDetail(match_id=match_id, kind=kind)
            session.add(detail)
        detail.payload = json.dumps(obj, ensure_ascii=False)

    def sync_match_detail(self, event_id: str) -> None:
        data = self.client.get_json("summary", {"event": event_id})
        parts = self._detail_parts(data)
        with session_scope() as session:
            match = session.scalar(
                select(Match).where(
                    Match.league_code == self.league_code, Match.source_id == str(event_id)
                )
            )
            if match is None:
                return
            for kind, obj in parts.items():
                self._upsert_detail(session, match.id, kind, obj)
            match.detail_fetched = True
        logger.debug("[%s] 同步比赛技术数据 event=%s parts=%s",
                     self.league_code, event_id, list(parts.keys()))

    def _mark_detail_attempted(self, event_id: str) -> None:
        """将抓取失败的比赛标记为已尝试，避免无限重试。"""
        try:
            with session_scope() as session:
                match = session.scalar(
                    select(Match).where(
                        Match.league_code == self.league_code,
                        Match.source_id == str(event_id),
                    )
                )
                if match is not None:
                    match.detail_fetched = True
        except Exception:  # noqa: BLE001
            pass

    def sync_pending_details(self, limit: Optional[int] = None,
                             delay: float = 0.15) -> int:
        """为已结束但详情未抓取的比赛补齐技术数据。

        limit=None 表示抓取全部 final 且未抓取的比赛（分批循环）。
        """
        done = 0
        batch_size = 200
        while True:
            with session_scope() as session:
                q = (
                    select(Match)
                    .where(Match.league_code == self.league_code)
                    .where(Match.status == "final")
                    .where(Match.detail_fetched.is_(False))
                    .limit(batch_size)
                )
                ids = [m.source_id for m in session.scalars(q).all()]
            if not ids:
                break
            for eid in ids:
                try:
                    self.sync_match_detail(eid)
                    done += 1
                    if delay:
                        time.sleep(delay)
                except Exception:  # noqa: BLE001
                    logger.warning("详情抓取失败 event=%s", eid)
                    self._mark_detail_attempted(eid)
                if limit and done >= limit:
                    break
            if limit and done >= limit:
                break
            logger.info("[%s] 已抓取比赛技术数据 %d 场...", self.league_code, done)
        logger.info("[%s] 比赛技术数据抓取完成，共 %d 场", self.league_code, done)
        return done

    # ------------------------- 全量 -------------------------
    def sync_all(self, dates: Optional[str] = None,
                 seasons: Optional[list[str]] = None,
                 fetch_details: bool = True,
                 detail_limit: Optional[int] = None) -> dict:
        """完整同步。

        - seasons 给定时按赛季逐月抓取历史赛程（扩充样本）；否则用 dates/当前赛程。
        - fetch_details 控制是否抓取每场比赛的技术数据（boxscore/事件等）。
        - detail_limit 限制本次抓取详情的场次，None 表示抓全部未抓取的。
        """
        self.sync_league_and_teams()
        if seasons:
            n_schedule = self.sync_schedule_seasons(seasons)
        else:
            n_schedule = self.sync_schedule(dates)
        n_players = self.sync_players()
        n_injuries = self.sync_injuries()
        n_details = self.sync_pending_details(detail_limit) if fetch_details else 0
        return {
            "schedule": n_schedule,
            "players": n_players,
            "injuries": n_injuries,
            "details": n_details,
        }


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (ValueError, TypeError):
        return None


def _txt(value: Any) -> Optional[str]:
    """宽容地取 ESPN 嵌套字段的文本：dict 取 type/description/name，否则转字符串。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return (
            value.get("type")
            or value.get("description")
            or value.get("name")
            or value.get("abbreviation")
        )
    return str(value)


def _linescores(competitor: dict) -> Optional[str]:
    ls = competitor.get("linescores")
    if not ls:
        return None
    return json.dumps([_to_int(x.get("value")) for x in ls], ensure_ascii=False)
