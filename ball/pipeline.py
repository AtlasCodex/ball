"""流程编排：建库 -> 爬取 -> 训练 -> 预测 -> 推送。"""
from __future__ import annotations

import logging

from ball.config import get
from ball.crawler.football import FootballCrawler
from ball.crawler.nba import NBACrawler
from ball.db.models import create_all

logger = logging.getLogger(__name__)


def make_crawler(sport: str, league_code: str, season: str | None = None):
    if sport == "nba":
        return NBACrawler(league_code, season)
    return FootballCrawler(league_code, season)


def init_db() -> None:
    create_all()
    logger.info("数据库表已初始化。")


def crawl(league_code: str, sport: str, season: str | None = None,
          dates: str | None = None, seasons: list[str] | None = None,
          fetch_details: bool = True, detail_limit: int | None = None) -> dict:
    crawler = make_crawler(sport, league_code, season)
    return {league_code: crawler.sync_all(
        dates, seasons=seasons, fetch_details=fetch_details,
        detail_limit=detail_limit)}


def train(league_code: str, sport: str) -> dict:
    from ball.dl.train import train_model

    return train_model(league_code, sport=sport)


def predict(league_code: str, sport: str, lookahead_days: int | None = None) -> list[dict]:
    from ball.dl.predict import predict_upcoming

    return predict_upcoming(league_code, sport=sport, lookahead_days=lookahead_days)


def notify(league_code: str, league_name: str, sport: str) -> dict:
    from ball.dl.predict import predict_upcoming
    from ball.notifier import Notifier
    from ball.report import build_report

    lookahead = get("pipeline.lookahead_days", None)
    predictions = predict_upcoming(league_code, sport=sport, lookahead_days=lookahead)
    report = build_report(league_name, predictions)
    return Notifier().send(f"{league_name} 赛事预测", report,
                            predictions=predictions, league_name=league_name)


def full(league_code: str, sport: str, season: str | None = None,
         dates: str | None = None, do_train: bool = False,
         do_notify: bool = False, seasons: list[str] | None = None,
         fetch_details: bool = True, detail_limit: int | None = None) -> dict:
    result: dict = {}
    result["crawl"] = crawl(league_code, sport, season, dates, seasons=seasons,
                            fetch_details=fetch_details, detail_limit=detail_limit)
    if do_train:
        result["train"] = train(league_code, sport)
    result["predict"] = predict(league_code, sport)
    if do_notify:
        name = league_name_for(league_code, sport)
        result["notify"] = notify(league_code, name, sport)
    return result


def league_name_for(code: str, sport: str) -> str:
    if sport == "nba":
        leagues = get("crawler.leagues.nba", []) or []
    else:
        leagues = get("crawler.leagues.football", []) or []
    for lg in leagues:
        if lg.get("code") == code:
            return lg.get("name", code)
    return code
