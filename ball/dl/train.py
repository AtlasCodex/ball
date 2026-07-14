"""模型训练：从历史赛果训练赛果分类器。"""
from __future__ import annotations

import logging
import pickle
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler

from ball.config import get
from ball.db.engine import session_scope
from ball.db.models import Prediction
from ball.dl.features import build_dataset
from ball.dl.model import MatchPredictor

logger = logging.getLogger(__name__)
MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models"


def train_model(league_code: str, sport: str = "football",
                epochs: int | None = None, batch_size: int | None = None,
                lr: float | None = None, hidden_dim: int | None = None,
                dropout: float | None = None, test_size: float | None = None,
                seed: int | None = None) -> dict:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    epochs = epochs or int(get("dl.epochs", 60))
    batch_size = batch_size or int(get("dl.batch_size", 64))
    lr = lr or float(get("dl.learning_rate", 0.001))
    hidden_dim = hidden_dim or int(get("dl.hidden_dim", 64))
    dropout = dropout or float(get("dl.dropout", 0.2))
    test_size = test_size if test_size is not None else float(get("dl.test_size", 0.2))
    seed = seed or int(get("dl.random_seed", 42))

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    data = build_dataset(league_code, sport, test_size=test_size)
    X, y = data["X"], data["y"]
    if len(X) < 50:
        raise ValueError(f"样本不足（{len(X)}），请先爬取更多历史比赛。")

    split = data["split"]
    Xtr, Xte = X[:split], X[split:]
    ytr, yte = y[:split], y[split:]

    scaler = StandardScaler().fit(Xtr)
    Xtr_t = torch.tensor(scaler.transform(Xtr), dtype=torch.float32)
    Xte_t = torch.tensor(scaler.transform(Xte), dtype=torch.float32)
    ytr_t = torch.tensor(ytr, dtype=torch.long)
    yte_t = torch.tensor(yte, dtype=torch.long)

    model = MatchPredictor(
        input_dim=data["feature_dim"],
        hidden_dim=hidden_dim,
        dropout=dropout,
        num_classes=data["num_classes"],
    )
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    n = len(Xtr_t)
    for epoch in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            xb, yb = Xtr_t[idx], ytr_t[idx]
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
        if (epoch + 1) % 10 == 0 or epoch == 0:
            logger.info("epoch %d/%d loss=%.4f", epoch + 1, epochs, loss.item())

    # 评估
    model.eval()
    with torch.no_grad():
        pred = model(Xte_t).argmax(dim=1)
        acc = (pred == yte_t).float().mean().item()

    # 保存
    model_path = MODELS_DIR / f"{league_code}.pt"
    meta_path = MODELS_DIR / f"{league_code}_meta.pkl"
    torch.save(model.state_dict(), model_path)
    with open(meta_path, "wb") as f:
        pickle.dump({
            "input_dim": data["feature_dim"],
            "hidden_dim": hidden_dim,
            "dropout": dropout,
            "num_classes": data["num_classes"],
            "sport": sport,
            "league_code": league_code,
            "label_map": data["label_map"],
            "scaler": scaler,
        }, f)

    logger.info("[%s] 训练完成 测试准确率=%.3f 样本=%d", league_code, acc, len(X))
    return {
        "league_code": league_code,
        "samples": len(X),
        "test_accuracy": round(acc, 4),
        "model_path": str(model_path),
    }
