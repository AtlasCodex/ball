"""深度学习模型：用于赛果分类的轻量 MLP。"""
from __future__ import annotations

import torch
import torch.nn as nn


class MatchPredictor(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 64,
                 dropout: float = 0.2, num_classes: int = 3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.BatchNorm1d(hidden_dim),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            logits = self.net(x)
            return torch.softmax(logits, dim=-1)
