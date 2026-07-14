"""体彩竞猜预测报告：把「竞彩场次 + 匹配结果 + 模型预测」整理成
现代化 HTML 邮件 + 纯文本兜底。"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from html import escape

logger = logging.getLogger(__name__)

_LABEL_CN = {"home": "主胜", "draw": "平局", "away": "客胜"}
_OUT = [
    ("主胜", "prob_home", "#16a34a"),
    ("平局", "prob_draw", "#d97706"),
    ("客胜", "prob_away", "#2563eb"),
]


def _bj(dt) -> str:
    """UTC datetime → 北京时间字符串 MM-DD HH:MM。"""
    if dt is None:
        return "—"
    bj = dt + timedelta(hours=8)
    return bj.strftime("%m-%d %H:%M")


def _o(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "—"


def _odds_line_football(sm: dict) -> str:
    parts = []
    had = sm.get("had") or {}
    if had.get("h") is not None:
        parts.append(f"胜平负 {_o(had['h'])} / {_o(had['d'])} / {_o(had['a'])}")
    hhad = sm.get("hhad") or {}
    if hhad.get("h") is not None:
        line = hhad.get("line") or "0"
        parts.append(f"让球({line}) {_o(hhad['h'])} / {_o(hhad['d'])} / {_o(hhad['a'])}")
    return "　|　".join(parts) if parts else "（暂无固定奖金）"


def _odds_line_basketball(sm: dict) -> str:
    parts = []
    mnl = sm.get("mnl") or {}
    if mnl.get("h") is not None:
        parts.append(f"胜负 {_o(mnl['h'])} / {_o(mnl['a'])}")
    hdc = sm.get("hdc") or {}
    if hdc.get("h") is not None:
        parts.append(f"让分({hdc.get('line')}) {_o(hdc['h'])} / {_o(hdc['a'])}")
    hilo = sm.get("hilo") or {}
    if hilo.get("h") is not None:
        parts.append(f"大小分({hilo.get('line')}) {_o(hilo['h'])} / {_o(hilo['l'])}")
    return "　|　".join(parts) if parts else "（暂无固定奖金）"


def _pred_block(pred: dict) -> str:
    label_cn = _LABEL_CN.get(pred.get("label"), pred.get("label", ""))
    conf = pred.get("confidence", 0)
    conf_pct = f"{conf:.0%}" if isinstance(conf, float) else str(conf)
    bars = ""
    for name, key, color in _OUT:
        prob = pred.get(key, 0)
        pct = int(round(prob * 100)) if isinstance(prob, float) else int(prob)
        bars += (
            '<div style="display:flex;align-items:center;margin-top:8px;">'
            f'<div style="width:44px;font-size:12px;color:#6b7280;text-align:right;'
            f'padding-right:10px;">{name}</div>'
            '<div style="flex:1;background:#eef0f3;border-radius:6px;height:10px;'
            'overflow:hidden;">'
            f'<div style="width:{pct}%;height:10px;background:{color};'
            f'border-radius:6px;"></div></div>'
            f'<div style="width:48px;font-size:12px;color:#374151;text-align:left;'
            f'padding-left:10px;">{pct}%</div></div>'
        )
    badge_color = {"home": "#16a34a", "draw": "#d97706",
                   "away": "#2563eb"}.get(pred.get("label"), "#4f46e5")
    return (
        f'<div style="margin-top:12px;font-size:13px;font-weight:600;color:#111827;">'
        f'模型预测：<span style="color:{badge_color};">{escape(str(label_cn))}</span></div>'
        f'<div style="margin-top:10px;">{bars}</div>'
        '<div style="margin-top:10px;font-size:12px;color:#6b7280;">'
        f'置信度：<b style="color:#4f46e5;">{escape(str(conf_pct))}</b></div>'
    )


def _status_note(item: dict, preds: dict) -> str:
    if item.get("matched"):
        code = item["league_code"]
        mp = preds.get(code)
        mid = item.get("match_id")
        if isinstance(mp, dict) and mid in mp:
            return _pred_block(mp[mid])
        if mp == "NO_MODEL":
            return ('<div style="margin-top:12px;font-size:12px;color:#b45309;'
                    'background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;'
                    'padding:8px 10px;">该联赛暂无训练模型'
                    '（可加 --train-missing 自动训练）。</div>')
        if mp == "ERR":
            return ('<div style="margin-top:12px;font-size:12px;color:#b91c1c;'
                    'background:#fef2f2;border:1px solid #fecaca;border-radius:8px;'
                    'padding:8px 10px;">该联赛预测出错。</div>')
        return ('<div style="margin-top:12px;font-size:12px;color:#6b7280;">'
                '已匹配赛程，但暂无预测。</div>')
    reason = item.get("reason", "未匹配")
    return ('<div style="margin-top:12px;font-size:12px;color:#92400e;'
            'background:#fffbeb;border:1px solid #fde68a;border-radius:8px;'
            f'padding:8px 10px;">未匹配：{escape(str(reason))}</div>')


def build_sporttery_report(matched: list[dict],
                           preds: dict[str, Any]) -> tuple[str, str]:
    """返回 (html, text)。"""
    total = len(matched)
    n_matched = sum(1 for i in matched if i.get("matched"))
    n_pred = 0
    for i in matched:
        if i.get("matched"):
            mp = preds.get(i["league_code"])
            if isinstance(mp, dict) and i.get("match_id") in mp:
                n_pred += 1

    cards = ""
    text_lines = [f"体彩竞猜预测（共 {total} 场，匹配 {n_matched}，可预测 {n_pred}）",
                  "=" * 36]
    for item in matched:
        sm = item.get("match", {})
        sport = sm.get("sport", "football")
        sport_cn = "篮球" if sport == "basketball" else "足球"
        odds = (_odds_line_basketball(sm) if sport == "basketball"
                else _odds_line_football(sm))
        teams = f"{escape(str(sm.get('home_name','')))} vs {escape(str(sm.get('away_name','')))}"
        title = (f"{escape(str(sm.get('match_num','')))} "
                f"{escape(str(sm.get('league_name','')))} · {sport_cn}")
        time_str = _bj(sm.get("dt_utc"))

        text_lines.append("")
        text_lines.append(f"{sm.get('match_num','')} {sm.get('league_name','')} [{sport_cn}]")
        text_lines.append(f"  {sm.get('home_name','')} vs {sm.get('away_name','')}  {time_str} (北京)")
        text_lines.append(f"  固定奖金：{odds}")
        if item.get("matched"):
            mp = preds.get(item["league_code"])
            mid = item.get("match_id")
            if isinstance(mp, dict) and mid in mp:
                p = mp[mid]
                lc = _LABEL_CN.get(p.get("label"), p.get("label", ""))
                text_lines.append(
                    f"  模型预测：{lc} "
                    f"(主 {p['prob_home']:.0%}/平 {p['prob_draw']:.0%}/客 {p['prob_away']:.0%}) "
                    f"置信 {p['confidence']:.0%}")
            elif mp == "NO_MODEL":
                text_lines.append("  模型：该联赛暂无训练模型")
            elif mp == "ERR":
                text_lines.append("  模型：预测出错")
            else:
                text_lines.append("  模型：已匹配，暂无预测")
        else:
            text_lines.append(f"  未匹配：{item.get('reason','')}")

        cards += (
            '<table width="100%" cellpadding="0" cellspacing="0" '
            'style="background:#fafbff;border:1px solid #eceef4;border-radius:12px;'
            'margin-bottom:14px;overflow:hidden;"><tr><td style="padding:16px 18px;">'
            f'<div style="font-size:15px;font-weight:700;color:#111827;">{title}</div>'
            f'<div style="margin-top:6px;font-size:14px;color:#374151;">{teams}</div>'
            f'<div style="margin-top:4px;font-size:12px;color:#6b7280;">'
            f'开赛（北京）：{escape(str(time_str))}</div>'
            f'<div style="margin-top:8px;font-size:12px;color:#4b5563;">'
            f'固定奖金：{escape(str(odds))}</div>'
            f'{_status_note(item, preds)}'
            "</td></tr></table>"
        )

    text_lines.append("=" * 36)
    text_lines.append("以上由 Ball 预测系统自动生成，仅供参考，不构成任何投注建议。")
    text = "\n".join(text_lines)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>体彩竞猜预测</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,0.06);">
        <tr><td style="background:linear-gradient(135deg,#0ea5e9 0%,#6366f1 100%);padding:28px 32px;">
          <div style="font-size:12px;letter-spacing:2px;color:rgba(255,255,255,0.8);text-transform:uppercase;">Ball 预测系统 · 体彩竞猜</div>
          <div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:6px;">每周竞猜预测</div>
        </td></tr>
        <tr><td style="padding:20px 32px 0;">
          <div style="font-size:13px;color:#6b7280;">
            竞猜场次：<b style="color:#111827;">{total}</b>　|
            已匹配：<b style="color:#111827;">{n_matched}</b>　|
            可预测：<b style="color:#111827;">{n_pred}</b>
          </div>
        </td></tr>
        <tr><td style="padding:16px 32px 8px;">{cards}</td></tr>
        <tr><td style="padding:20px 32px 28px;border-top:1px solid #eef0f3;">
          <div style="font-size:12px;color:#9ca3af;line-height:1.6;">以上由 Ball 预测系统自动生成，仅供参考，不构成任何投注建议。</div>
        </td></tr>
      </table>
      <div style="font-size:11px;color:#c0c4cc;margin-top:12px;">本邮件由系统自动发送，请勿直接回复。</div>
    </td></tr>
  </table>
</body>
</html>"""
    return html, text
