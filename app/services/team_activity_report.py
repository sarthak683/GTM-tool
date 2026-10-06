"""
Weekly Team Activity Report — Calls, Email, and LinkedIn numbers for every
active AE and SDR, sent every Monday morning. Same delivery pattern as
app.services.weekly_digest: config lives in
WorkspaceSettings.sync_schedule_settings under the "team_activity_report"
key, sent via the connected Report Sender Gmail account (not Resend — see
the pipeline stage-change alert incident this was modeled after), and the
HTML is fully inline-styled/table-based from the start so it renders
correctly in Gmail without needing a later rewrite.

Calls and Email numbers are synced automatically (Aircall, Gmail) and are
always complete. LinkedIn has no API integration — every row only exists
because a rep clicked "Log LinkedIn", so that column reflects logging
habits, not raw LinkedIn activity. See performance_metrics.linkedin_touches.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.gmail_sender import GMAIL_RECONNECT_REQUIRED_ERROR, send_gmail_email
from app.config import settings
from app.models.settings import WorkspaceSettings
from app.models.user import User
from app.services import performance_metrics as pm
from app.services.weekly_digest import is_production_environment

logger = logging.getLogger(__name__)

TEAM_ACTIVITY_REPORT_CONFIG_KEY = "team_activity_report"

TEAM_ACTIVITY_REPORT_ROLES = ("ae", "sdr")

DEFAULT_TEAM_ACTIVITY_REPORT_RECIPIENTS = [
    "maithili@beacon.li",
    "sarthak@beacon.li",
]

DEFAULT_TEAM_ACTIVITY_REPORT_SETTINGS = {
    "enabled": True,
    "recipients": DEFAULT_TEAM_ACTIVITY_REPORT_RECIPIENTS,
    "send_timezone": "Asia/Kolkata",
    "send_hour": 9,
    "send_minute": 30,
    "send_days": ["mon"],
    "nonprod_scheduled_enabled": False,
    "nonprod_recipients": ["sarthak@beacon.li"],
    "last_scheduled_send_key": None,
    "last_scheduled_send_at": None,
    "partial_send_key": None,
    "partial_sent_recipients": [],
}

DAY_KEYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
WEEKDAY_TO_KEY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _zone_name(value: object, fallback: str) -> str:
    name = str(value or fallback).strip() or fallback
    try:
        ZoneInfo(name)
        return name
    except Exception:
        return fallback


def normalize_team_activity_report_settings(value: dict | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    merged = {**DEFAULT_TEAM_ACTIVITY_REPORT_SETTINGS, **raw}

    def _emails(items: object, fallback: list[str]) -> list[str]:
        if isinstance(items, str):
            source = items.split(",")
        elif isinstance(items, list):
            source = items
        else:
            source = fallback
        cleaned = []
        for item in source:
            email = str(item or "").strip().lower()
            if email and "@" in email and email not in cleaned:
                cleaned.append(email)
        return cleaned or fallback

    send_days = [
        str(day or "").strip().lower()[:3]
        for day in (merged.get("send_days") if isinstance(merged.get("send_days"), list) else ["mon"])
    ]
    send_days = [day for day in send_days if day in DAY_KEYS] or ["mon"]

    return {
        "enabled": bool(merged.get("enabled")),
        "recipients": _emails(merged.get("recipients"), DEFAULT_TEAM_ACTIVITY_REPORT_RECIPIENTS),
        "send_timezone": _zone_name(merged.get("send_timezone"), "Asia/Kolkata"),
        "send_hour": max(0, min(23, int(merged.get("send_hour") or 0))),
        "send_minute": max(0, min(59, int(merged.get("send_minute") or 0))),
        "send_days": send_days,
        "nonprod_scheduled_enabled": bool(merged.get("nonprod_scheduled_enabled")),
        "nonprod_recipients": _emails(merged.get("nonprod_recipients"), ["sarthak@beacon.li"]),
        "last_scheduled_send_key": merged.get("last_scheduled_send_key"),
        "last_scheduled_send_at": merged.get("last_scheduled_send_at"),
        "partial_send_key": merged.get("partial_send_key"),
        "partial_sent_recipients": [
            str(r).strip().lower()
            for r in (merged.get("partial_sent_recipients") or [])
            if isinstance(r, str) and "@" in r
        ],
    }


async def load_team_activity_report_settings(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(WorkspaceSettings, 1)
    raw = None
    if row and isinstance(row.sync_schedule_settings, dict):
        raw = row.sync_schedule_settings.get(TEAM_ACTIVITY_REPORT_CONFIG_KEY)
    return normalize_team_activity_report_settings(raw if isinstance(raw, dict) else None)


def _resolve_report_recipients(
    recipients: list[str] | None,
    report_settings: dict[str, Any],
) -> tuple[list[str], list[str]]:
    requested = list(recipients) if recipients is not None else report_settings["recipients"]
    if is_production_environment():
        return requested, []

    nonprod = [
        email.strip().lower()
        for email in settings.SALES_REPORT_NONPROD_RECIPIENTS.split(",")
        if email.strip()
    ] or report_settings.get("nonprod_recipients") or ["sarthak@beacon.li"]
    allowed = set(nonprod)
    safe = [r for r in requested if r.lower() in allowed]
    blocked = [r for r in requested if r.lower() not in allowed]
    if recipients is None:
        return nonprod, []
    return safe, blocked


def team_activity_report_period(
    now: datetime | None = None, report_settings: dict[str, Any] | None = None
) -> tuple[date, date]:
    """Previous Mon-Sun window, in the report's send_timezone — same logic
    as weekly_digest_period, kept independent so the two reports' schedules
    can diverge without coupling."""
    tz = ZoneInfo((report_settings or DEFAULT_TEAM_ACTIVITY_REPORT_SETTINGS)["send_timezone"])
    local_now = (now or datetime.now(dt_timezone.utc)).astimezone(tz)
    yesterday = local_now.date() - timedelta(days=1)
    period_end = yesterday
    period_start = period_end - timedelta(days=period_end.weekday())
    return period_start, period_end


@dataclass
class RepActivityRow:
    rep_id: UUID
    rep_name: str
    calls_made: int
    calls_connected: int
    emails_sent_manual: int
    emails_sent_instantly: int
    emails_replied_to: int
    linkedin_touches: int

    @property
    def connect_rate(self) -> float:
        return (self.calls_connected / self.calls_made) if self.calls_made else 0.0

    @property
    def emails_sent(self) -> int:
        return self.emails_sent_manual + self.emails_sent_instantly


@dataclass
class TeamActivityReport:
    period_start: date
    period_end: date
    timezone: str
    rows: list[RepActivityRow] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    html_body: str = ""
    recipients: list[str] = field(default_factory=list)
    send_results: list[dict] = field(default_factory=list)

    @property
    def totals(self) -> RepActivityRow:
        return RepActivityRow(
            rep_id=UUID(int=0),
            rep_name="Team total",
            calls_made=sum(r.calls_made for r in self.rows),
            calls_connected=sum(r.calls_connected for r in self.rows),
            emails_sent_manual=sum(r.emails_sent_manual for r in self.rows),
            emails_sent_instantly=sum(r.emails_sent_instantly for r in self.rows),
            emails_replied_to=sum(r.emails_replied_to for r in self.rows),
            linkedin_touches=sum(r.linkedin_touches for r in self.rows),
        )


async def build_team_activity_report(
    session: AsyncSession,
    period_start: date,
    period_end: date,
    *,
    report_settings: dict[str, Any] | None = None,
) -> TeamActivityReport:
    config = report_settings or DEFAULT_TEAM_ACTIVITY_REPORT_SETTINGS
    tz_name = config.get("send_timezone", "Asia/Kolkata")
    period = pm.resolve_period("week", anchor=period_start, tz_name=tz_name)

    reps = (
        await session.execute(
            select(User)
            .where(User.is_active == True, User.role.in_(TEAM_ACTIVITY_REPORT_ROLES))  # noqa: E712
            .order_by(User.role.desc(), User.name)
        )
    ).scalars().all()

    rows: list[RepActivityRow] = []
    for rep in reps:
        rows.append(
            RepActivityRow(
                rep_id=rep.id,
                rep_name=rep.name,
                calls_made=await pm.calls_made(session, rep.id, period),
                calls_connected=await pm.calls_connected(session, rep.id, period),
                emails_sent_manual=await pm.emails_sent_manual(session, rep.id, period),
                emails_sent_instantly=await pm.emails_sent_instantly(session, rep.id, period),
                emails_replied_to=await pm.emails_replied_to(session, rep.id, period),
                linkedin_touches=await pm.linkedin_touches(session, rep.id, period),
            )
        )

    report = TeamActivityReport(period_start=period_start, period_end=period_end, timezone=tz_name, rows=rows)
    report.subject = f"Team Activity Report — {period_start.strftime('%b %d')}–{period_end.strftime('%b %d, %Y')}"
    report.body = _render_report_text(report)
    report.html_body = _render_report_html(report)
    return report


def _render_report_text(report: TeamActivityReport) -> str:
    lines = [
        f"Team Activity Report — {report.period_start.strftime('%a, %b %d')} to {report.period_end.strftime('%a, %b %d, %Y')}",
        "",
    ]
    for row in report.rows:
        lines.append(
            f"{row.rep_name}: {row.calls_made} calls made, {row.calls_connected} connected "
            f"({row.connect_rate:.0%}), {row.emails_sent_manual} manual emails, "
            f"{row.emails_sent_instantly} Instantly emails, {row.emails_replied_to} replied, "
            f"{row.linkedin_touches} LinkedIn touches"
        )
    t = report.totals
    lines.append("")
    lines.append(
        f"Team total: {t.calls_made} calls made, {t.calls_connected} connected ({t.connect_rate:.0%}), "
        f"{t.emails_sent_manual} manual emails, {t.emails_sent_instantly} Instantly emails, "
        f"{t.emails_replied_to} replied, {t.linkedin_touches} LinkedIn touches"
    )
    return "\n".join(lines)


def _e(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _render_report_html(report: TeamActivityReport) -> str:
    """Fully inline styles, table-based layout — same pattern as the
    redesigned weekly_digest._render_digest_html, and the same rendering
    problem it fixed: a <style> block or flexbox here would get stripped or
    ignored by Gmail."""
    period_label = f"{report.period_start.strftime('%a, %b %d')} &ndash; {report.period_end.strftime('%a, %b %d, %Y')}"
    totals = report.totals

    TH = "padding:9px 12px;color:#94a3b8;font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid #e5e3dc;white-space:nowrap;"
    # Two-line variant for the three "<word> Email" headers — these were the
    # widest labels and pushed the table past its container at normal
    # single-line width, so they break onto a fixed second line instead.
    TH2 = "padding:9px 12px;color:#94a3b8;font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid #e5e3dc;line-height:1.5;"
    TD = "padding:10px 12px;border-bottom:1px solid #eeece5;font-size:12.5px;color:#1f2a37;"
    TD_LAST = "padding:10px 12px;font-size:12.5px;color:#1f2a37;"

    def _row(row: RepActivityRow, is_last: bool) -> str:
        td = TD_LAST if is_last else TD
        return f"""<tr>
              <td style="{td}"><strong>{_e(row.rep_name)}</strong></td>
              <td align="center" style="{td}">{row.calls_made}</td>
              <td align="center" style="{td}">{row.calls_connected}</td>
              <td align="center" style="{td}color:#6b7280;">{row.connect_rate:.0%}</td>
              <td align="center" style="{td}">{row.emails_sent_manual}</td>
              <td align="center" style="{td}">{row.emails_sent_instantly}</td>
              <td align="center" style="{td}color:#6b7280;">{row.emails_replied_to}</td>
              <td align="center" style="{td}">{row.linkedin_touches}</td>
            </tr>"""

    # Every data row keeps its bottom border (is_last=False) — the total row
    # below supplies its own top divider, so the last rep row doesn't need
    # a borderless bottom edge the way single-section digest tables do.
    rows_html = "".join(_row(row, False) for row in report.rows) or (
        '<tr><td colspan="8" style="padding:16px;color:#94a3b8;font-style:italic;text-align:center;font-size:12.5px;">No active reps found</td></tr>'
    )

    total_row_html = f"""<tr style="background:#f3f6ec;">
          <td style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">Team total</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">{totals.calls_made}</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">{totals.calls_connected}</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#4d7c0f;font-weight:700;">{totals.connect_rate:.0%}</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">{totals.emails_sent_manual}</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">{totals.emails_sent_instantly}</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">{totals.emails_replied_to}</td>
          <td align="center" style="padding:11px 12px;border-top:2px solid #cfe89a;font-size:12.5px;color:#1f2a37;font-weight:700;">{totals.linkedin_touches}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f1ea;">
<div style="max-width:780px;margin:0 auto;padding:28px 22px 34px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="background:#ffffff;border-radius:14px;padding:32px 30px 28px;">

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 22px;">
      <tr>
        <td style="width:22px;height:22px;border-radius:6px;background:#4d7c0f;color:#fff;font-size:12px;font-weight:700;text-align:center;vertical-align:middle;">b</td>
        <td style="padding-left:8px;font-size:13px;font-weight:600;color:#1f2a37;">Beacon CRM</td>
      </tr>
    </table>

    <div style="font-size:19px;font-weight:600;color:#1f2a37;margin:0 0 4px;letter-spacing:-0.01em;">Team Activity Report</div>
    <div style="font-size:12.5px;color:#94a3b8;margin:0 0 22px;">{period_label} &middot; {_e(report.timezone)}</div>

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 26px;">
      <tr>
        <td width="33%" style="border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
          <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{totals.calls_made}</div>
          <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">Calls made</div>
        </td>
        <td width="4"></td>
        <td width="33%" style="border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
          <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{totals.emails_sent}</div>
          <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">Emails sent</div>
        </td>
        <td width="4"></td>
        <td width="33%" style="border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
          <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{totals.linkedin_touches}</div>
          <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">LinkedIn touches</div>
        </td>
      </tr>
    </table>

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px;">
      <tr>
        <td style="width:7px;height:7px;border-radius:999px;background:#2c4f9e;font-size:0;line-height:0;">&nbsp;</td>
        <td style="padding-left:9px;font-size:13.5px;font-weight:600;color:#1f2a37;">By rep</td>
      </tr>
    </table>

    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border:1px solid #e5e3dc;border-radius:12px;border-collapse:separate;overflow:hidden;">
      <tr style="background:#fafaf8;">
        <th align="left" style="{TH}">Rep</th>
        <th align="center" style="{TH}">Calls made</th>
        <th align="center" style="{TH}">Connected</th>
        <th align="center" style="{TH}">Connect %</th>
        <th align="center" style="{TH2}">Manual<br />Email</th>
        <th align="center" style="{TH2}">Instantly<br />Email</th>
        <th align="center" style="{TH2}">Replied<br />Email</th>
        <th align="center" style="{TH}">LinkedIn</th>
      </tr>
      {rows_html}
      {total_row_html}
    </table>

    <div style="margin-top:14px;font-size:11px;color:#94a3b8;line-height:1.6;">
      "Connect %" is Calls Connected &divide; Calls Made. "Manual" is Gmail-sent and manually logged emails combined;
      "Instantly" is automated sequence sends. LinkedIn touches are only what reps logged via "Log LinkedIn" &mdash;
      there's no LinkedIn API, so this column reflects logging habits, not raw LinkedIn activity.
    </div>

    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e5e3dc;font-size:11px;color:#94a3b8;line-height:1.6;">
      Sent every Monday at {DEFAULT_TEAM_ACTIVITY_REPORT_SETTINGS['send_hour']}:{DEFAULT_TEAM_ACTIVITY_REPORT_SETTINGS['send_minute']:02d} {_e(report.timezone)} &middot; Covers the previous Mon&ndash;Sun window.<br />
      Questions or want off this list? Reply to this email or ping an admin in Beacon.
    </div>
  </div>
</div>
</body>
</html>"""


async def send_team_activity_report_email(
    session: AsyncSession,
    period_start: date,
    period_end: date,
    *,
    recipients: list[str] | None = None,
    report_settings: dict[str, Any] | None = None,
) -> TeamActivityReport:
    config = report_settings or await load_team_activity_report_settings(session)
    report = await build_team_activity_report(session, period_start, period_end, report_settings=config)

    safe_recipients, blocked_recipients = _resolve_report_recipients(recipients, config)
    report.recipients = safe_recipients
    if not safe_recipients:
        report.send_results = [
            {
                "status": "blocked",
                "error": (
                    "No recipients configured for the team activity report."
                    if is_production_environment()
                    else "Non-production report recipient is not in the allowed recipient list."
                ),
                "blocked_recipients": blocked_recipients,
            }
        ]
        return report

    settings_row = await session.get(WorkspaceSettings, 1)
    if (
        not settings_row
        or not settings_row.report_sender_email
        or not settings_row.report_sender_connected_email
        or not settings_row.report_sender_token_data
    ):
        report.send_results = [
            {"status": "not_configured", "error": "Report sender Gmail account is not connected in Settings."}
        ]
        return report

    if settings_row.report_sender_email.lower() != settings_row.report_sender_connected_email.lower():
        report.send_results = [
            {
                "status": "failed",
                "error": (
                    f"Configured report sender {settings_row.report_sender_email} does not match "
                    f"connected Gmail account {settings_row.report_sender_connected_email}."
                ),
            }
        ]
        return report

    send_results = []
    token_data = settings_row.report_sender_token_data
    reconnect_failure: dict[str, Any] | None = None
    for recipient in report.recipients:
        if reconnect_failure is not None:
            result = dict(reconnect_failure)
        else:
            try:
                result, token_data = await send_gmail_email(
                    token_data=token_data,
                    from_email=settings_row.report_sender_email,
                    to=recipient,
                    subject=report.subject,
                    body=report.body,
                    html_body=report.html_body,
                    from_name="Beacon CRM",
                )
            except Exception as exc:
                logger.exception("Gmail send raised for %s: %s", recipient, exc)
                result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            if result.get("reconnect_required"):
                reconnect_failure = dict(result)
        send_results.append({"to": recipient, **result})

    if token_data != settings_row.report_sender_token_data:
        settings_row.report_sender_token_data = token_data
    failures = [f"{r['to']}: {r.get('error') or 'Gmail send failed'}" for r in send_results if r.get("status") != "sent"]
    if reconnect_failure is not None:
        settings_row.report_sender_last_error = GMAIL_RECONNECT_REQUIRED_ERROR
    elif failures:
        settings_row.report_sender_last_error = " | ".join(failures)[:500]
    else:
        settings_row.report_sender_last_error = None
    session.add(settings_row)
    await session.commit()

    report.send_results = send_results
    return report
