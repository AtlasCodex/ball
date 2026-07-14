"""深度学习子包（特征工程无 torch 依赖；训练/预测需要 torch）。"""
from ball.dl.features import build_dataset, compute_team_stats, feature_vector

__all__ = ["build_dataset", "compute_team_stats", "feature_vector"]
