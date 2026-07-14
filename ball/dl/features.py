"""特征工程：从历史赛果构建球队状态特征与训练样本。

设计要点（避免数据泄露）：
- 按时间顺序遍历已结束的比赛；
- 每条样本的特征只来自「该场比赛之前」的累积统计；
- 更新统计在特征提取之后进行。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import numpy as np
from sqlalchemy import select

from ball.db.engine import session_scope
from ball.db.models import Match

# 特征分组：overall / home / away 各 7 维 + 主场指示 1 维 = 29 维
FEATURE_DIM = 7 * 4 + 1

LABEL_MAP = {
    "football": {0: "home", 1: "draw", 2: "away"},
    "basketball": {0: "home", 1: "away"},
}


def _new_stat():
    # [wins, draws, losses, goals_for, goals_against, played]
    return [0, 0, 0, 0, 0, 0]


def _update(stat, gf: int, ga: int) -> None:
    if gf > ga:
        stat[0] += 1
    elif gf == ga:
        stat[1] += 1
    else:
        stat[2] += 1
    stat[3] += gf
    stat[4] += ga
    stat[5] += 1


def _group_vec(stat: list, sport: str) -> list[float]:
    w, d, l, gf, ga, played = stat
    winrate = w / played if played else 0.0
    ppg = ((3 * w + (1 if sport == "football" else 0) * d) / played) if played else 0.0
    return [float(w), float(d), float(l), float(gf), float(ga), winrate, ppg]


def feature_vector(home_id, away_id, all_s, home_s, away_s, sport: str) -> list[float]:
    vec = (
        _group_vec(all_s[home_id], sport)
        + _group_vec(home_s[home_id], sport)
        + _group_vec(all_s[away_id], sport)
        + _group_vec(away_s[away_id], sport)
        + [1.0]  # 主场优势指示
    )
    return vec


def compute_team_stats(league_code: str, sport: str = "football"):
    """基于全部已结束比赛，计算球队累积统计（用于预测未知比赛）。"""
    with session_scope() as s:
        rows = s.execute(
            select(
                Match.home_team_id, Match.away_team_id,
                Match.home_score, Match.away_score, Match.start_time,
            )
            .where(Match.league_code == league_code)
            .where(Match.status == "final")
            .where(Match.home_score.is_not(None))
            .where(Match.away_score.is_not(None))
            .order_by(Match.start_time)
        ).all()

    all_s = defaultdict(_new_stat)
    home_s = defaultdict(_new_stat)
    away_s = defaultdict(_new_stat)
    for ht, at, hs, ast, _ in rows:
        hs, ast = int(hs), int(ast)
        _update(all_s[ht], hs, ast)
        _update(all_s[at], ast, hs)
        _update(home_s[ht], hs, ast)
        _update(away_s[at], ast, hs)
    return all_s, home_s, away_s


def build_dataset(league_code: str, sport: str = "football",
                  min_prior: int = 3, test_size: float = 0.2):
    """构建训练样本。返回 (X, y, meta) 与切分索引。"""
    with session_scope() as s:
        rows = s.execute(
            select(
                Match.id, Match.home_team_id, Match.away_team_id,
                Match.home_score, Match.away_score, Match.start_time,
            )
            .where(Match.league_code == league_code)
            .where(Match.status == "final")
            .where(Match.home_score.is_not(None))
            .where(Match.away_score.is_not(None))
            .order_by(Match.start_time)
        ).all()

    all_s = defaultdict(_new_stat)
    home_s = defaultdict(_new_stat)
    away_s = defaultdict(_new_stat)

    X, y, meta = [], [], []
    for mid, ht, at, hs, ast, _ in rows:
        hs, ast = int(hs), int(ast)
        if all_s[ht][5] >= min_prior and all_s[at][5] >= min_prior:
            X.append(feature_vector(ht, at, all_s, home_s, away_s, sport))
            if sport == "basketball":
                y.append(0 if hs > ast else 1)
            else:
                y.append(0 if hs > ast else (1 if hs == ast else 2))
            meta.append(mid)
        # 更新发生在特征提取之后（防泄露）
        _update(all_s[ht], hs, ast)
        _update(all_s[at], ast, hs)
        _update(home_s[ht], hs, ast)
        _update(away_s[at], ast, hs)

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int64)
    split = int(len(X) * (1 - test_size))
    return {
        "X": X, "y": y, "meta": meta,
        "split": split,
        "feature_dim": FEATURE_DIM,
        "num_classes": 2 if sport == "basketball" else 3,
        "label_map": LABEL_MAP[sport],
    }
