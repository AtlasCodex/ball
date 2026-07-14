"""结果推送：通过邮箱（SMTP）发送预测报告。

邮件采用现代化响应式 HTML 模板。发送失败时回退到本地保存 + 控制台打印。
配置见 config.yaml 的 notify.email 段。
"""
from __future__ import annotations

from ball.notifier.notifier import Notifier, list_channels

__all__ = ["Notifier", "list_channels"]
