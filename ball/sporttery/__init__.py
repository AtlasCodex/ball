"""体彩竞猜：抓取官方赛程 -> 匹配本地 ESPN 赛程 -> 预测 -> 邮件。"""
from ball.sporttery.client import SportteryMatch, fetch
from ball.sporttery.mapping import LEAGUE_MAP, TEAM_ALIASES, resolve_league, resolve_team
from ball.sporttery.matcher import distinct_leagues, match_all, match_one

__all__ = [
    "SportteryMatch", "fetch",
    "LEAGUE_MAP", "TEAM_ALIASES", "resolve_league", "resolve_team",
    "match_one", "match_all", "distinct_leagues",
]
