"""预测：对即将进行的比赛进行预测并写入数据库。"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from sqlalchemy import select

from ball.config import get
from ball.db.engine import session_scope
from ball.db.models import Match, Prediction
from ball.dl.features import compute_team_stats, feature_vector
from ball.dl.model import MatchPredictor

logger = logging.getLogger(__name__)
MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models"


def _load(league_code: str):
    model_path = MODELS_DIR / f"{league_code}.pt"
    meta_path = MODELS_DIR / f"{league_code}_meta.pkl"
    if not model_path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"未找到 {league_code} 的模型，请先运行训练。")
    with open(meta_path, "rb") as f:
        meta = pickle.load(f)
    model = MatchPredictor(
        input_dim=meta["input_dim"],
        hidden_dim=meta["hidden_dim"],
        dropout=meta["dropout"],
        num_classes=meta["num_classes"],
    )
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()
    return model, meta


def predict_upcoming(league_code: str, sport: str | None = None,
                     lookahead_days: int | None = None) -> list[dict]:
    model, meta = _load(league_code)
    sport = sport or meta.get("sport", "football")
    scaler: "StandardScaler" = meta["scaler"]
    label_map = meta["label_map"]
    num_classes = meta["num_classes"]

    lookahead = lookahead_days or int(get("pipeline.lookahead_days", 3))
    horizon = datetime.utcnow().timestamp() + lookahead * 86400

    all_s, home_s, away_s = compute_team_stats(league_code, sport)

    with session_scope() as s:
        matches = s.execute(
            select(Match)
            .where(Match.league_code == league_code)
            .where(Match.status == "scheduled")
            .order_by(Match.start_time)
        ).scalars().all()
        # 转成可脱离 session 的纯数据
        rows = [
            (m.id, m.home_team_id, m.away_team_id,
             m.start_time, m.home_score, m.away_score)
            for m in matches
            if m.start_time is not None
            and m.start_time.timestamp() <= horizon
        ]

    results = []
    with session_scope() as s:
        for mid, ht, at, st, hs, ast in rows:
            vec = np.array(
                [feature_vector(ht, at, all_s, home_s, away_s, sport)],
                dtype=np.float32,
            )
            x = torch.tensor(scaler.transform(vec), dtype=torch.float32)
            probs = model.predict_proba(x).numpy()[0]
            pred_idx = int(np.argmax(probs))
            label = label_map.get(pred_idx, "unknown")

            # 覆盖写入
            existing = s.scalar(
                select(Prediction).where(Prediction.match_id == mid)
            )
            if existing is None:
                existing = Prediction(match_id=mid, model_name=f"mlp_{league_code}")
                s.add(existing)
            existing.model_name = f"mlp_{league_code}"
            existing.sport = sport
            if num_classes == 3:
                existing.prob_home = float(probs[0])
                existing.prob_draw = float(probs[1])
                existing.prob_away = float(probs[2])
            else:
                existing.prob_home = float(probs[0])
                existing.prob_draw = 0.0
                existing.prob_away = float(probs[1])
            existing.predicted_label = label
            existing.confidence = float(probs[pred_idx])
            existing.created_at = datetime.utcnow()

            results.append({
                "match_id": mid, "start_time": st, "label": label,
                "prob_home": existing.prob_home,
                "prob_draw": existing.prob_draw,
                "prob_away": existing.prob_away,
                "confidence": existing.confidence,
            })
    logger.info("[%s] 预测 %d 场即将进行的比赛", league_code, len(results))
    return results
