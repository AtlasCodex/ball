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
from ball.db.models import (
    Injury,
    League,
    Match,
    MatchDetail,
    MatchTeamStat,
    Player,
    PlayerGameStat,
    Team,
)

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
        self._pmap: Optional[dict] = None  # (source_id -> player.id) 回填时一次性装载
        self._tmap: Optional[dict] = None  # (source_id -> team.id)

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
            # 结构化落地：球队技术统计 + 球员表现
            if "boxscore" in parts:
                self._ingest_boxscore(session, match, parts["boxscore"])
            if "leaders" in parts and self.sport != "basketball":
                self._ingest_leaders(session, match, parts["leaders"])
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

    # ------------------------- 球员 / 球队结构化统计 -------------------------
    def _lookup_player_id(self, session, source_player_id: Optional[str]):
        if not source_player_id or source_player_id == "None":
            return None
        if self._pmap is not None:
            return self._pmap.get((self.league_code, str(source_player_id)))
        row = session.scalar(
            select(Player.id).where(
                Player.league_code == self.league_code,
                Player.source_id == str(source_player_id),
            )
        )
        return row

    def _ensure_player_id(self, session, source_player_id, name,
                          league_code: Optional[str] = None, team_id=None,
                          position=None, jersey=None, headshot=None):
        """返回 Player.id；若 players 表中不存在，则创建一条（保证 player_id 关联完整）。

        补齐球员目录，使球员级统计的行都能正确外键关联到 players 主表，
        而非仅停留在 source_player_id 字符串层面。
        """
        if not source_player_id or source_player_id == "None":
            return None
        key = str(source_player_id)
        lc = league_code or self.league_code
        if self._pmap is not None:
            pid = self._pmap.get((lc, key))
            if pid is not None:
                return pid
        else:
            pid = session.scalar(
                select(Player.id).where(
                    Player.league_code == lc, Player.source_id == key))
            if pid is not None:
                return pid
        # 不存在 → 创建
        p = Player(
            source_id=key, league_code=lc, name=name or "",
            position=position, jersey=jersey, team_id=team_id,
            headshot_url=headshot, status="active",
        )
        session.add(p)
        session.flush()
        pid = p.id
        if self._pmap is not None:
            self._pmap[(lc, key)] = pid
        return pid

    def _lookup_team_id(self, session, source_team_id: Optional[str]):
        if not source_team_id or source_team_id == "None":
            return None
        if self._tmap is not None:
            return self._tmap.get(str(source_team_id))
        row = session.scalar(
            select(Team.id).where(
                Team.league_code == self.league_code,
                Team.source_id == str(source_team_id),
            )
        )
        return row

    def _upsert_team_stat(self, session, match, team_block: dict) -> None:
        """从 boxscore.teams[i] 解析球队级技术统计。两运动通用。"""
        team_obj = team_block.get("team") or {}
        src_team = str(team_obj.get("id")) if team_obj.get("id") else None
        if not src_team:
            return
        stats = _team_stat_pairs(team_block.get("statistics"))
        row = session.scalar(
            select(MatchTeamStat).where(
                MatchTeamStat.match_id == match.id,
                MatchTeamStat.source_team_id == src_team,
            )
        )
        if row is None:
            row = MatchTeamStat(
                match_id=match.id, source_team_id=src_team,
                league_code=self.league_code,
                sport="basketball" if self.sport == "basketball" else "football",
            )
            session.add(row)
        row.team_name = team_obj.get("displayName")
        row.is_home = (team_block.get("homeAway") == "home")
        row.stats_json = json.dumps(stats, ensure_ascii=False)
        t = self._lookup_team_id(session, src_team)
        row.team_id = t
        # 通用映射
        g = lambda k: stats.get(k)
        if self.sport == "basketball":
            row.points = _to_int(g("points"))
            row.total_rebounds = _to_int(g("totalRebounds"))
            row.offensive_rebounds = _to_int(g("offensiveRebounds"))
            row.defensive_rebounds = _to_int(g("defensiveRebounds"))
            row.assists = _to_int(g("assists"))
            row.steals = _to_int(g("steals"))
            row.blocks = _to_int(g("blocks"))
            row.turnovers = _to_int(g("turnovers"))
            row.fouls = _to_int(g("fouls"))
            row.plus_minus = _to_int(g("plusMinus"))
            row.field_goals_made = _to_int(g("fieldGoalsMade"))
            row.field_goals_attempted = _to_int(g("fieldGoalsAttempted"))
            row.three_made = _to_int(g("threePointFieldGoalsMade"))
            row.three_attempted = _to_int(g("threePointFieldGoalsAttempted"))
            row.free_throws_made = _to_int(g("freeThrowsMade"))
            row.free_throws_attempted = _to_int(g("freeThrowsAttempted"))
        else:
            row.possession_pct = g("possessionPct") or g("possession")
            row.total_shots = _to_int(g("totalShots"))
            row.shots_on_target = _to_int(g("shotsOnTarget"))
            row.fouls_committed = _to_int(g("foulsCommitted"))
            row.yellow_cards = _to_int(g("yellowCards"))
            row.red_cards = _to_int(g("redCards"))
            row.corners = _to_int(g("wonCorners")) or _to_int(g("corners"))
            row.offsides = _to_int(g("offsides"))
            row.passes = _to_int(g("totalPasses"))
            row.passes_completed = _to_int(g("accuratePasses"))
            row.pass_accuracy = g("passesAccuracy")
            row.tackles = _to_int(g("tackles")) or _to_int(g("totalTackles"))
            row.saves = _to_int(g("saves")) or _to_int(g("totalSaves"))

    def _upsert_player_bb(self, session, match, athlete: dict,
                           stats: dict, src_team: Optional[str]) -> None:
        """篮球：把 boxscore 中某个球员的一行写入 PlayerGameStat。"""
        aid = str(athlete.get("id")) if athlete.get("id") else None
        if not aid:
            return
        row = session.scalar(
            select(PlayerGameStat).where(
                PlayerGameStat.match_id == match.id,
                PlayerGameStat.source_player_id == aid,
                PlayerGameStat.stat_type == "boxscore",
            )
        )
        if row is None:
            row = PlayerGameStat(
                match_id=match.id, source_player_id=aid,
                league_code=self.league_code, sport="basketball",
                stat_type="boxscore",
            )
            session.add(row)
        row.source_team_id = src_team
        row.player_name = athlete.get("displayName")
        pos = (athlete.get("position") or {}).get("abbreviation")
        row.position = pos
        row.jersey = _to_int(athlete.get("jersey"))
        row.starter = bool(stats.get("starter", row.starter))
        row.did_not_play = bool(stats.get("didNotPlay", row.did_not_play))
        row.active = bool(stats.get("active", row.active))
        row.minutes = str(stats.get("minutes")) if stats.get("minutes") is not None else None
        row.points = _to_int(stats.get("points"))
        row.rebounds = _to_int(stats.get("rebounds"))
        row.offensive_rebounds = _to_int(stats.get("offensiveRebounds"))
        row.defensive_rebounds = _to_int(stats.get("defensiveRebounds"))
        row.assists = _to_int(stats.get("assists"))
        row.steals = _to_int(stats.get("steals"))
        row.blocks = _to_int(stats.get("blocks"))
        row.turnovers = _to_int(stats.get("turnovers"))
        row.fouls = _to_int(stats.get("fouls"))
        row.plus_minus = _to_int(stats.get("plusMinus"))
        row.field_goals_made = _to_int(stats.get("fieldGoalsMade"))
        row.field_goals_attempted = _to_int(stats.get("fieldGoalsAttempted"))
        row.three_made = _to_int(stats.get("threePointFieldGoalsMade"))
        row.three_attempted = _to_int(stats.get("threePointFieldGoalsAttempted"))
        row.free_throws_made = _to_int(stats.get("freeThrowsMade"))
        row.free_throws_attempted = _to_int(stats.get("freeThrowsAttempted"))
        row.raw_json = json.dumps(
            {"athlete": athlete, "stats": stats}, ensure_ascii=False)
        t = self._lookup_team_id(session, src_team)
        row.team_id = t
        p = self._ensure_player_id(
            session, aid, athlete.get("displayName"),
            team_id=t, position=pos, jersey=row.jersey,
            headshot=(athlete.get("headshot") or {}).get("href"),
        )
        if p:
            row.player_id = p

    def _upsert_player_fb(self, session, match, rec: dict) -> None:
        """足球：把从 leaders 聚合后的某球员表现写入 PlayerGameStat。"""
        aid = rec.get("source_player_id")
        if not aid:
            return
        row = session.scalar(
            select(PlayerGameStat).where(
                PlayerGameStat.match_id == match.id,
                PlayerGameStat.source_player_id == aid,
                PlayerGameStat.stat_type == "leaders",
            )
        )
        if row is None:
            row = PlayerGameStat(
                match_id=match.id, source_player_id=aid,
                league_code=self.league_code, sport="football",
                stat_type="leaders",
            )
            session.add(row)
        row.source_team_id = rec.get("source_team_id")
        row.player_name = rec.get("player_name")
        row.position = rec.get("position")
        row.jersey = rec.get("jersey")
        s = rec.get("stats") or {}
        row.goals = _to_int(s.get("totalGoals"))
        row.assists = _to_int(s.get("totalAssists"))
        row.shots = _to_int(s.get("totalShots"))
        row.shots_on_target = _to_int(s.get("shotsOnTarget"))
        row.passes = _to_int(s.get("totalPasses"))
        row.passes_completed = _to_int(s.get("accuratePasses"))
        row.pass_accuracy = s.get("passesAccuracy")
        row.tackles = _to_int(s.get("tackles")) or _to_int(s.get("totalTackles"))
        row.interceptions = _to_int(s.get("interceptions"))
        row.clearances = _to_int(s.get("clearances"))
        row.yellow_cards = _to_int(s.get("yellowCards"))
        row.red_cards = _to_int(s.get("redCards"))
        row.saves = _to_int(s.get("saves")) or _to_int(s.get("totalSaves"))
        row.fouls_committed = _to_int(s.get("foulsCommitted"))
        row.fouls_drawn = _to_int(s.get("foulsDrawn")) or _to_int(s.get("foulsSuffered"))
        row.offsides = _to_int(s.get("offsides"))
        row.rating = s.get("rating")
        row.raw_json = json.dumps(rec, ensure_ascii=False)
        t = self._lookup_team_id(session, row.source_team_id)
        row.team_id = t
        p = self._ensure_player_id(
            session, aid, rec.get("player_name"),
            team_id=t, position=rec.get("position"),
            jersey=rec.get("jersey"),
        )
        if p:
            row.player_id = p

    def _ingest_boxscore(self, session, match, box: dict) -> None:
        """解析 boxscore：球队级统计 + （篮球）球员逐项统计。"""
        for t in (box.get("teams") or []):
            if isinstance(t, dict):
                self._upsert_team_stat(session, match, t)
        # 球员逐项统计：仅篮球 boxscore 含 players 数组
        for entry in (box.get("players") or []):
            if not isinstance(entry, dict):
                continue
            src_team = str((entry.get("team") or {}).get("id"))
            for sb in (entry.get("statistics") or []):
                keys = (sb or {}).get("keys") or (sb or {}).get("names") or []
                for ath in (sb.get("athletes") or []):
                    if not isinstance(ath, dict):
                        continue
                    athlete = ath.get("athlete") or {}
                    d = _align_stats(keys, ath.get("stats") or [])
                    # 把 starter/didNotPlay/active 也并入，便于落地
                    d["starter"] = ath.get("starter")
                    d["didNotPlay"] = ath.get("didNotPlay")
                    d["active"] = ath.get("active")
                    self._upsert_player_bb(session, match, athlete, d, src_team)

    def _ingest_leaders(self, session, match, leaders: Any) -> None:
        """足球：leaders 提供各分类的球员榜（射手/助攻/射门…）。

        同一球员可能在多个分类上榜，这里按 athlete id 聚合，
        得到每名球员该场尽可能完整的表现快照（stat_type='leaders'）。
        """
        if not isinstance(leaders, list):
            return
        agg: dict[str, dict] = {}
        for tblock in leaders:
            if not isinstance(tblock, dict):
                continue
            src_team = str((tblock.get("team") or {}).get("id"))
            for cblock in (tblock.get("leaders") or []):
                if not isinstance(cblock, dict):
                    continue
                for ath_entry in (cblock.get("leaders") or []):
                    if not isinstance(ath_entry, dict):
                        continue
                    athlete = ath_entry.get("athlete") or {}
                    aid = str(athlete.get("id")) if athlete.get("id") else None
                    if not aid:
                        continue
                    rec = agg.setdefault(aid, {
                        "source_player_id": aid,
                        "source_team_id": src_team,
                        "player_name": athlete.get("displayName"),
                        "position": (athlete.get("position") or {}).get("abbreviation"),
                        "jersey": _to_int(athlete.get("jersey")),
                        "stats": {},
                    })
                    for st in (ath_entry.get("statistics") or []):
                        if isinstance(st, dict) and st.get("name"):
                            rec["stats"][st["name"]] = _num(
                                st.get("value", st.get("displayValue")))
        for rec in agg.values():
            self._upsert_player_fb(session, match, rec)

    def rebuild_player_stats(self, limit: Optional[int] = None) -> int:
        """回填：扫描已存库的 boxscore / leaders MatchDetail，结构化写入。

        优化：一次性装载本联赛的 (source_id -> id) 映射，避免逐行查询；
        eagerly 加载 match 消除 N+1。
        """
        from ball.db.models import create_all
        create_all()
        done = 0
        batch = 400
        # 预装载 id 映射（全局：ESPN 的 team/player id 在全球范围唯一，
        # 不限定 league_code，避免同一 id 落到别的联赛导致关联失败）。
        with session_scope() as s:
            self._pmap = {
                (lc, str(a)): i for i, lc, a in s.execute(
                    select(Player.id, Player.league_code, Player.source_id)).all()}
            self._tmap = {
                str(a): i for i, a in s.execute(
                    select(Team.id, Team.source_id)).all()}
        try:
            offset = 0
            while True:
                with session_scope() as s:
                    rows = s.execute(
                        select(MatchDetail, Match)
                        .join(Match, Match.id == MatchDetail.match_id)
                        .where(MatchDetail.kind.in_(["boxscore", "leaders"]))
                        .where(Match.league_code == self.league_code)
                        .order_by(MatchDetail.id)
                        .limit(batch).offset(offset)
                    ).all()
                    if not rows:
                        break
                    for d, match in rows:
                        if match is None:
                            continue
                        obj = json.loads(d.payload)
                        if d.kind == "boxscore":
                            self._ingest_boxscore(s, match, obj)
                        elif d.kind == "leaders" and self.sport != "basketball":
                            self._ingest_leaders(s, match, obj)
                    done += len(rows)
                offset += batch
                if limit and done >= limit:
                    break
                logger.info("[%s] 已回填球员/球队统计 %d 条…",
                            self.league_code, done)
        finally:
            self._pmap = None
            self._tmap = None
        logger.info("[%s] 球员/球队统计回填完成：%d 条原始详情",
                    self.league_code, done)
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


# ------------------------- 球员/球队统计解析辅助 -------------------------
def _num(value: Any) -> Optional[float]:
    """宽容地把 ESPN 统计值转成数字（字符串 / 数字 / 空）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s in ("", "-", "NULL", "None", "NaN"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _align_stats(keys: list, vals: list) -> dict:
    """把 boxscore 球员行的 keys 与 stats 对齐成字典。

    ESPN 篮球把 'fieldGoalsMade-fieldGoalsAttempted' 这种合并键的
    取值写成 '10-15'，这里按 '-' 拆成两个逻辑字段。
    """
    d: dict[str, Any] = {}
    if not isinstance(keys, list) or not isinstance(vals, list):
        return d
    for i, k in enumerate(keys):
        v = vals[i] if i < len(vals) else None
        if isinstance(k, str) and "-" in k:
            sub = k.split("-")
            if isinstance(v, str) and "-" in v:
                parts = v.split("-")
                if len(parts) == len(sub):
                    for j, sk in enumerate(sub):
                        d[sk] = _num(parts[j])
                    continue
            d[sub[0]] = _num(v)
        else:
            d[k] = _num(v)
    return d


def _team_stat_pairs(stats_list: Any) -> dict:
    """把 boxscore.teams[i].statistics 列表整理成 {name: value}。

    合并键（如 'fieldGoalsMade-fieldGoalsAttempted'）会拆成两个。
    """
    out: dict[str, Any] = {}
    for st in stats_list or []:
        if not isinstance(st, dict):
            continue
        name = st.get("name")
        if not name:
            continue
        val = st.get("value", st.get("displayValue"))
        if isinstance(name, str) and "-" in name and isinstance(val, str) and "-" in val:
            parts = val.split("-")
            subs = name.split("-")
            if len(parts) == len(subs) == 2:
                out[subs[0]] = _num(parts[0])
                out[subs[1]] = _num(parts[1])
                continue
        out[name] = _num(val)
    return out


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
