"""NBA 爬虫（基于 ESPN basketball）。"""
from __future__ import annotations

from ball.crawler.espn_client import Crawler


class NBACrawler(Crawler):
    sport = "basketball"

    def __init__(self, league_code: str = "nba", season: str | None = None):
        super().__init__(league_code, season)
