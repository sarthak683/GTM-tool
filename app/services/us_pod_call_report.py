import html
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.clients.gmail_sender import send_gmail_email
from app.config import settings
from app.models.activity import Activity
from app.models.call_recording import CallRecording
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.meeting import Meeting
from app.models.settings import WorkspaceSettings
from app.models.user import User

logger = logging.getLogger(__name__)

REPORT_TIMEZONE = ZoneInfo("America/Chicago")
REPORT_CUTOFF_TIMEZONE = ZoneInfo("Asia/Kolkata")
REPORT_CUTOFF_HOUR = 6
LOOKBACK_DAYS = 7
DAY_KEYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
WEEKDAY_TO_KEY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

US_POD_REPS = [
    {"name": "Pravalika Jamalpur", "email": "pravalika@beacon.li", "aliases": ["pravalika"]},
    {"name": "Mahesh Pothula", "email": "mahesh@beacon.li", "aliases": ["mahesh"]},
    {"name": "Pulkit Anand", "email": "pulkit@beacon.li", "aliases": ["pulkit"]},
]

US_POD_REPORT_RECIPIENTS = [
    "sehar@beacon.li",
    "rakesh@beacon.li",
    "shahruk@beacon.li",
    "pravalika@beacon.li",
    "mahesh@beacon.li",
    "pulkit@beacon.li",
    "sarthak@beacon.li",
    "maithili@beacon.li",
    "manognya@beacon.li",
]

# India pod — same {name, email, aliases} roster shape as US, sourced from the
# shared pod registry. The same call-report machinery is reused, just pointed at
# this roster + its own config block (`india_sales_report`) and schedule.
INDIA_POD_REPS = [
    {"name": "Dyuthith Din", "email": "dyuthith@beacon.li", "aliases": ["dyuthith"]},
    {"name": "Yashveer Singh", "email": "yash@beacon.li", "aliases": ["yashveer", "yash"]},
    {"name": "Bhavya Mukkera", "email": "bhavya@beacon.li", "aliases": ["bhavya"]},
    {"name": "Sandeep Sinha", "email": "sandeep@beacon.li", "aliases": ["sandeep"]},
    {"name": "Sipra Sonali Palta", "email": "sipra@beacon.li", "aliases": ["sipra"]},
]

INDIA_POD_REPORT_RECIPIENTS = [
    "annie@beacon.li",
    "dyuthith@beacon.li",
    "yash@beacon.li",
    "bhavya@beacon.li",
    "sandeep@beacon.li",
    "sipra@beacon.li",
    "rakesh@beacon.li",  # boss
    "sarthak@beacon.li",
    "maithili@beacon.li",
    "manognya@beacon.li",
]

# India pod works an IST daytime (it doesn't call US prospects overnight like the
# US pod), so its "day" is a normal IST calendar day: reset at IST midnight, send
# the recap the next morning IST. Distinct from the US pod's 7:30 AM IST cutoff.
INDIA_DEFAULT_SALES_REPORT_SETTINGS = {
    "enabled": False,  # seeded on; flip on after first verification
    "recipients": INDIA_POD_REPORT_RECIPIENTS,
    "send_timezone": "Asia/Kolkata",
    "send_hour": 9,
    "send_minute": 0,
    "cutoff_timezone": "Asia/Kolkata",
    "cutoff_hour": 0,
    "cutoff_minute": 0,
    "report_label_timezone": "Asia/Kolkata",
    "send_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "weekly_report_day": "sat",
    "skip_weekends": True,
    "nonprod_scheduled_enabled": False,
    "nonprod_recipients": ["sarthak@beacon.li"],
}

DEFAULT_SALES_REPORT_SETTINGS = {
    "enabled": True,
    "recipients": US_POD_REPORT_RECIPIENTS,
    "send_timezone": "Asia/Kolkata",
    "send_hour": 7,
    "send_minute": 0,
    "cutoff_timezone": "Asia/Kolkata",
    "cutoff_hour": 6,
    "cutoff_minute": 0,
    "report_label_timezone": "America/Chicago",
    # Saturday IS a send day: the pod reports US/Chicago activity from an IST
    # clock, so Friday's US workday only completes Saturday morning IST. The
    # Saturday send delivers Friday's daily + the weekly. skip_weekends still
    # suppresses reports whose *period* is a weekend (handled per report-period,
    # not per send-day).
    "send_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "weekly_report_day": "sat",
    "skip_weekends": True,
    "nonprod_scheduled_enabled": False,
    "nonprod_recipients": ["sarthak@beacon.li"],
    "last_scheduled_send_key": None,
    "last_scheduled_send_at": None,
}


def _zone_name(value: object, fallback: str) -> str:
    name = str(value or fallback).strip() or fallback
    try:
        ZoneInfo(name)
        return name
    except Exception:
        return fallback


def normalize_sales_report_settings(value: dict | None, defaults: dict | None = None) -> dict[str, Any]:
    base = defaults or DEFAULT_SALES_REPORT_SETTINGS
    raw = value if isinstance(value, dict) else {}
    merged = {**DEFAULT_SALES_REPORT_SETTINGS, **base, **raw}

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
        for day in (merged.get("send_days") if isinstance(merged.get("send_days"), list) else DEFAULT_SALES_REPORT_SETTINGS["send_days"])
    ]
    send_days = [day for day in send_days if day in DAY_KEYS] or DEFAULT_SALES_REPORT_SETTINGS["send_days"]
    weekly_report_day = str(merged.get("weekly_report_day") or "fri").strip().lower()[:3]
    if weekly_report_day not in DAY_KEYS:
        weekly_report_day = "fri"

    return {
        "enabled": bool(merged.get("enabled")),
        "recipients": _emails(merged.get("recipients"), base.get("recipients", US_POD_REPORT_RECIPIENTS)),
        "send_timezone": _zone_name(merged.get("send_timezone"), "Asia/Kolkata"),
        "send_hour": max(0, min(23, int(merged.get("send_hour") or 0))),
        "send_minute": max(0, min(59, int(merged.get("send_minute") or 0))),
        "cutoff_timezone": _zone_name(merged.get("cutoff_timezone"), "Asia/Kolkata"),
        "cutoff_hour": max(0, min(23, int(merged.get("cutoff_hour") or 0))),
        "cutoff_minute": max(0, min(59, int(merged.get("cutoff_minute") or 0))),
        "report_label_timezone": _zone_name(merged.get("report_label_timezone"), "America/Chicago"),
        "send_days": send_days,
        "weekly_report_day": weekly_report_day,
        "skip_weekends": bool(merged.get("skip_weekends")),
        "nonprod_scheduled_enabled": bool(merged.get("nonprod_scheduled_enabled")),
        "nonprod_recipients": _emails(merged.get("nonprod_recipients"), ["sarthak@beacon.li"]),
        "last_scheduled_send_key": merged.get("last_scheduled_send_key"),
        "last_scheduled_send_at": merged.get("last_scheduled_send_at"),
        # Per-type send-key tracking so daily and weekly can dedupe
        # independently when both fire on the weekly day. Falls back to
        # the legacy single key when only one of the two has ever run.
        "last_scheduled_daily_send_key": merged.get("last_scheduled_daily_send_key"),
        "last_scheduled_weekly_send_key": merged.get("last_scheduled_weekly_send_key"),
        # When true (default), the weekly day ALSO sends the daily report for
        # the prior weekday — so reps get both the Thursday recap and the
        # Mon-Fri summary on Friday morning. Set false to keep the old
        # one-email-per-day behavior.
        "weekly_day_also_sends_daily": bool(merged.get("weekly_day_also_sends_daily", True)),
    }


async def load_sales_report_settings(
    session: AsyncSession,
    key: str = "sales_report",
    defaults: dict | None = None,
) -> dict[str, Any]:
    """Load a pod's report config block. `key` selects the block in
    sync_schedule_settings ("sales_report" = US, "india_sales_report" = India);
    `defaults` supplies that pod's defaults when the block is absent/partial."""
    row = await session.get(WorkspaceSettings, 1)
    raw = None
    if row and isinstance(row.sync_schedule_settings, dict):
        raw = row.sync_schedule_settings.get(key)
    return normalize_sales_report_settings(raw if isinstance(raw, dict) else None, defaults=defaults)


def _report_zone(config: dict[str, Any] | None, key: str, fallback: ZoneInfo) -> ZoneInfo:
    if not config:
        return fallback
    return ZoneInfo(_zone_name(config.get(key), fallback.key))


def _cutoff_hour(config: dict[str, Any] | None) -> int:
    if not config:
        return REPORT_CUTOFF_HOUR
    return max(0, min(23, int(config.get("cutoff_hour", REPORT_CUTOFF_HOUR))))


def _cutoff_minute(config: dict[str, Any] | None) -> int:
    if not config:
        return 0
    return max(0, min(59, int(config.get("cutoff_minute", 0) or 0)))


def is_production_environment() -> bool:
    return settings.ENVIRONMENT.strip().lower() == "production"


def _nonprod_report_recipient_allowlist(report_settings: dict[str, Any] | None = None) -> list[str]:
    recipients = [
        email.strip().lower()
        for email in settings.SALES_REPORT_NONPROD_RECIPIENTS.split(",")
        if email.strip()
    ]
    configured = normalize_sales_report_settings(report_settings).get("nonprod_recipients") if report_settings else None
    return recipients or configured or ["sarthak@beacon.li"]


def _resolve_report_recipients(
    recipients: list[str] | None,
    report_settings: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    config = normalize_sales_report_settings(report_settings)
    requested = recipients or config["recipients"]
    if is_production_environment():
        return requested, []

    allowed = set(_nonprod_report_recipient_allowlist(config))
    safe_recipients = [recipient for recipient in requested if recipient.lower() in allowed]
    blocked_recipients = [recipient for recipient in requested if recipient.lower() not in allowed]

    if recipients is None:
        safe_recipients = _nonprod_report_recipient_allowlist(config)
        blocked_recipients = []

    return safe_recipients, blocked_recipients


@dataclass
class ResolvedRep:
    name: str
    email: str
    user_id: UUID | None
    user_email: str | None
    matched: bool


def default_report_date(now: datetime | None = None, report_settings: dict[str, Any] | None = None) -> date:
    label_tz = _report_zone(report_settings, "report_label_timezone", REPORT_TIMEZONE)
    return _latest_completed_report_cutoff(now, report_settings).astimezone(label_tz).date()


def _latest_completed_report_cutoff(now: datetime | None = None, report_settings: dict[str, Any] | None = None) -> datetime:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff_tz = _report_zone(report_settings, "cutoff_timezone", REPORT_CUTOFF_TIMEZONE)
    cutoff_hour = _cutoff_hour(report_settings)
    cutoff_minute = _cutoff_minute(report_settings)
    local_reference = reference.astimezone(cutoff_tz)
    cutoff_date = local_reference.date()
    if local_reference.time() < time(cutoff_hour, cutoff_minute):
        cutoff_date -= timedelta(days=1)
    return datetime.combine(
        cutoff_date,
        time(cutoff_hour, cutoff_minute),
        tzinfo=cutoff_tz,
    )


def current_report_local_date(now: datetime | None = None, report_settings: dict[str, Any] | None = None) -> date:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff_tz = _report_zone(report_settings, "cutoff_timezone", REPORT_CUTOFF_TIMEZONE)
    return reference.astimezone(cutoff_tz).date()


def is_weekend_report_day(now: datetime | None = None, report_settings: dict[str, Any] | None = None) -> bool:
    return current_report_local_date(now, report_settings).weekday() >= 5


def scheduled_report_type(now: datetime | None = None, report_settings: dict[str, Any] | None = None) -> str:
    settings = normalize_sales_report_settings(report_settings)
    weekday_key = WEEKDAY_TO_KEY[current_report_local_date(now, settings).weekday()]
    return "weekly" if weekday_key == settings["weekly_report_day"] else "daily"


def weekly_report_period(report_date: date | None = None, report_settings: dict[str, Any] | None = None) -> tuple[date, date]:
    end_date = report_date or default_report_date(report_settings=report_settings)
    start_date = end_date - timedelta(days=end_date.weekday())
    return start_date, end_date


def _utc_bounds_for_report_day(day: date, report_settings: dict[str, Any] | None = None) -> tuple[datetime, datetime]:
    cutoff_tz = _report_zone(report_settings, "cutoff_timezone", REPORT_CUTOFF_TIMEZONE)
    cutoff_hour = _cutoff_hour(report_settings)
    cutoff_minute = _cutoff_minute(report_settings)
    local_end = datetime.combine(
        day + timedelta(days=1),
        time(cutoff_hour, cutoff_minute),
        tzinfo=cutoff_tz,
    )
    local_start = local_end - timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _activity_report_date(activity: Activity, report_settings: dict[str, Any] | None = None) -> date:
    created_at = activity.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    cutoff_tz = _report_zone(report_settings, "cutoff_timezone", REPORT_CUTOFF_TIMEZONE)
    cutoff_hour = _cutoff_hour(report_settings)
    cutoff_minute = _cutoff_minute(report_settings)
    label_tz = _report_zone(report_settings, "report_label_timezone", REPORT_TIMEZONE)
    local_created_at = created_at.astimezone(cutoff_tz)
    report_period_start = local_created_at.date()
    if local_created_at.time() < time(cutoff_hour, cutoff_minute):
        report_period_start -= timedelta(days=1)
    report_cutoff_end = datetime.combine(
        report_period_start + timedelta(days=1),
        time(cutoff_hour, cutoff_minute),
        tzinfo=cutoff_tz,
    )
    return report_cutoff_end.astimezone(label_tz).date()


def _normalize(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _bucket_from_manual_disposition_text(activity: Activity) -> str | None:
    """Recover the intended outcome for legacy manual-call rows.

    The prospecting UI used to default every call outcome to "attempted" and
    did not change it when the rep selected a connected disposition. The saved
    activity content still starts with the disposition label, so use that as a
    compatibility signal for reports generated from already-logged calls.
    """
    if _normalize(activity.source) != "manual":
        return None
    text = _normalize(activity.content)
    if not text:
        return None

    if text.startswith("call back later/rescheduled"):
        return "callback"
    if text.startswith("left voicemail") or text.startswith("voicemail"):
        return "voicemail"
    if text.startswith("no answer/busy signal") or text.startswith("invalid number/wrong number"):
        return "not_answered"
    if text.startswith(
        (
            "demo scheduled/booked",
            "interested/follow-up required",
            "meeting confirmed",
            "gatekeeper (connected to admin, not lead)",
            "connected - not interested",
            "do not contact/dnc",
            "contact poor fit",
            "redirected to other icp",
        )
    ):
        return "connected"
    return None


def _is_connected_call(activity: Activity) -> bool:
    disposition_bucket = _bucket_from_manual_disposition_text(activity)
    if disposition_bucket in {"connected", "callback"}:
        return True
    outcome = _normalize(activity.call_outcome).replace("-", "_")
    if outcome in {"connected", "answered", "completed", "success"}:
        return True
    if outcome in {"missed", "no_answer", "not_answered", "voicemail", "failed", "busy"}:
        return False
    return bool(activity.call_duration and activity.call_duration >= 60)


def _outcome_bucket(activity: Activity) -> str:
    disposition_bucket = _bucket_from_manual_disposition_text(activity)
    if disposition_bucket:
        return disposition_bucket
    outcome = _normalize(activity.call_outcome).replace("-", "_")
    if outcome in {"connected", "answered", "completed", "success"}:
        return "connected"
    if outcome == "callback":
        return "callback"
    if outcome in {"voicemail", "left_voicemail"}:
        return "voicemail"
    # "attempted" is the manual-call FE's default state — the rep dialed and
    # didn't connect. Semantically equivalent to no-answer/missed for the
    # report, so it counts in the "No answer" column instead of falling into
    # "Unknown" and triggering the misleading "many calls missing outcome"
    # flag every day.
    if outcome in {"missed", "no_answer", "not_answered", "busy", "attempted"}:
        return "not_answered"
    if outcome == "failed":
        return "failed"
    return "unknown"


def _is_meeting_booked_call(activity: Activity) -> bool:
    """True when a logged call's disposition indicates a booked meeting/demo.

    Activities have no structured disposition column — for manual CRM calls the
    rep's chosen disposition is the leading label of the activity content (the
    same signal _bucket_from_manual_disposition_text relies on). The two
    booked-outcome labels are "demo scheduled/booked" and "meeting confirmed"
    (mirrors call_disposition_ai's demo_scheduled_booked / meeting_confirmed).
    The call_outcome check is defensive in case a future path stores the
    structured value there.
    """
    text = _normalize(activity.content)
    if _normalize(activity.source) == "manual" and text.startswith(
        ("demo scheduled/booked", "meeting confirmed")
    ):
        return True
    outcome = _normalize(activity.call_outcome).replace("-", "_")
    return outcome in {"demo_scheduled_booked", "meeting_confirmed"}


_OUTCOME_LABELS = {
    "connected": "Connected",
    "callback": "Callback",
    "voicemail": "Voicemail",
    "not_answered": "No answer",
    "failed": "Rejected",
    "unknown": "Unknown",
}


def _outcome_label(activity: Activity) -> str:
    if _is_meeting_booked_call(activity):
        return "Mtg booked"
    return _OUTCOME_LABELS.get(_outcome_bucket(activity), "Unknown")


async def _build_call_detail_rows(
    session: AsyncSession, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Enrich raw per-call rows with contact/company/meeting names.

    One batched query per lookup table (contacts, companies, meetings) rather
    than N+1 per call — the daily call volume across a pod can be 50-100+.
    """
    if not candidates:
        return []

    contact_ids = {c["contact_id"] for c in candidates if c["contact_id"]}
    deal_ids = {c["deal_id"] for c in candidates if c["deal_id"]}

    contacts_by_id: dict[UUID, Contact] = {}
    if contact_ids:
        contacts = (
            await session.execute(select(Contact).where(Contact.id.in_(contact_ids)))
        ).scalars().all()
        contacts_by_id = {c.id: c for c in contacts}

    company_ids = {c.company_id for c in contacts_by_id.values() if c.company_id}
    companies_by_id: dict[UUID, Company] = {}
    if company_ids:
        companies = (
            await session.execute(select(Company).where(Company.id.in_(company_ids)))
        ).scalars().all()
        companies_by_id = {c.id: c for c in companies}

    # For "Mtg booked" calls, show the deal's next scheduled meeting — the one
    # the call presumably just booked. Picks the earliest upcoming meeting per
    # deal; falls back to the most recent past one if nothing is upcoming.
    meeting_by_deal: dict[UUID, datetime] = {}
    booked_deal_ids = {c["deal_id"] for c in candidates if c["deal_id"] and c["is_meeting_booked"]}
    if booked_deal_ids:
        meetings = (
            await session.execute(
                select(Meeting)
                .where(Meeting.deal_id.in_(booked_deal_ids), Meeting.scheduled_at.is_not(None))
                .order_by(Meeting.scheduled_at.asc())
            )
        ).scalars().all()
        now = datetime.utcnow()
        for meeting in meetings:
            existing = meeting_by_deal.get(meeting.deal_id)
            if existing is None:
                meeting_by_deal[meeting.deal_id] = meeting.scheduled_at
                continue
            # Prefer the first upcoming meeting found; otherwise keep whichever
            # is closest to now among past meetings already seen.
            if meeting.scheduled_at >= now and existing < now:
                meeting_by_deal[meeting.deal_id] = meeting.scheduled_at

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        contact = contacts_by_id.get(candidate["contact_id"]) if candidate["contact_id"] else None
        company = companies_by_id.get(contact.company_id) if contact and contact.company_id else None
        meeting_at = meeting_by_deal.get(candidate["deal_id"]) if candidate["deal_id"] else None
        rows.append(
            {
                "rep_name": candidate["rep_name"],
                "company_name": company.name if company else "—",
                "prospect_name": f"{contact.first_name} {contact.last_name}".strip() if contact else "—",
                "designation": (contact.title if contact and contact.title else "—"),
                "outcome_label": candidate["outcome_label"],
                "meeting_date": meeting_at.strftime("%b %d, %I:%M %p") if meeting_at else "—",
                "created_at": candidate["created_at"],
            }
        )

    rows.sort(key=lambda r: r["created_at"])
    return rows


async def _resolve_reps(session: AsyncSession, reps: list[dict] | None = None) -> list[ResolvedRep]:
    roster = reps if reps is not None else US_POD_REPS
    users = (
        await session.execute(select(User).where(User.is_active == True))  # noqa: E712
    ).scalars().all()
    by_email = {_normalize(user.email): user for user in users}
    by_name = {_normalize(user.name): user for user in users}

    resolved: list[ResolvedRep] = []
    for rep in roster:
        expected_email = _normalize(rep["email"])
        user = by_email.get(expected_email)
        if not user:
            for alias in rep["aliases"]:
                alias_norm = _normalize(alias)
                user = next(
                    (
                        candidate
                        for name, candidate in by_name.items()
                        if name.startswith(alias_norm) or alias_norm in name
                    ),
                    None,
                )
                if user:
                    break
        resolved.append(
            ResolvedRep(
                name=str(rep["name"]),
                email=str(rep["email"]),
                user_id=user.id if user else None,
                user_email=user.email if user else None,
                matched=user is not None,
            )
        )
    return resolved


def _activity_rep_id(
    activity: Activity,
    *,
    rep_ids: set[UUID],
    rep_ids_by_aircall_name: dict[str, UUID],
    deal_owner: dict[UUID, UUID | None],
    contact_owner: dict[UUID, UUID | None],
) -> UUID | None:
    source = _normalize(activity.source)
    medium = _normalize(activity.medium)
    kind = _normalize(activity.type)
    if (
        activity.created_by_id in rep_ids
        and source == "manual"
        and (medium in {"call", "linkedin"} or kind in {"call", "linkedin"})
    ):
        return activity.created_by_id

    if activity.created_by_id in rep_ids:
        return activity.created_by_id

    aircall_name = _normalize(activity.aircall_user_name)
    if aircall_name:
        direct_match = rep_ids_by_aircall_name.get(aircall_name)
        if direct_match:
            return direct_match
        for rep_name, rep_id in rep_ids_by_aircall_name.items():
            if rep_name and (rep_name in aircall_name or aircall_name in rep_name):
                return rep_id

    if activity.deal_id and deal_owner.get(activity.deal_id) in rep_ids:
        return deal_owner.get(activity.deal_id)
    if activity.contact_id and contact_owner.get(activity.contact_id) in rep_ids:
        return contact_owner.get(activity.contact_id)
    return activity.created_by_id if activity.created_by_id in rep_ids else None


async def _load_owner_maps(
    session: AsyncSession,
    activities: list[Activity],
) -> tuple[dict[UUID, UUID | None], dict[UUID, UUID | None]]:
    deal_ids = {activity.deal_id for activity in activities if activity.deal_id}
    contact_ids = {activity.contact_id for activity in activities if activity.contact_id}

    deal_owner: dict[UUID, UUID | None] = {}
    if deal_ids:
        deal_rows = (
            await session.execute(select(Deal.id, Deal.assigned_to_id).where(Deal.id.in_(deal_ids)))
        ).all()
        deal_owner = {row.id: row.assigned_to_id for row in deal_rows}

    contact_owner: dict[UUID, UUID | None] = {}
    if contact_ids:
        contact_rows = (
            await session.execute(
                select(Contact.id, Contact.assigned_to_id).where(Contact.id.in_(contact_ids))
            )
        ).all()
        contact_owner = {row.id: row.assigned_to_id for row in contact_rows}

    return deal_owner, contact_owner


async def build_us_pod_call_report(
    session: AsyncSession,
    report_date: date | None = None,
    report_settings: dict[str, Any] | None = None,
    reps: list[dict] | None = None,
) -> dict[str, Any]:
    config = normalize_sales_report_settings(report_settings or await load_sales_report_settings(session))
    target_date = report_date or default_report_date(report_settings=config)
    return await _build_us_pod_call_report_for_period(
        session,
        period_start=target_date,
        period_end=target_date,
        report_type="daily",
        report_settings=config,
        reps=reps,
    )


async def build_us_pod_weekly_call_report(
    session: AsyncSession,
    report_date: date | None = None,
    report_settings: dict[str, Any] | None = None,
    reps: list[dict] | None = None,
) -> dict[str, Any]:
    config = normalize_sales_report_settings(report_settings or await load_sales_report_settings(session))
    period_start, period_end = weekly_report_period(report_date, report_settings=config)
    return await _build_us_pod_call_report_for_period(
        session,
        period_start=period_start,
        period_end=period_end,
        report_type="weekly",
        report_settings=config,
        reps=reps,
    )


async def _build_us_pod_call_report_for_period(
    session: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    report_type: str,
    report_settings: dict[str, Any] | None = None,
    reps: list[dict] | None = None,
) -> dict[str, Any]:
    config = normalize_sales_report_settings(report_settings)
    target_date = period_end
    start_date = target_date - timedelta(days=LOOKBACK_DAYS - 1)
    query_start_date = min(start_date, period_start)
    start_utc, _ = _utc_bounds_for_report_day(query_start_date, config)
    _, end_utc = _utc_bounds_for_report_day(target_date, config)
    # Period (report-day) window, used to sum talk time from call recordings.
    period_start_utc, _ = _utc_bounds_for_report_day(period_start, config)
    _, period_end_utc = _utc_bounds_for_report_day(period_end, config)

    reps = await _resolve_reps(session, reps)
    rep_ids = {rep.user_id for rep in reps if rep.user_id}
    rep_ids_by_aircall_name = {
        _normalize(rep.name): rep.user_id
        for rep in reps
        if rep.user_id
    }

    activities = (
        await session.execute(
            select(Activity)
            .where(
                or_(
                    func.lower(Activity.type) == "call",
                    func.lower(Activity.medium) == "call",
                ),
                Activity.created_at >= start_utc,
                Activity.created_at < end_utc,
            )
            .order_by(Activity.created_at.asc())
        )
    ).scalars().all()

    deal_owner, contact_owner = await _load_owner_maps(session, activities)
    daily_counts: dict[UUID, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    target_metrics: dict[UUID, dict[str, Any]] = {}
    rep_name_by_id: dict[UUID, str] = {rep.user_id: rep.name for rep in reps if rep.user_id}
    # Raw per-call rows for the "Call details" section — filled in below during
    # the same activity loop, then enriched with contact/company/meeting data
    # in one batch after the loop (avoids N+1 queries).
    call_detail_candidates: list[dict[str, Any]] = []

    for rep in reps:
        if rep.user_id:
            target_metrics[rep.user_id] = {
                "calls": 0,
                "connected_calls": 0,
                "voicemail": 0,
                "not_answered": 0,
                "callback": 0,
                "failed": 0,
                "unknown_outcome": 0,
                "meetings_booked_calls": 0,
                "duration_seconds": 0,
                "unique_contacts": set(),
                "unique_deals": set(),
            }

    for activity in activities:
        rep_id = _activity_rep_id(
            activity,
            rep_ids=rep_ids,
            rep_ids_by_aircall_name=rep_ids_by_aircall_name,
            deal_owner=deal_owner,
            contact_owner=contact_owner,
        )
        if not rep_id or rep_id not in rep_ids:
            continue

        activity_day = _activity_report_date(activity, config)
        if start_date <= activity_day <= target_date:
            daily_counts[rep_id][activity_day] += 1

        if not (period_start <= activity_day <= period_end):
            continue

        metrics = target_metrics[rep_id]
        metrics["calls"] += 1
        metrics["duration_seconds"] += activity.call_duration or 0
        if activity.contact_id:
            metrics["unique_contacts"].add(activity.contact_id)
        if activity.deal_id:
            metrics["unique_deals"].add(activity.deal_id)

        call_detail_candidates.append(
            {
                "rep_name": rep_name_by_id.get(rep_id, "Unknown"),
                "contact_id": activity.contact_id,
                "deal_id": activity.deal_id,
                "outcome_label": _outcome_label(activity),
                "is_meeting_booked": _is_meeting_booked_call(activity),
                "created_at": activity.created_at,
            }
        )

        bucket = _outcome_bucket(activity)
        if bucket == "connected" or _is_connected_call(activity):
            metrics["connected_calls"] += 1
        if bucket == "callback":
            metrics["callback"] += 1
        elif bucket == "voicemail":
            metrics["voicemail"] += 1
        elif bucket == "not_answered":
            metrics["not_answered"] += 1
        elif bucket == "failed":
            metrics["failed"] += 1
        elif bucket == "unknown":
            metrics["unknown_outcome"] += 1

        if _is_meeting_booked_call(activity):
            metrics["meetings_booked_calls"] += 1

    # Talk time = duration of the call recordings reps attach to manual calls
    # (CallRecording.audio_duration_seconds), summed per recorder over the report
    # period. activity.call_duration is only set by the Aircall sync, which this
    # team doesn't use, so it's always null — the recording is the real signal.
    talk_secs_by_rep: dict[UUID, int] = {}
    rec_rows = (
        await session.execute(
            select(
                CallRecording.created_by_id,
                func.sum(CallRecording.audio_duration_seconds),
            )
            .where(
                CallRecording.created_at >= period_start_utc,
                CallRecording.created_at < period_end_utc,
                CallRecording.deleted_at.is_(None),
            )
            .group_by(CallRecording.created_by_id)
        )
    ).all()
    for rec_user_id, secs in rec_rows:
        if rec_user_id:
            talk_secs_by_rep[rec_user_id] = int(secs or 0)

    rows: list[dict[str, Any]] = []
    for rep in reps:
        metrics = target_metrics.get(rep.user_id) if rep.user_id else None
        day_counts = daily_counts.get(rep.user_id, {}) if rep.user_id else {}
        # Average over *working* days only. Any contiguous 7-day window always
        # contains exactly 5 weekdays (Mon-Fri) and 2 weekend days; the team
        # doesn't call on Sat/Sun, so including those two zeros drags every
        # rep's avg down ~30% and breaks the "below half average" flag. We
        # sum only the weekday slots and divide by their count — that count
        # is 5 for the standard 7-day lookback and stays correct if anyone
        # changes LOOKBACK_DAYS later.
        working_day_calls = [
            day_counts.get(start_date + timedelta(days=offset), 0)
            for offset in range(LOOKBACK_DAYS)
            if (start_date + timedelta(days=offset)).weekday() < 5
        ]
        working_day_count = len(working_day_calls) or 1
        total_5d = sum(working_day_calls)
        avg_5d = round(total_5d / working_day_count, 1)
        calls = int(metrics["calls"]) if metrics else 0
        flags: list[str] = []
        if not rep.matched:
            flags.append("user not found")
        if calls == 0:
            flags.append("0 calls logged")
        elif avg_5d > 0 and calls < avg_5d * 0.5:
            flags.append("below 50% of working-day average")
        if metrics and metrics["unknown_outcome"] > max(2, calls // 2):
            flags.append("many calls missing outcome")

        rows.append(
            {
                "rep_name": rep.name,
                "rep_email": rep.email,
                "user_id": str(rep.user_id) if rep.user_id else None,
                "matched_user_email": rep.user_email,
                "calls": calls,
                "connected_calls": int(metrics["connected_calls"]) if metrics else 0,
                "voicemail": int(metrics["voicemail"]) if metrics else 0,
                "not_answered": int(metrics["not_answered"]) if metrics else 0,
                "callback": int(metrics["callback"]) if metrics else 0,
                "failed": int(metrics["failed"]) if metrics else 0,
                "unknown_outcome": int(metrics["unknown_outcome"]) if metrics else 0,
                "meetings_booked_calls": int(metrics["meetings_booked_calls"]) if metrics else 0,
                "duration_minutes": round(talk_secs_by_rep.get(rep.user_id, 0) / 60, 1) if rep.user_id else 0.0,
                "unique_contacts": len(metrics["unique_contacts"]) if metrics else 0,
                "unique_deals": len(metrics["unique_deals"]) if metrics else 0,
                # Field name preserved for downstream/email-template compat,
                # but the value is now the working-day (Mon-Fri) average.
                "avg_calls_last_7_days": avg_5d,
                "flags": flags,
            }
        )

    # "Call details" is scoped to booked meetings only — the summary table above
    # still counts every call; this section is a spotlight on the calls that
    # actually converted, not a full per-call log.
    meeting_booked_candidates = [c for c in call_detail_candidates if c["is_meeting_booked"]]
    call_details = await _build_call_detail_rows(session, meeting_booked_candidates)

    report = {
        "report_type": report_type,
        "report_date": target_date.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "timezone": (
            f"{config['cutoff_timezone']} cutoff at {int(config['cutoff_hour']):02d}:00; "
            f"report day labeled in {config['report_label_timezone']}"
        ),
        "lookback_days": LOOKBACK_DAYS,
        "recipients": config["recipients"],
        "rows": rows,
        "call_details": call_details,
    }
    report["subject"] = _report_subject(report)
    report["body"] = _render_report_text(report)
    report["html_body"] = _render_report_html(report)
    return report


def _report_subject(report: dict[str, Any]) -> str:
    pod = report.get("pod_label", "US Pod")
    if report.get("report_type") == "weekly":
        return f"{pod} Weekly Call Report - {report['period_start']} to {report['period_end']}"
    return f"{pod} Daily Call Report - {report['report_date']}"


def _report_title(report: dict[str, Any]) -> str:
    pod = report.get("pod_label", "US Pod")
    if report.get("report_type") == "weekly":
        return f"{pod} Weekly Call Report - {report['period_start']} to {report['period_end']}"
    return f"{pod} Daily Call Report - {report['report_date']}"


def _render_report_text(report: dict[str, Any]) -> str:
    lines = [
        _report_title(report),
        f"Reporting timezone: {report['timezone']}",
        "",
        "Rep                 Calls  Connected  Mtg booked  No answer  Callback  Rejected  5d avg  Talk min  Contacts  Flags",
        "------------------  -----  ---------  ----------  ---------  --------  --------  ------  --------  --------  -----",
    ]
    for row in report["rows"]:
        flags = ", ".join(row["flags"]) if row["flags"] else "-"
        lines.append(
            f"{row['rep_name'][:18]:18}  "
            f"{row['calls']:>5}  "
            f"{row['connected_calls']:>9}  "
            f"{row['meetings_booked_calls']:>10}  "
            f"{row['not_answered']:>9}  "
            f"{row['callback']:>8}  "
            f"{row['failed']:>8}  "
            f"{row['avg_calls_last_7_days']:>6}  "
            f"{row['duration_minutes']:>8}  "
            f"{row['unique_contacts']:>8}  "
            f"{flags}"
        )

    call_details = report.get("call_details") or []
    if call_details:
        lines.extend(
            [
                "",
                "Call details:",
                "Rep            Company              Prospect             Designation          Outcome      Meeting date",
                "-------------  -------------------  -------------------  -------------------  -----------  ----------------",
            ]
        )
        for row in call_details:
            lines.append(
                f"{row['rep_name'][:13]:13}  "
                f"{row['company_name'][:19]:19}  "
                f"{row['prospect_name'][:19]:19}  "
                f"{row['designation'][:19]:19}  "
                f"{row['outcome_label'][:11]:11}  "
                f"{row['meeting_date']}"
            )

    lines.extend(
        [
            "",
            "Counting logic:",
            "- Includes activities where type or medium is call.",
            "- Credits manual CRM calls to the user who logged the activity, matching Sales Analytics.",
            "- Credits Aircall calls by Aircall user name when available, then deal/contact owner fallback.",
            "- Mtg booked: calls logged with disposition 'demo scheduled/booked' or 'meeting confirmed'.",
            "- Rejected: calls that did not connect (carrier/line failure or declined).",
            "- Flags: per-rep exceptions to act on — '0 calls logged', 'below 50% of working-day average', or 'many calls missing outcome'. Blank (-) means no issues that day.",
            f"- Uses {report['timezone']} so the report matches the configured US pod business day.",
        ]
    )
    return "\n".join(lines)


def _render_report_html(report: dict[str, Any]) -> str:
    """Render the report as an HTML table.

    The plain-text version uses fixed-width column padding which Gmail's
    mobile clients render with a proportional font, collapsing the columns
    into a wall of digits (per Sarthak's screenshot 2026-05-07). HTML tables
    survive that environment because alignment is structural, not visual.
    """
    headers = [
        "Rep", "Calls", "Connected", "Mtg booked",
        "No answer", "Callback", "Rejected",
        "5d avg", "Talk min", "Contacts", "Flags",
    ]

    def _grid_th(label: str, *, last: bool, align: str = "left") -> str:
        border_right = "" if last else "border-right:1px solid #e5e3dc;"
        return (
            f'<th style="text-align:{align};padding:12px 14px;color:#94a3b8;font-weight:500;'
            f'font-size:11px;{border_right}border-bottom:1px solid #e5e3dc;">{html.escape(label)}</th>'
        )

    def _grid_td(value, *, last: bool, align: str = "left", color: str = "#1f2a37", size: str = "12.5px") -> str:
        border_right = "" if last else "border-right:1px solid #eeece5;"
        return (
            f'<td style="padding:12px 14px;text-align:{align};color:{color};font-size:{size};'
            f'{border_right}border-bottom:1px solid #eeece5;">{html.escape(str(value))}</td>'
        )

    header_html = "".join(
        _grid_th(h, last=(i == len(headers) - 1), align="left" if i == 0 else "right" if i < len(headers) - 1 else "left")
        for i, h in enumerate(headers)
    )
    rows_html = []
    for row in report["rows"]:
        flags = ", ".join(row["flags"]) if row["flags"] else "—"
        flags_color = "#B45252" if row["flags"] else "#cbd5e1"
        cells_html = (
            _grid_td(row["rep_name"], last=False)
            + _grid_td(row["calls"], last=False, align="right")
            + _grid_td(row["connected_calls"], last=False, align="right")
            + _grid_td(row["meetings_booked_calls"], last=False, align="right")
            + _grid_td(row["not_answered"], last=False, align="right")
            + _grid_td(row["callback"], last=False, align="right")
            + _grid_td(row["failed"], last=False, align="right")
            + _grid_td(row["avg_calls_last_7_days"], last=False, align="right")
            + _grid_td(row["duration_minutes"], last=False, align="right")
            + _grid_td(row["unique_contacts"], last=False, align="right")
            + _grid_td(flags, last=True, color=flags_color, size="11.5px")
        )
        rows_html.append(f"<tr>{cells_html}</tr>")

    call_details = report.get("call_details") or []
    call_details_html = ""
    if call_details:
        detail_headers = ["Rep", "Company", "Prospect", "Designation", "Meeting date"]
        detail_header_html = "".join(
            _grid_th(h, last=(i == len(detail_headers) - 1)) for i, h in enumerate(detail_headers)
        )
        detail_rows_html = []
        for row in call_details:
            meeting_date = row["meeting_date"]
            if meeting_date == "—":
                meeting_date_html = (
                    '<span style="font-size:11px;padding:2px 9px;border-radius:999px;'
                    'background:#F1EFE8;color:#5F5E5A;">Pending</span>'
                )
            else:
                meeting_date_html = html.escape(meeting_date)
            cells_html = (
                _grid_td(row["rep_name"], last=False)
                + _grid_td(row["company_name"], last=False)
                + _grid_td(row["prospect_name"], last=False)
                + _grid_td(row["designation"], last=False, color="#6b7280")
                + f'<td style="padding:12px 14px;border-bottom:1px solid #eeece5;">{meeting_date_html}</td>'
            )
            detail_rows_html.append(f"<tr>{cells_html}</tr>")
        call_details_html = f"""
    <div style="margin:28px 0 12px 0;display:flex;align-items:center;gap:8px;">
      <span style="font-size:14px;font-weight:500;color:#1f2a37;">Meetings booked</span>
      <span style="font-size:11px;padding:2px 9px;border-radius:999px;background:#EEEDFE;color:#3C3489;font-weight:500;">{len(call_details)}</span>
    </div>
    <div style="border:1px solid #e5e3dc;border-radius:14px;overflow:hidden;">
    <table style="border-collapse:collapse;width:100%;background:#fff;">
      <thead><tr style="background:#fafaf8;">{detail_header_html}</tr></thead>
      <tbody>{''.join(detail_rows_html)}</tbody>
    </table>
    </div>
    """

    return f"""
    <h2 style="margin:0 0 6px 0;font-size:17px;font-weight:500;color:#1f2a37;">{html.escape(_report_title(report))}</h2>
    <p style="margin:0 0 20px 0;color:#94a3b8;font-size:12px;">Reporting timezone: {html.escape(report['timezone'])}</p>
    <div style="border:1px solid #e5e3dc;border-radius:14px;overflow:hidden;">
    <table style="border-collapse:collapse;width:100%;background:#fff;">
      <thead><tr style="background:#fafaf8;">{header_html}</tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    </div>
    {call_details_html}
    <h3 style="margin:24px 0 6px 0;font-size:13px;color:#475569;text-transform:uppercase;letter-spacing:0.04em;">Counting logic</h3>
    <ul style="margin:0;padding-left:20px;color:#475569;font-size:13px;line-height:1.6;">
      <li>Includes activities where type or medium is <code>call</code>.</li>
      <li>Credits manual CRM calls to the user who logged the activity, matching Sales Analytics.</li>
      <li>Credits Aircall calls by Aircall user name when available, then deal/contact owner fallback.</li>
      <li><strong>Mtg booked</strong>: calls logged with disposition &ldquo;demo scheduled/booked&rdquo; or &ldquo;meeting confirmed&rdquo;.</li>
      <li><strong>Rejected</strong>: calls that did not connect (carrier/line failure or declined).</li>
      <li><strong>Flags</strong>: per-rep exceptions to act on &mdash; &ldquo;0 calls logged&rdquo;, &ldquo;below 50% of working-day average&rdquo;, or &ldquo;many calls missing outcome&rdquo;. A dash (&mdash;) means no issues that day.</li>
      <li>Uses {html.escape(report['timezone'])} so the report matches the configured US pod business day.</li>
    </ul>
    """.strip()


async def send_us_pod_call_report_email(
    session: AsyncSession,
    report_date: date | None = None,
    *,
    report_type: str = "daily",
    recipients: list[str] | None = None,
    reps: list[dict] | None = None,
    config_key: str = "sales_report",
    config_defaults: dict | None = None,
    pod_label: str = "US Pod",
) -> dict[str, Any]:
    report_settings = await load_sales_report_settings(session, key=config_key, defaults=config_defaults)
    if report_type == "weekly":
        report = await build_us_pod_weekly_call_report(session, report_date, report_settings=report_settings, reps=reps)
    else:
        report = await build_us_pod_call_report(session, report_date, report_settings=report_settings, reps=reps)
    report["pod_label"] = pod_label
    # Re-stamp subject/body now that pod_label is known (build defaults to US Pod).
    report["subject"] = _report_subject(report)
    report["body"] = _render_report_text(report)
    report["html_body"] = _render_report_html(report)
    safe_recipients, blocked_recipients = _resolve_report_recipients(recipients, report_settings)
    report["recipients"] = safe_recipients
    if blocked_recipients:
        report["blocked_recipients"] = blocked_recipients
    if not safe_recipients:
        report["send_results"] = [
            {
                "status": "blocked",
                "error": "Non-production report recipient is not in the allowed recipient list.",
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
        report["send_results"] = [
            {
                "status": "not_configured",
                "error": "Report sender Gmail account is not connected in Settings.",
            }
        ]
        return report

    if settings_row.report_sender_email.lower() != settings_row.report_sender_connected_email.lower():
        report["send_results"] = [
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
    # Attempt every recipient. Earlier this loop did `break` on the first
    # non-"sent" result, which meant a single transient Gmail hiccup
    # silently skipped every later recipient (Pulkit was at position 6 of
    # 7 — incident on 2026-05-22). Now we attempt each independently and
    # accumulate any errors so the operator gets one combined report,
    # while still letting the caller decide whether to commit the
    # scheduled send_key based on whether every recipient succeeded.
    failures: list[str] = []
    for recipient in report["recipients"]:
        try:
            result, token_data = await send_gmail_email(
                token_data=token_data,
                from_email=settings_row.report_sender_email,
                to=recipient,
                subject=report["subject"],
                body=report["body"],
                html_body=report.get("html_body"),
                from_name="Beacon Sales Ops",
            )
        except Exception as exc:  # network/timeout/quota/auth — never crash the whole report
            logger.exception("Gmail send raised for %s: %s", recipient, exc)
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        send_results.append({"to": recipient, **result})
        if result.get("status") != "sent":
            failures.append(f"{recipient}: {result.get('error') or 'Gmail send failed'}")

    if token_data != settings_row.report_sender_token_data:
        settings_row.report_sender_token_data = token_data
    if failures:
        # Cap the combined error so it fits the 500-char column. Multi-line
        # so the UI/log can show each failure on its own line.
        joined = " | ".join(failures)
        settings_row.report_sender_last_error = joined[:500]
    else:
        settings_row.report_sender_last_error = None
    session.add(settings_row)
    await session.commit()

    report["send_results"] = send_results
    sent_ok = sum(1 for r in send_results if r.get("status") == "sent")
    logger.info(
        "US pod call report attempted for %s: %d/%d recipients delivered",
        report["report_date"], sent_ok, len(send_results),
    )
    return report
