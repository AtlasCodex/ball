"""爬虫调度：遍历配置中的联赛，按计划同步数据。"""
from __future__ import annotations

import logging
from typing import Optional

from ball.config import get
from ball.crawler.football import FootballCrawler
from ball.crawler.nba import NBACrawler

logger = logging.getLogger(__name__)


def _make_crawler(sport: str, code: str, season: Optional[str]):
    if sport == "nba":
        return NBACrawler(code, season)
    return FootballCrawler(code, season)


def run_crawl(season: Optional[str] = None, dates: Optional[str] = None,
              seasons: Optional[list[str]] = None,
              fetch_details: bool = True) -> dict:
    """同步所有配置联赛的数据，返回汇总。"""
    summary: dict = {}
    football = get("crawler.leagues.football", []) or []
    nba = get("crawler.leagues.nba", []) or []
    if seasons is None:
        cfg_seasons = get("crawler.seasons", None)
        seasons = [str(s) for s in cfg_seasons] if cfg_seasons else None

    for lg in football:
        code = lg["code"]
        name = lg.get("name", code)
        logger.info(">>> 开始同步足球联赛: %s", name)
        crawler = _make_crawler("football", code, season)
        summary[code] = crawler.sync_all(dates, seasons=seasons,
                                         fetch_details=fetch_details)

    for lg in nba:
        code = lg["code"]
        name = lg.get("name", code)
        logger.info(">>> 开始同步篮球联赛: %s", name)
        crawler = _make_crawler("nba", code, season)
        summary[code] = crawler.sync_all(dates, seasons=seasons,
                                         fetch_details=fetch_details)

    return summary


def run_crawl_league(code: str, sport: str, season: Optional[str] = None,
                     dates: Optional[str] = None) -> dict:
    crawler = _make_crawler(sport, code, season)
    return {code: crawler.sync_all(dates)}
