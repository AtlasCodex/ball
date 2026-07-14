"""结果推送：通过邮箱（SMTP）发送预测报告。

仅使用邮箱通道。任意异常都会回退到「本地保存 + 打印」，保证结果不丢失。
邮件采用现代化、响应式的 HTML 模板（含赛事预测卡片与概率进度条）。
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from html import escape
from pathlib import Path
from typing import Any

from ball.config import get

logger = logging.getLogger(__name__)


def list_channels() -> list[str]:
    return ["email"]


_LABEL_CN = {"home": "主胜", "draw": "平局", "away": "客胜"}
_OUTCOMES = [
    ("主胜", "prob_home", "#16a34a"),
    ("平局", "prob_draw", "#d97706"),
    ("客胜", "prob_away", "#2563eb"),
]


class Notifier:
    def __init__(self, cfg: dict | None = None):
        self.cfg: dict = cfg if cfg is not None else (get("notify", {}) or {})
        self.channel = "email"
        self.enabled = bool(self.cfg.get("enabled", True))

    # ------------------------- 对外入口 -------------------------
    def send(self, title: str, content: str, *,
             predictions: list[dict] | None = None,
             league_name: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            logger.info("[NOTIFY] 推送已禁用，仅本地输出。")
            return self._save_local(title, content)
        try:
            return self._send_email(title, content, predictions, league_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[NOTIFY] 邮件发送失败：%s，回退本地文件。", exc)
            return self._save_local(title, content, error=str(exc))

    # ------------------------- 通用报告入口 -------------------------
    def send_report(self, subject_suffix: str, html: str,
                   text: str | None = None) -> dict[str, Any]:
        """发送任意 HTML 报告（如体彩竞猜预测）。"""
        if not self.enabled:
            logger.info("[NOTIFY] 推送已禁用，仅本地输出。")
            return self._save_local(subject_suffix, text or self._strip_html(html))
        try:
            return self._send_report_email(subject_suffix, html, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[NOTIFY] 邮件发送失败：%s，回退本地文件。", exc)
            return self._save_local(subject_suffix, text or self._strip_html(html), error=str(exc))

    def _send_report_email(self, subject_suffix: str, html: str,
                           text: str | None) -> dict[str, Any]:
        ec = self.cfg.get("email", {}) or {}
        host = ec.get("smtp_host") or "smtp.163.com"
        port = int(ec.get("smtp_port") or 465)
        username = ec.get("username") or ""
        password = ec.get("password") or ""
        if not username or not password:
            raise ValueError("email 模式需要 notify.email.username / password")
        sender = ec.get("sender") or username
        sender_name = ec.get("sender_name") or "Ball 预测系统"
        recipients = ec.get("recipients") or [username]
        prefix = ec.get("subject_prefix") or "Ball 预测"

        subject = f"{prefix} · {subject_suffix}"
        plain = text or self._strip_html(html)

        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr((sender_name, sender))
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        use_ssl = bool(ec.get("use_ssl", True))
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        try:
            server.login(username, password)
            server.sendmail(sender, recipients, msg.as_string())
        finally:
            server.quit()

        logger.info("[NOTIFY] 邮件推送成功：%s -> %s", subject, ", ".join(recipients))
        return {"channel": "email", "ok": True, "subject": subject,
                "recipients": recipients}

    def _strip_html(html: str) -> str:
        import re

        return re.sub(r"<[^>]+>", "", html or "").strip()

    # ------------------------- 邮件发送 -------------------------
    def _send_email(self, title: str, content: str,
                    predictions: list[dict] | None,
                    league_name: str | None) -> dict[str, Any]:
        ec = self.cfg.get("email", {}) or {}
        host = ec.get("smtp_host") or "smtp.163.com"
        port = int(ec.get("smtp_port") or 465)
        username = ec.get("username") or ""
        password = ec.get("password") or ""
        if not username or not password:
            raise ValueError("email 模式需要 notify.email.username / password")
        sender = ec.get("sender") or username
        sender_name = ec.get("sender_name") or "Ball 预测系统"
        recipients = ec.get("recipients") or [username]
        prefix = ec.get("subject_prefix") or "Ball 预测"

        subject = f"{prefix} · {title}"
        html = self._build_html(title, content, predictions, league_name)
        plain = content or title

        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr((sender_name, sender))
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        use_ssl = bool(ec.get("use_ssl", True))
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        try:
            server.login(username, password)
            server.sendmail(sender, recipients, msg.as_string())
        finally:
            server.quit()

        logger.info("[NOTIFY] 邮件推送成功：%s -> %s", subject, ", ".join(recipients))
        return {"channel": "email", "ok": True, "subject": subject,
                "recipients": recipients}

    # ------------------------- 现代化 HTML 模板 -------------------------
    def _build_html(self, title: str, content: str,
                    predictions: list[dict] | None,
                    league_name: str | None) -> str:
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        if predictions:
            count = len(predictions)
            cards = "".join(self._prediction_card(p) for p in predictions)
        else:
            count = 0
            cards = (
                '<div style="padding:18px;background:#fafbff;border:1px solid '
                '#eceef4;border-radius:12px;color:#6b7280;font-size:14px;">'
                "（近期暂无赛程）</div>"
            )

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,0.06);">
        <tr><td style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);padding:28px 32px;">
          <div style="font-size:12px;letter-spacing:2px;color:rgba(255,255,255,0.8);text-transform:uppercase;">Ball 预测系统</div>
          <div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:6px;">{escape(title)}</div>
        </td></tr>
        <tr><td style="padding:20px 32px 0;">
          <div style="font-size:13px;color:#6b7280;">生成时间：{generated}</div>
          <div style="font-size:13px;color:#6b7280;margin-top:4px;">待预测场次：<b style="color:#111827;">{count}</b></div>
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

    def _prediction_card(self, p: dict) -> str:
        label = p.get("label", "")
        label_cn = _LABEL_CN.get(label, label)
        badge_color = {"home": "#16a34a", "draw": "#d97706",
                       "away": "#2563eb"}.get(label, "#4f46e5")

        st = p.get("start_time")
        time_str = (st.strftime("%m-%d %H:%M")
                    if isinstance(st, datetime) else str(st))
        conf = p.get("confidence", 0)
        conf_pct = f"{conf:.0%}" if isinstance(conf, float) else str(conf)

        bars = ""
        for name, key, color in _OUTCOMES:
            prob = p.get(key, 0)
            pct = int(round(prob * 100)) if isinstance(prob, float) else int(prob)
            bars += (
                '<div style="display:flex;align-items:center;margin-top:8px;">'
                f'<div style="width:44px;font-size:12px;color:#6b7280;text-align:right;'
                f'padding-right:10px;">{name}</div>'
                '<div style="flex:1;background:#eef0f3;border-radius:6px;height:10px;'
                'overflow:hidden;">'
                f'<div style="width:{pct}%;height:10px;background:{color};'
                f'border-radius:6px;"></div></div>'
                f'<div style="width:44px;font-size:12px;color:#374151;text-align:left;'
                f'padding-left:10px;">{pct}%</div></div>'
            )

        return (
            '<table width="100%" cellpadding="0" cellspacing="0" '
            'style="background:#fafbff;border:1px solid #eceef4;border-radius:12px;'
            'margin-bottom:14px;overflow:hidden;"><tr><td style="padding:16px 18px;">'
            '<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div style="font-size:14px;font-weight:600;color:#111827;">{escape(str(time_str))}</div>'
            f'<div style="font-size:12px;font-weight:600;color:#ffffff;background:{badge_color};'
            f'padding:4px 12px;border-radius:999px;">预测：{escape(str(label_cn))}</div>'
            "</div>"
            f'<div style="margin-top:14px;">{bars}</div>'
            '<div style="margin-top:12px;font-size:12px;color:#6b7280;">'
            f'置信度：<b style="color:#4f46e5;">{escape(str(conf_pct))}</b></div>'
            "</td></tr></table>"
        )

    def build_multi_html(self, title: str,
                        predictions_by_league: dict[str, list[dict]],
                        league_names: dict[str, str] | None = None) -> str:
        """多联赛汇总 HTML：每个联赛一个分节（标题 + 该联赛预测卡片）。"""
        league_names = league_names or {}
        sections = ""
        total = 0
        for code, preds in predictions_by_league.items():
            name = league_names.get(code, code)
            total += len(preds)
            if preds:
                cards = "".join(self._prediction_card(p) for p in preds)
            else:
                cards = (
                    '<div style="padding:18px;background:#fafbff;border:1px solid '
                    '#eceef4;border-radius:12px;color:#6b7280;font-size:14px;">'
                    "（近期暂无赛程）</div>"
                )
            sections += (
                '<div style="margin-top:18px;font-size:16px;font-weight:700;'
                'color:#111827;padding-bottom:6px;border-bottom:2px solid #4f46e5;">'
                f'{escape(str(name))}'
                f'<span style="font-size:12px;font-weight:400;color:#9ca3af;'
                f'margin-left:8px;">{len(preds)} 场</span></div>'
                f'<div style="padding:12px 0 4px;">{cards}</div>'
            )

        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,0.06);">
        <tr><td style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);padding:28px 32px;">
          <div style="font-size:12px;letter-spacing:2px;color:rgba(255,255,255,0.8);text-transform:uppercase;">Ball 预测系统</div>
          <div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:6px;">{escape(title)}</div>
        </td></tr>
        <tr><td style="padding:20px 32px 0;">
          <div style="font-size:13px;color:#6b7280;">生成时间：{generated}</div>
          <div style="font-size:13px;color:#6b7280;margin-top:4px;">覆盖联赛：<b style="color:#111827;">{len(predictions_by_league)}</b> 个 · 待预测场次：<b style="color:#111827;">{total}</b></div>
        </td></tr>
        <tr><td style="padding:8px 32px 8px;">{sections}</td></tr>
        <tr><td style="padding:20px 32px 28px;border-top:1px solid #eef0f3;">
          <div style="font-size:12px;color:#9ca3af;line-height:1.6;">以上由 Ball 预测系统自动生成，仅供参考，不构成任何投注建议。</div>
        </td></tr>
      </table>
      <div style="font-size:11px;color:#c0c4cc;margin-top:12px;">本邮件由系统自动发送，请勿直接回复。</div>
    </td></tr>
  </table>
</body>
</html>"""

    # ------------------------- 本地兜底（保存 + 打印） -------------------------
    def _save_local(self, title: str, content: str,
                    error: str | None = None) -> dict[str, Any]:
        out_dir = Path(get("notify.file_dir", "data/reports"))
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fpath = out_dir / fname
        body = f"{title}\n{'=' * 28}\n{content}"
        if error:
            body += f"\n\n（注：邮件发送失败，原因：{error}）"
        fpath.write_text(body, encoding="utf-8")
        print("\n" + "=" * 40)
        print(body)
        print("=" * 40)
        print(f"[本地报告已保存] {fpath}")
        return {"channel": "file", "ok": True, "path": str(fpath)}
