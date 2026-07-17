"""流程编排：建库 -> 爬取 -> 训练 -> 预测 -> 推送。"""
from __future__ import annotations

import logging

from ball.config import get
from ball.crawler.football import FootballCrawler
from ball.crawler.nba import NBACrawler
from ball.db.models import create_all

logger = logging.getLogger(__name__)


def make_crawler(sport: str, league_code: str, season: str | None = None):
    # sport 词表兼容："nba"/"basketball" 走篮球；"football"/"soccer" 走足球。
    if sport in ("nba", "basketball"):
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


def train(league_code: str | None = None, sport: str = "football") -> dict:
    """训练模型。

    - 指定 ``league_code``：仅训练该联赛（使用其自身数据）。
    - 不指定：批量遍历 config 中该 sport 的全部联赛，**每个联赛各自用自己
      的历史数据独立训练**，互不混合；样本不足 50 场的联赛自动跳过并记录。
    """
    from ball.dl.train import train_model

    if league_code:
        return train_model(league_code, sport=sport)

    key = "nba" if sport in ("nba", "basketball") else "football"
    leagues = get(f"crawler.leagues.{key}", []) or []
    results: dict[str, object] = {}
    for lg in leagues:
        code = lg["code"]
        try:
            results[code] = train_model(code, sport=sport)
            logger.info("[train] 完成 %s（%s）", code, lg.get("name", code))
        except Exception as exc:  # noqa: BLE001
            results[code] = {"league_code": code, "error": str(exc)}
            logger.warning("[train] 跳过 %s：%s", code, exc)
    return results


def predict(league_code: str, sport: str, lookahead_days: int | None = None) -> list[dict]:
    from ball.dl.predict import predict_upcoming

    return predict_upcoming(league_code, sport=sport, lookahead_days=lookahead_days)


def sync_player_stats(league_code: str, sport: str,
                     limit: int | None = None) -> dict:
    """将已抓取但未结构化的 boxscore / leaders 详情解析为球员与球队统计表。

    - 篮球：boxscore 提供完整球员逐项数据（得分/篮板/助攻/抢断/盖帽/失误/犯规…）。
    - 足球：boxscore 仅提供球队级统计；球员个人数据来自 leaders 榜
      （射手/助攻/射门/传球等分类榜），按球员聚合。
    两运动的球队级统计都从 boxscore.teams 结构化落地。
    """
    crawler = make_crawler(sport, league_code)
    n = crawler.rebuild_player_stats(limit)
    return {league_code: n}


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


def _config_leagues(sport: str | None = None) -> list[tuple[str, str, str]]:
    """返回 [(sport, code, name), ...]，来自 config 中配置的联赛。"""
    out: list[tuple[str, str, str]] = []
    for sp, key in [("football", "football"), ("nba", "nba")]:
        if sport and sport != sp:
            continue
        for lg in get(f"crawler.leagues.{key}", []) or []:
            out.append((sp, lg["code"], lg.get("name", lg["code"])))
    return out


def predict_all(sport: str | None = None) -> dict[str, list[dict]]:
    """对所有配置联赛各自用自身已训练模型预测（按各联赛自身赛程）。

    返回 {league_code: predictions}；模型缺失或出错的联赛记空列表并跳过。
    """
    from ball.dl.predict import predict_upcoming

    out: dict[str, list[dict]] = {}
    for sp, code, _name in _config_leagues(sport):
        try:
            out[code] = predict_upcoming(code, sport=sp)
        except FileNotFoundError:
            out[code] = []
        except Exception as exc:  # noqa: BLE001
            logger.warning("[predict_all] %s 预测跳过：%s", code, exc)
            out[code] = []
    return out


def notify_all(predictions_by_league: dict[str, list[dict]],
               sport: str | None = None,
               title: str | None = None) -> dict:
    """把各联赛的预测汇总成**一封**邮件统一推送（而非逐联赛分开发送）。"""
    from ball.notifier import Notifier
    from ball.report import build_multi_text

    league_names = {code: name for _, code, name in _config_leagues(sport)}
    text = build_multi_text(predictions_by_league, league_names)
    notifier = Notifier()
    html = notifier.build_multi_html(
        title or "赛事预测汇总", predictions_by_league, league_names)
    return notifier.send_report(title or "赛事预测汇总", html, text)


# ------------------------- 体彩竞猜流程 -------------------------
def _sync_matched_leagues(matched: list[dict]) -> None:
    """对匹配到的竞彩联赛，抓取 ESPN 近期赛程，使对应场次进入 matches 表。"""
    from ball.sporttery.matcher import distinct_leagues

    for sport, code in distinct_leagues(matched):
        try:
            crawler = make_crawler(sport, code)
            crawler.sync_schedule()  # 默认抓当前窗口赛程
            logger.info("[体彩] 赛程已同步：%s", code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[体彩] 赛程同步失败 %s：%s", code, exc)


def _train_missing(by_league: dict) -> None:
    """对缺少模型的竞彩联赛尝试训练（样本不足会自动跳过）。"""
    from ball.dl.train import train_model

    for sport, code in by_league:
        try:
            train_model(code, sport)
            logger.info("[体彩] 模型已训练：%s", code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[体彩] 模型训练跳过 %s：%s", code, exc)


def sporttery(notify: bool = False, sync: bool = False,
              train_missing: bool = False) -> dict:
    """端到端：抓取竞彩赛程 -> 匹配本地赛程 -> 预测 -> 可选推送邮件。

    - sync：先抓取竞彩涉及联赛的 ESPN 近期赛程（便于匹配上 upcoming）。
    - train_missing：对缺模型的竞彩联赛尝试训练（需该联赛已有 ≥50 场历史）。
    - notify：通过邮箱发送体彩竞猜预测报告。
    """
    from ball.dl.predict import predict_for_match_ids
    from ball.notifier import Notifier
    from ball.sporttery.client import fetch
    from ball.sporttery.matcher import match_all
    from ball.sporttery.report import build_sporttery_report

    fb = fetch("football")
    bk = fetch("basketball")
    allm = fb + bk
    matched = match_all(allm)

    if sync:
        _sync_matched_leagues(matched)
        matched = match_all(allm)  # 抓取后重新匹配

    # 按联赛收集需预测的 match_id
    by_league: dict[tuple[str, str], list[int]] = {}
    for r in matched:
        if r.get("matched"):
            key = (r["sport"], r["league_code"])
            by_league.setdefault(key, []).append(r["match_id"])

    if train_missing:
        _train_missing(by_league)

    preds: dict[str, object] = {}
    for (sport, code), mids in by_league.items():
        try:
            preds[code] = predict_for_match_ids(code, sport, list(set(mids)))
        except FileNotFoundError:
            preds[code] = "NO_MODEL"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[体彩] 预测失败 %s：%s", code, exc)
            preds[code] = "ERR"

    html, text = build_sporttery_report(matched, preds)
    result = {
        "count": len(allm),
        "matched": sum(1 for r in matched if r.get("matched")),
        "report_html_len": len(html),
    }
    if notify:
        result["notify"] = Notifier().send_report("体彩竞猜预测", html, text)
    return result
