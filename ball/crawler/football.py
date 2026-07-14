"""足球顶级联赛爬虫（基于 ESPN soccer）。"""
from __future__ import annotations

from ball.crawler.espn_client import Crawler


class FootballCrawler(Crawler):
    sport = "soccer"

    def __init__(self, league_code: str, season: str | None = None):
        super().__init__(league_code, season)
