"""
Weekly CRM activity digest — one email, sent to a fixed recipient list every
Monday morning, summarizing the prior Mon-Sun week across exactly 4 sections:

  1. Pipeline deal stage changes           (DealStageHistory)
  2. Accounts marked DND / Not a Fit / Reach Out Later  (AccountStatusHistory)
  3. Prospects marked DND                  (ContactDispositionHistory)
  4. New accounts added via Recent Imports (SourcingBatch)

Every recipient gets the identical, full digest — there is no per-rep
filtering and no row cap on any section, by explicit design decision. Config
lives in WorkspaceSettings.sync_schedule_settings under the "weekly_digest"
key, following the same shape as app.services.us_pod_call_report.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.clients.gmail_sender import send_gmail_email
from app.config import settings
from app.models.company import Company
from app.models.contact import Contact
from app.models.contact_disposition_history import ContactDispositionHistory
from app.models.account_status_history import AccountStatusHistory
from app.models.deal import Deal
from app.models.deal_stage_history import DealStageHistory
from app.models.settings import WorkspaceSettings
from app.models.sourcing_batch import SourcingBatch
from app.models.user import User

logger = logging.getLogger(__name__)

WEEKLY_DIGEST_CONFIG_KEY = "weekly_digest"

WEEKLY_DIGEST_RECIPIENTS = [
    "vaisakh@beacon.li",
    "raghav@beacon.li",
    "maithili@beacon.li",
    "avinash@beacon.li",
    "annie@beacon.li",
]

ACCOUNT_STATUS_TARGETS = ("dnd", "not_a_fit", "reach_out_later")

ACCOUNT_STATUS_LABELS = {
    "dnd": "DND",
    "not_a_fit": "Not a Fit",
    "reach_out_later": "Reach Out Later",
}

STAGE_LABEL_OVERRIDES = {
    "mql": "MQL",
}

DEFAULT_WEEKLY_DIGEST_SETTINGS = {
    "enabled": True,
    "recipients": WEEKLY_DIGEST_RECIPIENTS,
    "send_timezone": "Asia/Kolkata",
    "send_hour": 9,
    "send_minute": 0,
    "send_days": ["mon"],
    "nonprod_scheduled_enabled": False,
    "nonprod_recipients": ["sarthak@beacon.li"],
    "last_scheduled_send_key": None,
    "last_scheduled_send_at": None,
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


def normalize_weekly_digest_settings(value: dict | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    merged = {**DEFAULT_WEEKLY_DIGEST_SETTINGS, **raw}

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
        "recipients": _emails(merged.get("recipients"), WEEKLY_DIGEST_RECIPIENTS),
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


async def load_weekly_digest_settings(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(WorkspaceSettings, 1)
    raw = None
    if row and isinstance(row.sync_schedule_settings, dict):
        raw = row.sync_schedule_settings.get(WEEKLY_DIGEST_CONFIG_KEY)
    return normalize_weekly_digest_settings(raw if isinstance(raw, dict) else None)


def is_production_environment() -> bool:
    return settings.ENVIRONMENT.strip().lower() == "production"


def _resolve_digest_recipients(
    recipients: list[str] | None,
    digest_settings: dict[str, Any],
) -> tuple[list[str], list[str]]:
    requested = list(recipients) if recipients is not None else digest_settings["recipients"]
    if is_production_environment():
        return requested, []

    nonprod = [
        email.strip().lower()
        for email in settings.SALES_REPORT_NONPROD_RECIPIENTS.split(",")
        if email.strip()
    ] or digest_settings.get("nonprod_recipients") or ["sarthak@beacon.li"]
    allowed = set(nonprod)
    safe = [r for r in requested if r.lower() in allowed]
    blocked = [r for r in requested if r.lower() not in allowed]
    if recipients is None:
        return nonprod, []
    return safe, blocked


def weekly_digest_period(now: datetime | None = None, digest_settings: dict[str, Any] | None = None) -> tuple[date, date]:
    """Previous Mon-Sun window, in the digest's send_timezone. Called on a
    Monday send, so `now` (local) minus 1 day lands in the just-finished
    week; walk back to that week's Monday."""
    tz = ZoneInfo((digest_settings or DEFAULT_WEEKLY_DIGEST_SETTINGS)["send_timezone"])
    local_now = (now or datetime.now(dt_timezone.utc)).astimezone(tz)
    yesterday = local_now.date() - timedelta(days=1)
    period_end = yesterday
    period_start = period_end - timedelta(days=period_end.weekday())  # back to Monday
    return period_start, period_end


def _utc_bounds(period_start: date, period_end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    # changed_at columns are TIMESTAMP WITHOUT TIME ZONE (naive UTC, written via
    # datetime.utcnow()) — bind naive UTC bounds so asyncpg doesn't reject the
    # aware/naive mix.
    start = datetime.combine(period_start, time(0, 0), tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
    end = datetime.combine(period_end + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(dt_timezone.utc).replace(tzinfo=None)
    return start, end


def _stage_label(stage: str | None) -> str:
    if not stage:
        return "—"
    if stage in STAGE_LABEL_OVERRIDES:
        return STAGE_LABEL_OVERRIDES[stage]
    return stage.replace("_", " ").title()


@dataclass
class StageChangeRow:
    deal_name: str
    from_stage: str | None
    to_stage: str | None
    changed_by: str
    changed_at: datetime


@dataclass
class AccountStatusRow:
    account_name: str
    to_status: str | None
    changed_by: str
    changed_at: datetime


@dataclass
class ProspectDndRow:
    contact_name: str
    account_name: str
    changed_by: str
    changed_at: datetime


@dataclass
class ImportRow:
    filename: str
    accounts_created: int
    uploaded_by: str
    uploaded_at: datetime


@dataclass
class WeeklyDigest:
    period_start: date
    period_end: date
    timezone: str
    stage_changes: list[StageChangeRow] = field(default_factory=list)
    account_status_changes: list[AccountStatusRow] = field(default_factory=list)
    prospect_dnd: list[ProspectDndRow] = field(default_factory=list)
    imports: list[ImportRow] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    html_body: str = ""
    recipients: list[str] = field(default_factory=list)
    send_results: list[dict] = field(default_factory=list)


async def build_weekly_digest(
    session: AsyncSession,
    period_start: date,
    period_end: date,
    digest_settings: dict[str, Any] | None = None,
) -> WeeklyDigest:
    config = digest_settings or DEFAULT_WEEKLY_DIGEST_SETTINGS
    tz = ZoneInfo(config["send_timezone"])
    start_utc, end_utc = _utc_bounds(period_start, period_end, tz)

    digest = WeeklyDigest(period_start=period_start, period_end=period_end, timezone=config["send_timezone"])

    # ── Section 1: pipeline stage changes ──────────────────────────────────
    stage_rows = (
        await session.execute(
            select(DealStageHistory, Deal.name, User.name)
            .join(Deal, Deal.id == DealStageHistory.deal_id)
            .outerjoin(User, User.id == DealStageHistory.changed_by_id)
            .where(DealStageHistory.changed_at >= start_utc, DealStageHistory.changed_at < end_utc)
            .order_by(DealStageHistory.changed_at.asc())
        )
    ).all()
    for history, deal_name, changer_name in stage_rows:
        digest.stage_changes.append(
            StageChangeRow(
                deal_name=deal_name or "Untitled deal",
                from_stage=history.from_stage,
                to_stage=history.to_stage,
                changed_by=changer_name or "Unknown",
                changed_at=history.changed_at.replace(tzinfo=dt_timezone.utc) if history.changed_at.tzinfo is None else history.changed_at,
            )
        )

    # ── Section 2: account status -> DND / Not a Fit / Reach Out Later ────
    account_rows = (
        await session.execute(
            select(AccountStatusHistory, Company.name, User.name)
            .join(Company, Company.id == AccountStatusHistory.company_id)
            .outerjoin(User, User.id == AccountStatusHistory.changed_by_id)
            .where(
                AccountStatusHistory.changed_at >= start_utc,
                AccountStatusHistory.changed_at < end_utc,
                AccountStatusHistory.to_status.in_(ACCOUNT_STATUS_TARGETS),
            )
            .order_by(AccountStatusHistory.changed_at.asc())
        )
    ).all()
    for history, company_name, changer_name in account_rows:
        digest.account_status_changes.append(
            AccountStatusRow(
                account_name=company_name or "Unnamed account",
                to_status=history.to_status,
                changed_by=changer_name or "Unknown",
                changed_at=history.changed_at.replace(tzinfo=dt_timezone.utc) if history.changed_at.tzinfo is None else history.changed_at,
            )
        )

    # ── Section 3: prospects marked DND ────────────────────────────────────
    dnd_rows = (
        await session.execute(
            select(ContactDispositionHistory, Contact.first_name, Contact.last_name, Company.name, User.name)
            .join(Contact, Contact.id == ContactDispositionHistory.contact_id)
            .outerjoin(Company, Company.id == Contact.company_id)
            .outerjoin(User, User.id == ContactDispositionHistory.changed_by_id)
            .where(
                ContactDispositionHistory.changed_at >= start_utc,
                ContactDispositionHistory.changed_at < end_utc,
                ContactDispositionHistory.to_disposition == "dnd",
            )
            .order_by(ContactDispositionHistory.changed_at.asc())
        )
    ).all()
    for history, first_name, last_name, company_name, changer_name in dnd_rows:
        contact_name = " ".join(part for part in (first_name, last_name) if part).strip()
        digest.prospect_dnd.append(
            ProspectDndRow(
                contact_name=contact_name or "Unnamed prospect",
                account_name=company_name or "—",
                changed_by=changer_name or "Unknown",
                changed_at=history.changed_at.replace(tzinfo=dt_timezone.utc) if history.changed_at.tzinfo is None else history.changed_at,
            )
        )

    # ── Section 4: new accounts via Recent Imports ─────────────────────────
    batch_rows = (
        await session.execute(
            select(SourcingBatch)
            .where(
                SourcingBatch.created_at >= start_utc,
                SourcingBatch.created_at < end_utc,
                SourcingBatch.created_companies > 0,
            )
            .order_by(SourcingBatch.created_at.asc())
        )
    ).scalars().all()
    for batch in batch_rows:
        created_at = batch.created_at.replace(tzinfo=dt_timezone.utc) if batch.created_at.tzinfo is None else batch.created_at
        digest.imports.append(
            ImportRow(
                filename=batch.filename or "Untitled upload",
                accounts_created=batch.created_companies,
                uploaded_by=batch.created_by_name or batch.created_by_email or "Unknown",
                uploaded_at=created_at,
            )
        )

    digest.subject = _digest_subject(digest)
    digest.body = _render_digest_text(digest)
    digest.html_body = _render_digest_html(digest)
    return digest


def _digest_subject(digest: WeeklyDigest) -> str:
    start_label = digest.period_start.strftime("%b %d")
    end_label = digest.period_end.strftime("%b %d, %Y")
    return f"Weekly CRM Digest — {start_label}–{end_label}"


def _fmt_when(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return local.strftime("%a, %-I:%M %p") if hasattr(local, "strftime") else str(local)


def _render_digest_text(digest: WeeklyDigest) -> str:
    tz = ZoneInfo(digest.timezone)
    lines = [
        digest.subject,
        f"{digest.period_start.strftime('%a, %b %d')} - {digest.period_end.strftime('%a, %b %d, %Y')} ({digest.timezone})",
        "",
        f"1) Pipeline stage changes ({len(digest.stage_changes)})",
    ]
    if digest.stage_changes:
        for row in digest.stage_changes:
            lines.append(
                f"   - {row.deal_name}: {_stage_label(row.from_stage)} -> {_stage_label(row.to_stage)} "
                f"by {row.changed_by} on {_fmt_when(row.changed_at, tz)}"
            )
    else:
        lines.append("   (none this week)")

    lines.append("")
    lines.append(f"2) Accounts marked DND / Not a Fit / Reach Out Later ({len(digest.account_status_changes)})")
    if digest.account_status_changes:
        for row in digest.account_status_changes:
            label = ACCOUNT_STATUS_LABELS.get(row.to_status or "", row.to_status or "—")
            lines.append(f"   - {row.account_name}: {label} by {row.changed_by} on {_fmt_when(row.changed_at, tz)}")
    else:
        lines.append("   (none this week)")

    lines.append("")
    lines.append(f"3) Prospects marked DND ({len(digest.prospect_dnd)})")
    if digest.prospect_dnd:
        for row in digest.prospect_dnd:
            lines.append(f"   - {row.contact_name} ({row.account_name}) by {row.changed_by} on {_fmt_when(row.changed_at, tz)}")
    else:
        lines.append("   (none this week)")

    lines.append("")
    total_accounts = sum(r.accounts_created for r in digest.imports)
    lines.append(f"4) New accounts added (Recent Imports) — {len(digest.imports)} uploads, {total_accounts} accounts")
    if digest.imports:
        for row in digest.imports:
            lines.append(f"   - {row.filename}: {row.accounts_created} accounts by {row.uploaded_by} on {_fmt_when(row.uploaded_at, tz)}")
    else:
        lines.append("   (none this week)")

    lines.append("")
    lines.append(f"Sent every Monday at {DEFAULT_WEEKLY_DIGEST_SETTINGS['send_hour']}:00 {digest.timezone}. Covers the previous Mon-Sun window.")
    return "\n".join(lines)


def _e(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _render_digest_html(digest: WeeklyDigest) -> str:
    tz = ZoneInfo(digest.timezone)

    def _empty_row(colspan: int) -> str:
        return f'<tr class="empty-row"><td colspan="{colspan}">No activity this week</td></tr>'

    # Section 1 rows
    stage_rows_html = "".join(
        f"""<tr>
              <td><strong>{_e(row.deal_name)}</strong></td>
              <td><span class="pill pill-stage">{_e(_stage_label(row.from_stage))}</span><span class="arrow">&rarr;</span><span class="pill pill-stage">{_e(_stage_label(row.to_stage))}</span></td>
              <td class="who">{_e(row.changed_by)}</td>
              <td class="when">{_e(_fmt_when(row.changed_at, tz))}</td>
            </tr>"""
        for row in digest.stage_changes
    ) or _empty_row(4)

    # Section 2 rows
    def _status_pill(status: str | None) -> str:
        label = ACCOUNT_STATUS_LABELS.get(status or "", status or "—")
        cls = "pill-danger" if status == "dnd" else "pill-warn"
        return f'<span class="pill {cls}">{_e(label)}</span>'

    account_rows_html = "".join(
        f"""<tr>
              <td><strong>{_e(row.account_name)}</strong></td>
              <td>{_status_pill(row.to_status)}</td>
              <td class="who">{_e(row.changed_by)}</td>
              <td class="when">{_e(_fmt_when(row.changed_at, tz))}</td>
            </tr>"""
        for row in digest.account_status_changes
    ) or _empty_row(4)

    # Section 3 rows
    prospect_rows_html = "".join(
        f"""<tr>
              <td><strong>{_e(row.contact_name)}</strong></td>
              <td class="who">{_e(row.account_name)}</td>
              <td class="who">{_e(row.changed_by)}</td>
              <td class="when">{_e(_fmt_when(row.changed_at, tz))}</td>
            </tr>"""
        for row in digest.prospect_dnd
    ) or _empty_row(4)

    # Section 4 rows
    import_rows_html = "".join(
        f"""<tr>
              <td><strong>{_e(row.filename)}</strong></td>
              <td class="num">{row.accounts_created}</td>
              <td class="who">{_e(row.uploaded_by)}</td>
              <td class="when">{_e(_fmt_when(row.uploaded_at, tz))}</td>
            </tr>"""
        for row in digest.imports
    ) or _empty_row(4)

    total_import_accounts = sum(r.accounts_created for r in digest.imports)
    period_label = f"{digest.period_start.strftime('%a, %b %d')} &ndash; {digest.period_end.strftime('%a, %b %d, %Y')}"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f1ea;">
<style>
  .email-body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  .pill {{ display:inline-flex;align-items:center;font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap; }}
  .pill-stage {{ background:#eaf0fb;color:#2c4f9e; }}
  .pill-danger {{ background:#fbeaea;color:#a53434; }}
  .pill-warn {{ background:#fdf1e3;color:#a5590b; }}
  .pill-import {{ background:#f1eefe;color:#5b3fa0; }}
  table {{ border-collapse:collapse;width:100%;background:#ffffff; }}
  thead tr {{ background:#fafaf8; }}
  th {{ text-align:left;padding:9px 12px;color:#94a3b8;font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid #e5e3dc; }}
  th.num, td.num {{ text-align:right; }}
  td {{ padding:10px 12px;border-bottom:1px solid #eeece5;font-size:12.5px;color:#1f2a37;vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  .who {{ color:#6b7280;font-size:12px; }}
  .when {{ color:#94a3b8;font-size:11.5px;white-space:nowrap; }}
  .arrow {{ color:#94a3b8;margin:0 4px;font-size:11px; }}
  .empty-row td {{ color:#94a3b8;font-style:italic;text-align:center;padding:16px; }}
</style>
<div style="max-width:640px;margin:0 auto;padding:28px 22px 34px;">
  <div class="email-body" style="background:#ffffff;border-radius:14px;padding:32px 30px 28px;">
    <div style="display:flex;align-items:center;gap:8px;margin:0 0 22px;">
      <div style="width:22px;height:22px;border-radius:6px;background:#4d7c0f;color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;">b</div>
      <div style="font-size:13px;font-weight:600;color:#1f2a37;">Beacon CRM</div>
    </div>

    <h1 style="font-size:19px;font-weight:600;color:#1f2a37;margin:0 0 4px;letter-spacing:-0.01em;">Weekly Activity Digest</h1>
    <p style="font-size:12.5px;color:#94a3b8;margin:0 0 22px;">{period_label} &middot; {_e(digest.timezone)}</p>

    <div style="display:flex;gap:10px;margin:0 0 26px;">
      <div style="flex:1;border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
        <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{len(digest.stage_changes)}</div>
        <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">Stage moves</div>
      </div>
      <div style="flex:1;border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
        <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{len(digest.account_status_changes)}</div>
        <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">Accounts flagged</div>
      </div>
      <div style="flex:1;border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
        <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{len(digest.prospect_dnd)}</div>
        <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">Prospects DND</div>
      </div>
      <div style="flex:1;border:1px solid #e5e3dc;border-radius:10px;padding:12px 10px;text-align:center;">
        <div style="font-size:19px;font-weight:700;color:#1f2a37;line-height:1.1;">{len(digest.imports)}</div>
        <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-top:4px;">New imports</div>
      </div>
    </div>

    <div style="margin:0 0 26px;">
      <div style="display:flex;align-items:center;gap:9px;margin:0 0 12px;">
        <span style="width:7px;height:7px;border-radius:999px;flex-shrink:0;background:#2c4f9e;display:inline-block;"></span>
        <span style="font-size:13.5px;font-weight:600;color:#1f2a37;">Pipeline stage changes</span>
        <span class="pill pill-stage">{len(digest.stage_changes)}</span>
      </div>
      <div style="border:1px solid #e5e3dc;border-radius:12px;overflow:hidden;">
        <table>
          <thead><tr><th>Deal</th><th>Move</th><th>Changed by</th><th>When</th></tr></thead>
          <tbody>{stage_rows_html}</tbody>
        </table>
      </div>
    </div>

    <div style="margin:0 0 26px;">
      <div style="display:flex;align-items:center;gap:9px;margin:0 0 12px;">
        <span style="width:7px;height:7px;border-radius:999px;flex-shrink:0;background:#a53434;display:inline-block;"></span>
        <span style="font-size:13.5px;font-weight:600;color:#1f2a37;">Accounts marked DND / Not a Fit / Reach Out Later</span>
        <span class="pill pill-danger">{len(digest.account_status_changes)}</span>
      </div>
      <div style="border:1px solid #e5e3dc;border-radius:12px;overflow:hidden;">
        <table>
          <thead><tr><th>Account</th><th>Marked as</th><th>Changed by</th><th>When</th></tr></thead>
          <tbody>{account_rows_html}</tbody>
        </table>
      </div>
    </div>

    <div style="margin:0 0 26px;">
      <div style="display:flex;align-items:center;gap:9px;margin:0 0 12px;">
        <span style="width:7px;height:7px;border-radius:999px;flex-shrink:0;background:#a5590b;display:inline-block;"></span>
        <span style="font-size:13.5px;font-weight:600;color:#1f2a37;">Prospects marked DND</span>
        <span class="pill pill-warn">{len(digest.prospect_dnd)}</span>
      </div>
      <div style="border:1px solid #e5e3dc;border-radius:12px;overflow:hidden;">
        <table>
          <thead><tr><th>Prospect</th><th>Account</th><th>Marked by</th><th>When</th></tr></thead>
          <tbody>{prospect_rows_html}</tbody>
        </table>
      </div>
    </div>

    <div style="margin:0 0 4px;">
      <div style="display:flex;align-items:center;gap:9px;margin:0 0 12px;">
        <span style="width:7px;height:7px;border-radius:999px;flex-shrink:0;background:#5b3fa0;display:inline-block;"></span>
        <span style="font-size:13.5px;font-weight:600;color:#1f2a37;">New accounts added (Recent Imports)</span>
        <span class="pill pill-import">{len(digest.imports)} uploads &middot; {total_import_accounts} accounts</span>
      </div>
      <div style="border:1px solid #e5e3dc;border-radius:12px;overflow:hidden;">
        <table>
          <thead><tr><th>File</th><th class="num">Accounts</th><th>Uploaded by</th><th>When</th></tr></thead>
          <tbody>{import_rows_html}</tbody>
        </table>
      </div>
    </div>

    <div style="margin-top:30px;padding-top:16px;border-top:1px solid #e5e3dc;font-size:11px;color:#94a3b8;line-height:1.6;">
      Sent every Monday at {DEFAULT_WEEKLY_DIGEST_SETTINGS['send_hour']}:00 {_e(digest.timezone)} &middot; Covers the previous Mon&ndash;Sun window.<br />
      Questions or want off this list? Reply to this email or ping an admin in Beacon.
    </div>
  </div>
</div>
</body>
</html>"""


async def send_weekly_digest_email(
    session: AsyncSession,
    period_start: date,
    period_end: date,
    *,
    recipients: list[str] | None = None,
    digest_settings: dict[str, Any] | None = None,
) -> WeeklyDigest:
    config = digest_settings or await load_weekly_digest_settings(session)
    digest = await build_weekly_digest(session, period_start, period_end, digest_settings=config)

    safe_recipients, blocked_recipients = _resolve_digest_recipients(recipients, config)
    digest.recipients = safe_recipients
    if not safe_recipients:
        digest.send_results = [
            {
                "status": "blocked",
                "error": (
                    "No recipients configured for the weekly digest."
                    if is_production_environment()
                    else "Non-production digest recipient is not in the allowed recipient list."
                ),
                "blocked_recipients": blocked_recipients,
            }
        ]
        return digest

    settings_row = await session.get(WorkspaceSettings, 1)
    if (
        not settings_row
        or not settings_row.report_sender_email
        or not settings_row.report_sender_connected_email
        or not settings_row.report_sender_token_data
    ):
        digest.send_results = [
            {"status": "not_configured", "error": "Report sender Gmail account is not connected in Settings."}
        ]
        return digest

    if settings_row.report_sender_email.lower() != settings_row.report_sender_connected_email.lower():
        digest.send_results = [
            {
                "status": "failed",
                "error": (
                    f"Configured report sender {settings_row.report_sender_email} does not match "
                    f"connected Gmail account {settings_row.report_sender_connected_email}."
                ),
            }
        ]
        return digest

    send_results = []
    token_data = settings_row.report_sender_token_data
    for recipient in digest.recipients:
        try:
            result, token_data = await send_gmail_email(
                token_data=token_data,
                from_email=settings_row.report_sender_email,
                to=recipient,
                subject=digest.subject,
                body=digest.body,
                html_body=digest.html_body,
                from_name="Beacon CRM",
            )
        except Exception as exc:  # never let one bad mailbox crash the whole send
            logger.exception("Gmail send raised for %s: %s", recipient, exc)
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        send_results.append({"to": recipient, **result})

    if token_data != settings_row.report_sender_token_data:
        settings_row.report_sender_token_data = token_data
    session.add(settings_row)
    await session.commit()

    digest.send_results = send_results
    sent_ok = sum(1 for r in send_results if r.get("status") == "sent")
    logger.info(
        "Weekly CRM digest attempted for %s-%s: %d/%d recipients delivered",
        period_start, period_end, sent_ok, len(send_results),
    )
    return digest
