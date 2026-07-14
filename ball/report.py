"""把预测结果整理为中文报告（纯文本，供邮件纯文本兜底使用）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def build_report(league_name: str, predictions: list[dict],
                 title: str | None = None) -> str:
    """将预测列表格式化为可读的中文报告。"""
    lines: list[str] = []
    lines.append(title or f"【{league_name} 赛事预测】")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"待预测场次：{len(predictions)}")
    lines.append("=" * 28)
    if not predictions:
        lines.append("（近期暂无赛程）")
        return "\n".join(lines)

    label_cn = {"home": "主胜", "draw": "平局", "away": "客胜"}
    for i, p in enumerate(predictions, 1):
        st = p.get("start_time")
        time_str = st.strftime("%m-%d %H:%M") if isinstance(st, datetime) else str(st)
        label = label_cn.get(p.get("label"), p.get("label", ""))
        line = (
            f"{i}. {time_str}\n"
            f"   预测：{label}  "
            f"(主胜 {p['prob_home']:.0%} / 平 {p['prob_draw']:.0%} / 客胜 {p['prob_away']:.0%})\n"
            f"   置信度：{p['confidence']:.0%}"
        )
        lines.append(line)
    lines.append("=" * 28)
    lines.append("以上由 Ball 预测系统自动生成，仅供参考。")
    return "\n".join(lines)


def build_multi_text(predictions_by_league: dict[str, list[dict]],
                    league_names: dict[str, str] | None = None) -> str:
    """把多个联赛的预测汇总成纯文本（供统一邮件纯文本兜底）。"""
    league_names = league_names or {}
    lines: list[str] = []
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    label_cn = {"home": "主胜", "draw": "平局", "away": "客胜"}
    for code, preds in predictions_by_league.items():
        name = league_names.get(code, code)
        lines.append("")
        lines.append(f"【{name}】（{len(preds)} 场）")
        lines.append("-" * 28)
        if not preds:
            lines.append("（近期暂无赛程）")
            continue
        for i, p in enumerate(preds, 1):
            st = p.get("start_time")
            time_str = st.strftime("%m-%d %H:%M") if isinstance(st, datetime) else str(st)
            label = label_cn.get(p.get("label"), p.get("label", ""))
            lines.append(
                f"{i}. {time_str}\n"
                f"   预测：{label}  "
                f"(主胜 {p['prob_home']:.0%} / 平 {p['prob_draw']:.0%} / 客胜 {p['prob_away']:.0%})\n"
                f"   置信度：{p['confidence']:.0%}"
            )
    lines.append("")
    lines.append("=" * 28)
    lines.append("以上由 Ball 预测系统自动生成，仅供参考。")
    return "\n".join(lines)
