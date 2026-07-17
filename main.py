#!/usr/bin/env python
"""Ball 项目命令行入口。

示例：
  python main.py init
  python main.py crawl --sport football --league eng.1 --season 2024
  python main.py train --sport football --league eng.1
  python main.py train --sport football          # 批量：所有足球联赛各自独立训练
  python main.py predict --sport football --league eng.1
  python main.py notify --sport football --league eng.1 --name "英超"
  python main.py run --sport football --league eng.1 --season 2024 --train --notify
  python main.py run-all --seasons 2023,2024 --train --notify
    # 全联赛：各自训练 -> 各自按赛程预测 -> 汇总成【一封】邮件统一推送
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from ball import pipeline
from ball.config import get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ball")


def _parse_seasons(value: str | None) -> list[str] | None:
    """将 '2022,2023' 解析为列表；未传时回退 config 的 crawler.seasons。"""
    if value:
        return [s.strip() for s in value.split(",") if s.strip()]
    cfg_seasons = get("crawler.seasons", None)
    if cfg_seasons:
        return [str(s) for s in cfg_seasons]
    return None


def _league_from_config(sport: str, league: str | None):
    if league:
        return league
    leagues = get(f"crawler.leagues.{ 'nba' if sport == 'nba' else 'football' }", []) or []
    if not leagues:
        raise SystemExit("未配置联赛，请用 --league 指定。")
    return leagues[0]["code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ball", description="足球/NBA 数据采集与预测")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化数据库表")

    p_crawl = sub.add_parser("crawl", help="爬取某联赛数据")
    p_crawl.add_argument("--sport", default="football", choices=["football", "nba"])
    p_crawl.add_argument("--league", default=None)
    p_crawl.add_argument("--season", default=None)
    p_crawl.add_argument("--dates", default=None, help="如 2024 / 20240101 / 20240101-20240131")
    p_crawl.add_argument("--seasons", default=None,
                         help="按赛季逐月抓历史赛程扩样本，逗号分隔，如 2022,2023,2024")
    p_crawl.add_argument("--no-details", action="store_true", help="跳过比赛技术数据抓取")
    p_crawl.add_argument("--detail-limit", type=int, default=None,
                         help="限制本次抓取的比赛技术数据场次")

    p_train = sub.add_parser(
        "train", help="训练模型（不指定 --league 则批量训练该 sport 所有联赛，各自独立）")
    p_train.add_argument("--sport", default="football", choices=["football", "nba"])
    p_train.add_argument("--league", default=None, help="指定联赛 code 则只训该联赛；省略则批量")

    p_pred = sub.add_parser("predict", help="预测即将进行的比赛")
    p_pred.add_argument("--sport", default="football", choices=["football", "nba"])
    p_pred.add_argument("--league", default=None)

    p_notify = sub.add_parser("notify", help="推送预测报告（邮箱）")
    p_notify.add_argument("--sport", default="football", choices=["football", "nba"])
    p_notify.add_argument("--league", default=None)
    p_notify.add_argument("--name", default=None, required=True)

    p_run = sub.add_parser("run", help="单次完整流程")
    p_run.add_argument("--sport", default="football", choices=["football", "nba"])
    p_run.add_argument("--league", default=None)
    p_run.add_argument("--season", default=None)
    p_run.add_argument("--dates", default=None)
    p_run.add_argument("--seasons", default=None, help="逗号分隔，如 2022,2023,2024")
    p_run.add_argument("--no-details", action="store_true")
    p_run.add_argument("--detail-limit", type=int, default=None)
    p_run.add_argument("--train", action="store_true")
    p_run.add_argument("--notify", action="store_true")

    p_all = sub.add_parser("run-all", help="对所有配置联赛跑完整流程")
    p_all.add_argument("--season", default=None)
    p_all.add_argument("--seasons", default=None, help="逗号分隔，如 2022,2023,2024")
    p_all.add_argument("--no-details", action="store_true")
    p_all.add_argument("--detail-limit", type=int, default=None)
    p_all.add_argument("--train", action="store_true")
    p_all.add_argument("--notify", action="store_true")

    p_sched = sub.add_parser("schedule", help="定时爬取（默认每 24 小时）")
    p_sched.add_argument("--every", type=int, default=24, help="爬取间隔（小时）")
    p_sched.add_argument("--season", default=None)

    p_st = sub.add_parser("sporttery", help="体彩竞猜：抓取赛程+匹配+预测+推送")
    p_st.add_argument("--notify", action="store_true", help="邮件推送预测报告")
    p_st.add_argument("--sync", action="store_true",
                      help="先抓取竞彩涉及联赛的 ESPN 近期赛程以便匹配")
    p_st.add_argument("--train-missing", action="store_true",
                      help="对缺少模型的竞彩联赛尝试训练（需该联赛已有≥50场历史）")

    p_web = sub.add_parser("web", help="启动 Web 可视化与操作控制台")
    p_web.add_argument("--host", default="127.0.0.1", help="监听地址")
    p_web.add_argument("--port", type=int, default=8000, help="监听端口")

    p_pstats = sub.add_parser(
        "players", help="把已抓取的 boxscore/leaders 解析为结构化球员与球队统计")
    p_pstats.add_argument("--sport", default="football", choices=["football", "nba"])
    p_pstats.add_argument("--league", default=None, help="指定联赛；省略则批量处理该 sport 全部配置联赛")
    p_pstats.add_argument("--limit", type=int, default=None, help="限制回填的原始详情条数")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        pipeline.init_db()
        return 0

    if args.cmd == "crawl":
        lg = _league_from_config(args.sport, args.league)
        seasons = _parse_seasons(args.seasons)
        print(json.dumps(pipeline.crawl(
            lg, args.sport, args.season, args.dates, seasons=seasons,
            fetch_details=not args.no_details, detail_limit=args.detail_limit),
            ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "train":
        # 指定 --league 训单个；否则批量训该 sport 下所有配置联赛（各自独立）。
        print(json.dumps(
            pipeline.train(args.league, args.sport), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "predict":
        lg = _league_from_config(args.sport, args.league)
        print(json.dumps(pipeline.predict(lg, args.sport), ensure_ascii=False, indent=2, default=str))
        return 0

    if args.cmd == "notify":
        lg = _league_from_config(args.sport, args.league)
        print(json.dumps(pipeline.notify(lg, args.name, args.sport), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "run":
        lg = _league_from_config(args.sport, args.league)
        seasons = _parse_seasons(args.seasons)
        print(json.dumps(
            pipeline.full(lg, args.sport, args.season, args.dates,
                         do_train=args.train, do_notify=args.notify,
                         seasons=seasons, fetch_details=not args.no_details,
                         detail_limit=args.detail_limit),
            ensure_ascii=False, indent=2, default=str))
        return 0

    if args.cmd == "run-all":
        seasons = _parse_seasons(args.seasons)
        results = {}
        preds_by_league: dict[str, list[dict]] = {}
        # 1) 逐个联赛：抓取 + 各自独立训练（不单独推送）
        # 2) 各联赛按自身赛程分别预测
        for sport, key in [("football", "football"), ("nba", "nba")]:
            for lg in get(f"crawler.leagues.{key}", []) or []:
                code = lg["code"]
                name = lg.get("name", code)
                logger.info("=== 处理 %s ===", name)
                r = pipeline.full(code, sport, args.season, None,
                                  do_train=args.train, do_notify=False,
                                  seasons=seasons, fetch_details=not args.no_details,
                                  detail_limit=args.detail_limit)
                results[code] = r
                preds_by_league[code] = r.get("predict") or []
        # 3) 统一推送：所有联赛的预测汇总成**一封**邮件
        if args.notify:
            results["__notify__"] = pipeline.notify_all(preds_by_league)
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.cmd == "schedule":
        import time

        import schedule

        season = args.season

        def job():
            logger.info("定时爬取开始")
            pipeline.init_db()
            from ball.crawler.scheduler import run_crawl
            run_crawl(season=season)
            logger.info("定时爬取结束")

        job()  # 立即执行一次
        schedule.every(args.every).hours.do(job)
        logger.info("调度已启动，每 %d 小时运行一次（Ctrl+C 退出）", args.every)
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("调度已停止。")
        return 0

    if args.cmd == "sporttery":
        print(json.dumps(
            pipeline.sporttery(notify=args.notify, sync=args.sync,
                            train_missing=args.train_missing),
            ensure_ascii=False, indent=2, default=str))
        return 0

    if args.cmd == "web":
        import uvicorn
        from ball.web import server
        logger.info("启动 Web 控制台：http://%s:%d", args.host, args.port)
        uvicorn.run(server.app, host=args.host, port=args.port,
                    log_level="info")
        return 0

    if args.cmd == "players":
        if args.league:
            out = pipeline.sync_player_stats(args.league, args.sport, limit=args.limit)
        else:
            out = {}
            for lg in get(f"crawler.leagues.{'nba' if args.sport == 'nba' else 'football'}", []) or []:
                code = lg["code"]
                out[code] = pipeline.sync_player_stats(
                    code, args.sport, limit=args.limit).get(code)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
