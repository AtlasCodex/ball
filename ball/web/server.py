"""Ball Web 服务：数据可视化 + 训练/预测/抓取 操作控制台。

启动：python main.py web --host 127.0.0.1 --port 8000
前端为 ``web/`` 目录下的零构建静态页（Tailwind + GSAP + ECharts CDN）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from ball import pipeline
from ball.config import get
from ball.db.engine import session_scope
from ball.db.models import League, Match, Prediction, Team

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT / "web"
MODELS_DIR = ROOT / "data" / "models"

app = FastAPI(title="Ball 可视化中枢", version="1.0")


# ============================ 工具 ============================
def _iso(dt) -> Any:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def _match_row(m, hn, an, hl, al) -> dict:
    return {
        "id": m.id,
        "league_code": m.league_code,
        "start_time": _iso(m.start_time),
        "status": m.status,
        "season": m.season,
        "home": {"name": hn, "logo": hl},
        "away": {"name": an, "logo": al},
        "home_score": m.home_score,
        "away_score": m.away_score,
    }


# ============================ 数据 API ============================
@app.get("/api/overview")
def api_overview() -> dict:
    with session_scope() as s:
        n_leagues = s.scalar(select(func.count()).select_from(League)) or 0
        n_teams = s.scalar(select(func.count()).select_from(Team)) or 0
        n_matches = s.scalar(select(func.count()).select_from(Match)) or 0
        n_preds = s.scalar(select(func.count()).select_from(Prediction)) or 0

        # 各联赛比赛数（用于柱状图）
        league_rows = s.execute(
            select(League.code, League.name, League.sport,
                    func.count(Match.id))
            .outerjoin(Match, Match.league_code == League.code)
            .group_by(League.code, League.name, League.sport)
            .order_by(func.count(Match.id).desc())
            .limit(12)
        ).all()

        # 最近预测（含队名）
        Home = aliased(Team)
        Away = aliased(Team)
        recent = s.execute(
            select(Prediction, Match.league_code, Match.start_time,
                   Home.name, Away.name)
            .join(Match, Prediction.match_id == Match.id)
            .join(Home, Match.home_team_id == Home.id, isouter=True)
            .join(Away, Match.away_team_id == Away.id, isouter=True)
            .order_by(Prediction.created_at.desc())
            .limit(8)
        ).all()

        recent_list = [
            {
                "model_name": p.model_name,
                "league_code": lg,
                "start_time": _iso(st),
                "home": hn,
                "away": an,
                "label": p.predicted_label,
                "prob_home": p.prob_home,
                "prob_draw": p.prob_draw,
                "prob_away": p.prob_away,
                "confidence": p.confidence,
            }
            for p, lg, st, hn, an in recent
        ]

    def _exists(code: str) -> bool:
        return (MODELS_DIR / f"{code}.pt").exists() and \
               (MODELS_DIR / f"{code}_meta.pkl").exists()

    models = 0
    for sp, code, _ in pipeline._config_leagues():
        if _exists(code):
            models += 1

    return {
        "counts": {
            "leagues": n_leagues,
            "teams": n_teams,
            "matches": n_matches,
            "predictions": n_preds,
            "models": models,
        },
        "league_breakdown": [
            {"code": c, "name": n, "sport": sp, "matches": int(mc)}
            for c, n, sp, mc in league_rows
        ],
        "recent": recent_list,
    }


@app.get("/api/leagues")
def api_leagues() -> list[dict]:
    with session_scope() as s:
        Home = aliased(Team)
        rows = s.execute(
            select(League.code, League.name, League.sport, League.season,
                    func.count(func.distinct(Team.id)),
                    func.count(Match.id))
            .outerjoin(Team, Team.league_code == League.code)
            .outerjoin(Match, Match.league_code == League.code)
            .group_by(League.code, League.name, League.sport, League.season)
            .order_by(League.name)
        ).all()
    return [
        {"code": c, "name": n, "sport": sp, "season": season,
         "teams": int(tc), "matches": int(mc)}
        for c, n, sp, season, tc, mc in rows
    ]


@app.get("/api/teams")
def api_teams(league: str | None = None, search: str | None = None,
               limit: int = 200) -> list[dict]:
    with session_scope() as s:
        q = select(Team)
        if league:
            q = q.where(Team.league_code == league)
        if search:
            q = q.where(Team.name.ilike(f"%{search}%"))
        q = q.order_by(Team.points.desc()).limit(limit)
        teams = s.execute(q).scalars().all()
        return [
            {
                "id": t.id, "name": t.name, "short_name": t.short_name,
                "abbreviation": t.abbreviation, "logo_url": t.logo_url,
                "wins": t.wins, "losses": t.losses, "draws": t.draws,
                "points": t.points, "goals_for": t.goals_for,
                "goals_against": t.goals_against, "league_code": t.league_code,
            }
            for t in teams
        ]


@app.get("/api/matches")
def api_matches(league: str | None = None, status: str | None = None,
                search: str | None = None, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        Home = aliased(Team)
        Away = aliased(Team)
        q = (
            select(Match, Home.name, Away.name, Home.logo_url, Away.logo_url)
            .join(Home, Match.home_team_id == Home.id, isouter=True)
            .join(Away, Match.away_team_id == Away.id, isouter=True)
        )
        if league:
            q = q.where(Match.league_code == league)
        if status:
            q = q.where(Match.status == status)
        if search:
            q = q.where(Home.name.ilike(f"%{search}%") |
                         Away.name.ilike(f"%{search}%"))
        q = q.order_by(Match.start_time.desc()).limit(limit)
        rows = s.execute(q).all()
        return [_match_row(m, hn, an, hl, al) for m, hn, an, hl, al in rows]


@app.get("/api/models")
def api_models() -> list[dict]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for sp, code, name in pipeline._config_leagues():
        pt = MODELS_DIR / f"{code}.pt"
        meta_path = MODELS_DIR / f"{code}_meta.pkl"
        exists = pt.exists() and meta_path.exists()
        with session_scope() as s:
            samples = s.scalar(
                select(func.count()).select_from(Match)
                .where(Match.league_code == code)
                .where(Match.status == "final")
            ) or 0
        info = {"input_dim": None, "num_classes": None, "sport": sp,
                "trained_at": None}
        if exists:
            try:
                import pickle
                with open(meta_path, "rb") as f:
                    meta = pickle.load(f)
                info["input_dim"] = meta.get("input_dim")
                info["num_classes"] = meta.get("num_classes")
                info["sport"] = meta.get("sport", sp)
                info["trained_at"] = _iso(datetime.fromtimestamp(
                    pt.stat().st_mtime, tz=timezone.utc))
            except Exception:  # noqa: BLE001
                pass
        out.append({
            "code": code, "name": name, "sport": sp,
            "exists": exists, "samples": int(samples),
            "ready": samples >= 50, "meta": info,
        })
    return out


@app.get("/api/predictions")
def api_predictions(sport: str | None = None) -> dict:
    """返回数据库中已存储的预测，按联赛分组并附带队名。"""
    with session_scope() as s:
        Home = aliased(Team)
        Away = aliased(Team)
        rows = s.execute(
            select(Prediction, Match.league_code, Match.start_time,
                   Match.status, Home.name, Away.name)
            .join(Match, Prediction.match_id == Match.id)
            .join(Home, Match.home_team_id == Home.id, isouter=True)
            .join(Away, Match.away_team_id == Away.id, isouter=True)
            .order_by(Match.start_time.asc())
        ).all()
        by_league: dict[str, list[dict]] = {}
        names: dict[str, str] = {c: n for _, c, n in pipeline._config_leagues(sport)}
        for p, lg, st, status, hn, an in rows:
            if sport and (lg not in names):
                continue
            by_league.setdefault(lg, []).append({
                "match_id": p.match_id,
                "league_code": lg,
                "league_name": names.get(lg, lg),
                "start_time": _iso(st),
                "status": status,
                "home": hn, "away": an,
                "label": p.predicted_label,
                "prob_home": p.prob_home,
                "prob_draw": p.prob_draw,
                "prob_away": p.prob_away,
                "confidence": p.confidence,
            })
    return by_league


# ============================ 操作任务（后台 + SSE 日志） ============================
_tasks: dict[str, "Task"] = {}


class _TaskHandler(logging.Handler):
    def __init__(self, task: "Task"):
        super().__init__()
        self.task = task

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            msg = record.getMessage()
        with self.task.lock:
            self.task.logs.append(msg)


class Task:
    def __init__(self, tid: str):
        self.id = tid
        self.logs: list[str] = []
        self.status = "running"
        self.result: Any = None
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.handler = _TaskHandler(self)
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))


def _parse_seasons(value: str | None) -> list[str] | None:
    if value:
        return [x.strip() for x in value.split(",") if x.strip()]
    cfg = get("crawler.seasons", None)
    return [str(x) for x in cfg] if cfg else None


def _run_op(task: Task, action: str, params: dict) -> None:
    try:
        sport = params.get("sport", "football")
        if action == "init":
            pipeline.init_db()
            task.result = {"ok": True}
        elif action == "crawl":
            task.result = pipeline.crawl(
                params.get("league"), sport, params.get("season"),
                params.get("dates"), seasons=_parse_seasons(params.get("seasons")),
                fetch_details=not params.get("no_details", False),
                detail_limit=params.get("detail_limit"),
            )
        elif action == "train":
            task.result = pipeline.train(params.get("league"), sport)
        elif action == "predict":
            lg = params.get("league")
            if lg:
                task.result = pipeline.predict(lg, sport)
            else:
                task.result = pipeline.predict_all(sport)
        elif action == "run":
            task.result = pipeline.full(
                params.get("league"), sport, params.get("season"),
                params.get("dates"),
                do_train=params.get("train", False),
                do_notify=params.get("notify", False),
                seasons=_parse_seasons(params.get("seasons")),
                fetch_details=not params.get("no_details", False),
                detail_limit=params.get("detail_limit"),
            )
        elif action == "run-all":
            seasons = _parse_seasons(params.get("seasons"))
            preds_by_league: dict[str, list[dict]] = {}
            for sp, key in [("football", "football"), ("nba", "nba")]:
                for lg in get(f"crawler.leagues.{key}", []) or []:
                    code = lg["code"]
                    name = lg.get("name", code)
                    logger.info("=== 处理 %s ===", name)
                    r = pipeline.full(
                        code, sp, params.get("season"), None,
                        do_train=params.get("train", False),
                        do_notify=False,
                        seasons=seasons,
                        fetch_details=not params.get("no_details", False),
                        detail_limit=params.get("detail_limit"),
                    )
                    preds_by_league[code] = r.get("predict") or []
            if params.get("notify"):
                task.result = pipeline.notify_all(preds_by_league, sport=None)
            else:
                task.result = {"predictions": {k: len(v) for k, v in preds_by_league.items()}}
        elif action == "sporttery":
            task.result = pipeline.sporttery(
                notify=params.get("notify", False),
                sync=params.get("sync", False),
                train_missing=params.get("train_missing", False),
            )
        elif action == "notify":
            preds = pipeline.predict_all(sport)
            task.result = pipeline.notify_all(preds, sport=sport)
        else:
            raise ValueError(f"未知操作：{action}")
        task.status = "done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("操作失败")
        task.status = "error"
        task.result = {"error": str(exc)}
    finally:
        root = logging.getLogger()
        root.removeHandler(task.handler)
        task.done.set()


@app.post("/api/op")
async def start_op(payload: dict) -> dict:
    action = payload.get("action")
    if not action:
        raise HTTPException(400, "缺少 action")
    tid = uuid.uuid4().hex[:12]
    task = Task(tid)
    _tasks[tid] = task
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(task.handler)
    t = threading.Thread(
        target=_run_op, args=(task, action, payload.get("params", {}) or {}),
        daemon=True)
    t.start()
    return {"task_id": tid, "action": action}


@app.get("/api/op/{tid}")
def get_op(tid: str) -> dict:
    task = _tasks.get(tid)
    if not task:
        raise HTTPException(404, "任务不存在")
    with task.lock:
        return {"status": task.status, "logs": list(task.logs),
                "result": task.result}


@app.get("/api/op/{tid}/stream")
async def op_stream(tid: str):
    task = _tasks.get(tid)
    if not task:
        raise HTTPException(404, "任务不存在")

    async def gen():
        last = 0
        while True:
            with task.lock:
                new = task.logs[last:]
                last += len(new)
            for line in new:
                yield "data: " + json.dumps(
                    {"type": "log", "line": line}, ensure_ascii=False) + "\n\n"
            if task.done.is_set():
                with task.lock:
                    result, status = task.result, task.status
                yield "data: " + json.dumps(
                    {"type": "done", "status": status, "result": result},
                    ensure_ascii=False, default=str) + "\n\n"
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ============================ 静态前端 ============================
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "time": _iso(datetime.now(timezone.utc))}


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")
