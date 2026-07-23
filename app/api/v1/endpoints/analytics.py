from __future__ import annotations

import time
import re
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.core.analytics_defaults import DEFAULT_STAGE_PROBABILITIES
from app.core.dependencies import CurrentUser, DBSession
from app.models.activity import Activity
from app.models.company import Company
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.deal_stage_history import DealStageHistory
from app.models.meeting import Meeting
from app.models.user import User
from app.models.user_alias import UserAlias
from app.services.analytics_settings import get_analytics_settings
from app.services.company_stage_milestones import MILESTONE_LABELS, backfill_company_stage_milestones
from app.services.deal_stages import get_configured_deal_stages

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _utcnow() -> datetime:
    """Naive UTC now. The DB stores naive-UTC timestamps, so we keep comparisons
    naive (mixing tz-aware and naive datetimes raises). This replaces the
    deprecated stdlib ``utcnow()`` without changing window-boundary semantics."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Short-TTL snapshot cache for the sales dashboard. The dashboard is explicitly a
# point-in-time "snapshot" (it surfaces `generated_at`), and the identical query
# fires repeatedly from polling / re-renders. A few-second cache collapses those
# repeated full recomputes — each of which scans all deals, contacts, in-window
# activities and meetings — without making the numbers misleadingly stale.
_DASHBOARD_CACHE: dict[tuple, tuple[float, "SalesDashboardRead"]] = {}
_DASHBOARD_CACHE_TTL_SECONDS = 45.0


def _dashboard_cache_get(key: tuple):
    hit = _DASHBOARD_CACHE.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < _DASHBOARD_CACHE_TTL_SECONDS:
        return hit[1]
    return None


def _dashboard_cache_set(key: tuple, value: "SalesDashboardRead") -> None:
    _DASHBOARD_CACHE[key] = (time.monotonic(), value)
    if len(_DASHBOARD_CACHE) > 256:  # opportunistic eviction of expired entries
        now = time.monotonic()
        for stale in [k for k, (ts, _) in _DASHBOARD_CACHE.items() if now - ts >= _DASHBOARD_CACHE_TTL_SECONDS]:
            _DASHBOARD_CACHE.pop(stale, None)


def _dashboard_cache_clear() -> None:
    _DASHBOARD_CACHE.clear()

PROPOSAL_STAGES = {"poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop"}
HOT_MEETING_MARKERS = {"meeting_booked", "call booked", "demo booked"}
REAL_MEETING_SOURCES = {"", "google_calendar", "tldv", "manual"}

# Roles that count as a sales rep in activity analytics. Admins (and any other
# role) are NOT reps — their emails/calls/meetings must not inflate rep metrics
# or appear as a rep row. User.role is one of: admin | ae | sdr.
REP_ROLES = {"ae", "sdr"}
DEFAULT_SALES_ANALYTICS_EMAILS = {"jacob@beacon.li"}


def _sales_analytics_rep_user_ids(user_rows, analytics_settings: dict | None) -> set[UUID]:
    """AE/SDR users plus configured active users who should appear as reps."""
    config = analytics_settings or {}
    active_user_rows = [row for row in user_rows if getattr(row, "is_active", True) is not False]
    ids = {
        row.id
        for row in active_user_rows
        if str(row.role or "").strip().lower() in REP_ROLES
    }
    active_ids_by_str = {str(row.id): row.id for row in active_user_rows}
    for value in config.get("sales_analytics_user_ids") or []:
        user_id = active_ids_by_str.get(str(value or "").strip())
        if user_id:
            ids.add(user_id)
    if not config.get("sales_analytics_roster_configured"):
        default_emails = {
            str(email or "").strip().lower()
            for email in (config.get("sales_analytics_default_emails") or DEFAULT_SALES_ANALYTICS_EMAILS)
            if str(email or "").strip()
        } or DEFAULT_SALES_ANALYTICS_EMAILS
        active_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in active_user_rows if row.email}
        for email in default_emails:
            user_id = active_ids_by_email.get(email)
            if user_id:
                ids.add(user_id)
    return ids


def _sales_analytics_seed_user_ids(user_rows, analytics_settings: dict | None) -> set[UUID]:
    """Configured/default users who should get a visible zero row if needed."""
    config = analytics_settings or {}
    active_user_rows = [row for row in user_rows if getattr(row, "is_active", True) is not False]
    ids = set()
    active_ids_by_str = {str(row.id): row.id for row in active_user_rows}
    for value in config.get("sales_analytics_user_ids") or []:
        user_id = active_ids_by_str.get(str(value or "").strip())
        if user_id:
            ids.add(user_id)
    if not config.get("sales_analytics_roster_configured"):
        default_emails = {
            str(email or "").strip().lower()
            for email in (config.get("sales_analytics_default_emails") or DEFAULT_SALES_ANALYTICS_EMAILS)
            if str(email or "").strip()
        } or DEFAULT_SALES_ANALYTICS_EMAILS
        active_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in active_user_rows if row.email}
        for email in default_emails:
            user_id = active_ids_by_email.get(email)
            if user_id:
                ids.add(user_id)
    return ids

# A demo "converts" when its account reaches a qualified opportunity or beyond.
# Lost/dead stages (closed_lost, not_a_fit, churned, cold, on_hold, nurture) are
# explicitly NOT conversions. Used for the SDR demo funnel.
CONVERTED_DEAL_STAGES = {
    "qualified_lead", "poc_agreed", "poc_wip", "poc_done",
    "commercial_negotiation", "msa_review", "workshop", "closed_won",
}

# Canonical account-sourcing status values + display labels. Kept in lockstep
# with ACCOUNT_STATUS_VALUES in app/models/company.py and the frontend control.
# Insertion order drives the "Accounts by Status" breakdown order on the
# dashboard; keep it in lockstep with frontend/src/lib/accountStatus.ts.
ACCOUNT_STATUS_LABELS: dict[str, str] = {
    "cold": "Cold",
    "in_progress": "In Progress",
    "meeting_booked": "Meeting Booked",
    "meeting_done": "Meeting Done",
    "in_pipeline": "In Pipeline",
    "not_a_fit": "Not a Fit",
    "dnd": "DND",
    "reach_out_later": "Reach Out Later",
}


def _is_rep(rep_id, rep_user_ids) -> bool:
    """True if this attributed id should count as rep activity.

    None (Unassigned) is preserved as-is — it's a separate existing bucket, not
    a non-rep person. Any concrete id must belong to an ae/sdr user.
    """
    return rep_id is None or rep_id in rep_user_ids

# tl;dv and Google Calendar both ingest the SAME real-world meeting as separate
# Meeting rows (different external_source). When both exist we must count it
# once. We prefer tl;dv (only fires on calls that actually happened) over
# google_calendar (can include rescheduled/no-show events) over manual.
_MEETING_SOURCE_PRIORITY = {"tldv": 0, "google_calendar": 1, "manual": 2, "": 3}

# Cross-source clock skew: tl;dv stamps `happenedAt` (real start) while Google
# Calendar stamps the scheduled time, so the "same" meeting can differ by a
# minute or two (e.g. 4:30 vs 4:31). Match within this window rather than on an
# exact-minute key, which silently double-counted skewed pairs.
_MEETING_DEDUP_TOLERANCE_SECONDS = 5 * 60


def _meeting_entity_key(row):
    """Entity a meeting belongs to, for cross-source grouping.

    Prefer company_id; fall back to deal_id. Deliberately does NOT use owner —
    tl;dv resolves a rep owner while the Google Calendar row of the SAME meeting
    often has owner=None, so keying on owner splits true duplicates. Nor deal_id
    when a company exists: one source may map the deal and the other only the
    company. Company + time-proximity is the reliable shared signal.
    """
    if row.company_id is not None:
        return ("company", row.company_id)
    if row.deal_id is not None:
        return ("deal", row.deal_id)
    # No company/deal anchor — never cluster these with each other (they share
    # no entity). Callers already exclude such meetings, but key on the row's
    # own id so an unanchored row can only ever be its own singleton group.
    return ("meeting", row.id)


def _dedupe_meetings_across_sources(rows) -> list:
    """Collapse cross-source duplicates of the same real meeting to one row.

    Groups by entity (company, falling back to deal) and, within each group,
    clusters rows whose scheduled_at falls within the tolerance window — so a
    tl;dv row and a Google Calendar row a minute apart count once even when
    their owners differ. The tl;dv row wins via `_MEETING_SOURCE_PRIORITY`.
    Returns one row per real meeting; order is not guaranteed (callers sort).
    """
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[_meeting_entity_key(row)].append(row)

    kept: list = []
    for group in groups.values():
        # Sort by time (None last) so within-tolerance rows sit adjacent.
        group.sort(key=lambda r: (r.scheduled_at is None, r.scheduled_at or datetime.min))
        clusters: list[dict] = []
        for row in group:
            t = row.scheduled_at
            placed = False
            for cluster in clusters:
                anchor = cluster["anchor"]
                if t is not None and anchor is not None:
                    if abs((t - anchor).total_seconds()) <= _MEETING_DEDUP_TOLERANCE_SECONDS:
                        cluster["rows"].append(row)
                        placed = True
                        break
                elif t is None and anchor is None:
                    cluster["rows"].append(row)
                    placed = True
                    break
            if not placed:
                clusters.append({"anchor": t, "rows": [row]})
        for cluster in clusters:
            kept.append(
                min(
                    cluster["rows"],
                    key=lambda r: _MEETING_SOURCE_PRIORITY.get(
                        str(r.external_source or "").strip().lower(), 99
                    ),
                )
            )
    return kept


class MilestoneDealRow(BaseModel):
    milestone_key: str
    deal_name: Optional[str] = None
    company_name: Optional[str] = None
    reached_at: str
    close_date_est: Optional[str] = None
    deal_value: Optional[float] = None
    assigned_ae: Optional[str] = None
    assigned_sdr: Optional[str] = None


class SalesSummary(BaseModel):
    pipeline_amount: float
    weighted_pipeline_amount: float
    forecast_amount: float
    active_deals: int
    average_deal_size: float
    overdue_close_count: int
    missing_close_date_count: int
    stale_deal_count: int
    # Milestone-based counts (deduplicated: first time per company)
    demo_scheduled_count: int = 0
    qualified_lead_count: int = 0
    demo_done_count: int = 0
    poc_agreed_count: int = 0
    poc_wip_count: int = 0
    poc_done_count: int = 0
    commercial_negotiation_count: int = 0
    workshop_msa_count: int = 0
    closed_won_count: int = 0
    closed_won_value: float = 0.0
    milestone_deals: list[MilestoneDealRow] = []
    # Same metrics for the immediately-preceding window of equal length, so the
    # UI can render period-over-period trend deltas on the milestone KPIs. These
    # are window-bound counts (point-in-time pipeline metrics are not compared).
    prev_demo_scheduled_count: int = 0
    prev_qualified_lead_count: int = 0
    prev_demo_done_count: int = 0
    prev_poc_agreed_count: int = 0
    prev_poc_wip_count: int = 0
    prev_poc_done_count: int = 0
    prev_commercial_negotiation_count: int = 0
    prev_workshop_msa_count: int = 0
    prev_closed_won_count: int = 0
    prev_closed_won_value: float = 0.0


class RepActivityRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    # "ae" | "sdr" | None. Drives the SDR/AE leaderboard split on the client.
    role: Optional[str] = None
    calls: int
    connected_calls: int = 0
    live_calls: int = 0
    emails: int
    manual_emails: int = 0
    instantly_emails: int = 0
    email_opens: int = 0
    email_replies: int = 0
    linkedin_reachouts: int = 0
    linkedin_accepted: int = 0
    linkedin_meeting_booked: int = 0
    call_meeting_booked: int = 0
    meetings: int
    total: int
    active_deals: int
    pipeline_amount: float
    # Upcoming scheduled meetings bucketed by how far ahead they are from today
    meetings_next_1w: int = 0
    meetings_next_2w: int = 0
    meetings_beyond_2w: int = 0
    # Meetings with VP/SVP/Head/Chief within the selected analytics window
    direct_sql: int = 0
    # SDR demo funnel (attributed to the account's SDR). demos_converted counts
    # done demos whose account reached a qualified deal or beyond.
    demos_scheduled: int = 0
    demos_done: int = 0
    demos_converted: int = 0
    # AE demo funnel — only deals where sdr_id == assigned_to_id (AE sourced their own deal).
    ae_demos_scheduled: int = 0
    ae_demos_done: int = 0
    ae_demos_converted: int = 0
    # Call touchpoint breakdown
    call_first_attempt: int = 0
    call_second_plus: int = 0
    # Email touchpoint breakdown
    email_first_attempt: int = 0
    email_min_3_attempts: int = 0
    # LinkedIn touchpoint breakdown
    linkedin_connection_requested: int = 0
    linkedin_intro_msg: int = 0
    linkedin_followup_msg: int = 0
    # Prospect / contact coverage
    total_prospects: int = 0
    total_mobile_numbers: int = 0


class RepActivityWeekRow(BaseModel):
    week_key: str
    label: str
    week_start: str
    week_end: str
    emails: int = 0
    manual_emails: int = 0
    instantly_emails: int = 0
    calls: int = 0
    connected_calls: int = 0
    live_calls: int = 0
    linkedin_reachouts: int = 0
    meetings: int = 0
    total: int = 0


class RepWeeklyActivityRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    active_deals: int
    pipeline_amount: float
    totals: RepActivityRow
    weeks: list[RepActivityWeekRow]


class StageBucket(BaseModel):
    key: str
    label: str
    color: str
    deal_count: int
    amount: float
    weighted_amount: float = 0


class PipelineOwnerRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    deal_count: int
    amount: float
    weighted_amount: float
    stages: list[StageBucket]


class VelocityRow(BaseModel):
    key: str
    label: str
    color: str
    deal_count: int
    average_days_in_stage: float
    stale_deals: int


class ForecastRow(BaseModel):
    key: str
    label: str
    deal_count: int
    amount: float
    weighted_amount: float


class FunnelStep(BaseModel):
    key: str
    label: str
    count: int
    conversion_from_previous: Optional[float] = None


class QuotaState(BaseModel):
    configured: bool
    title: str
    message: str


class SalesHighlightDrilldown(BaseModel):
    entity_type: Literal["deal"] = "deal"
    stage_key: Optional[str] = None
    rep_user_id: Optional[UUID] = None
    stalled_only: bool = False
    overdue_close_date: bool = False
    missing_close_date: bool = False
    close_month: Optional[str] = None


class SalesHighlight(BaseModel):
    key: str
    message: str
    title: Optional[str] = None
    subtitle: Optional[str] = None
    drilldown: Optional[SalesHighlightDrilldown] = None


class MonthlyUniqueFunnelRow(BaseModel):
    month_key: str
    label: str
    demo_done: int
    poc_agreed: int = 0
    poc_wip: int
    poc_done: int
    closed_won: int


class AccountStatusRow(BaseModel):
    key: str       # canonical status value (or "unset")
    label: str     # human label
    count: int


class SalesDashboardRead(BaseModel):
    generated_at: datetime
    window_days: int
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    summary: SalesSummary
    highlights: list[SalesHighlight]
    rep_activity: list[RepActivityRow]
    rep_weekly_activity: list[RepWeeklyActivityRow]
    pipeline_by_stage: list[StageBucket]
    pipeline_by_owner: list[PipelineOwnerRow]
    velocity_by_stage: list[VelocityRow]
    forecast_by_month: list[ForecastRow]
    forecast_by_week: list[ForecastRow] = []
    forecast_buckets: list[ForecastRow] = []
    forecast_granularity: str = "month"
    conversion_funnel: list[FunnelStep]
    monthly_unique_funnel: list[MonthlyUniqueFunnelRow]
    accounts_by_status: list[AccountStatusRow] = []
    quota: QuotaState


class SalesActivityDrilldownRow(BaseModel):
    id: UUID
    kind: Literal["activity", "meeting"]
    activity_type: str
    occurred_at: datetime
    rep_user_id: Optional[UUID] = None
    rep_name: str
    source: Optional[str] = None
    source_label: Optional[str] = None
    subject: Optional[str] = None
    direction: Optional[str] = None
    from_email: Optional[str] = None
    to_email: Optional[str] = None
    call_outcome: Optional[str] = None
    call_duration: Optional[int] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    company_name: Optional[str] = None
    deal_name: Optional[str] = None
    # Optional entity ids so a drilldown row can navigate to a deal/company.
    # Populated cheaply (pass-through of ids already on the row); we do not run
    # extra lookups just to fill these for activity rows.
    deal_id: Optional[str] = None
    company_id: Optional[str] = None
    email_body: Optional[str] = None  # full email body for expand-in-drilldown (1.2)


class SalesActivityDrilldownRead(BaseModel):
    generated_at: datetime
    metric: str
    window_days: int
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    rep_user_id: Optional[UUID] = None
    rep_name: Optional[str] = None
    returned_count: int
    has_more: bool
    limit: int
    offset: int
    rows: list[SalesActivityDrilldownRow]


def _to_float(value) -> float:
    return float(value or 0)


def _month_label(month_key: str) -> str:
    return datetime.strptime(month_key, "%Y-%m").strftime("%b %Y")


def _month_key_for_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m")


def _start_of_week(value: datetime) -> date:
    return (value.date() - timedelta(days=value.weekday()))


def _week_key(week_start: date) -> str:
    return week_start.isoformat()


def _week_label(week_start: date) -> str:
    return f"{week_start.strftime('%b')} {week_start.day}"


def _rolling_week_starts(start: datetime, end: datetime) -> list[date]:
    first_week = _start_of_week(start)
    last_week = _start_of_week(end)
    weeks: list[date] = []
    cursor = first_week
    while cursor <= last_week:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    return weeks


def _resolve_analytics_window(window_days: int, from_date: Optional[str], to_date: Optional[str]) -> tuple[datetime, datetime]:
    now = _utcnow()
    try:
        if from_date:
            window_start = datetime.fromisoformat(from_date)
        else:
            window_start = now - timedelta(days=window_days)
        if to_date:
            window_end = datetime.fromisoformat(to_date) + timedelta(days=1)
        else:
            window_end = now
    except ValueError:
        raise HTTPException(status_code=422, detail="from_date/to_date must be ISO 8601")
    return window_start, window_end


def _stage_probability(stage_id: str) -> float:
    return DEFAULT_STAGE_PROBABILITIES.get(stage_id, 0.0)


def _stage_meta(stage_map: dict[str, dict[str, str]], stage_id: str) -> dict[str, str]:
    return stage_map.get(
        stage_id,
        {
            "id": stage_id,
            "label": stage_id.replace("_", " ").title(),
            "group": "active",
            "color": "#94a3b8",
        },
    )


def _average(values: list[int]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 1)


def _conversion(previous: int, current: int) -> Optional[float]:
    if previous <= 0:
        return None
    return round((current / previous) * 100, 1)


def _label_for_rep(rep_id: UUID | None, users: dict[UUID, str]) -> tuple[str, Optional[UUID], str]:
    if rep_id and rep_id in users:
        return str(rep_id), rep_id, users[rep_id]
    return "unassigned", None, "Unassigned"


# Our outbound email-sending identities: the primary domain plus the Instantly
# cold-outreach lookalike domains. A rep shares one local-part across all of them
# (annie@beacon.li == annie@beaconli.com == annie@beaconli.co), so an outbound
# email is mapped to its rep by local-part, not by the full address.
BEACON_SENDING_DOMAINS = {"beacon.li", "beaconli.co", "beaconli.com"}

# Instantly cold-outreach domains — emails from these always count as Emails Out.
INSTANTLY_DOMAINS = {"beaconli.co", "beaconli.com"}
EMAIL_SEND_METRICS = {"emails", "manual_emails", "instantly_emails"}

# All known Zippy addresses across every sending domain.
ZIPPY_ADDRS = {"zippy@beacon.li", "zippy@beaconli.com", "zippy@beaconli.co"}
_EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}")
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fw|fwd)\s*:\s*", re.IGNORECASE)


def _zippy_in_cc_or_bcc(row) -> bool:
    """True if any Zippy address appears in email_cc or email_bcc of the row."""
    for field_val in (getattr(row, "email_cc", None), getattr(row, "email_bcc", None)):
        for addr in str(field_val or "").lower().split(","):
            if addr.strip() in ZIPPY_ADDRS:
                return True
    return False


def _email_from_domain(row) -> str:
    from_addr = str(getattr(row, "email_from", None) or "").strip().lower()
    return from_addr.split("@", 1)[1] if "@" in from_addr else ""


def _is_instantly_email(row) -> bool:
    source = str(getattr(row, "source", None) or "").strip().lower()
    external_source = str(getattr(row, "external_source", None) or "").strip().lower()
    return (
        source == "instantly"
        or external_source.startswith("instantly")
        or _email_from_domain(row) in INSTANTLY_DOMAINS
    )


def _email_out_bucket(row) -> Literal["manual", "instantly"] | None:
    """
    Emails Out bucket rule:
    - Instantly rows count as Instantly even when the sender is a @beacon.li
      account; prod has both webhook and campaign-sync rows in that shape.
    - Non-Instantly @beacon.li rows count as Manual when:
        - source is personal_email_sync (personal inbox, already contact-filtered)
        - source is gmail_sync (rep's connected Gmail account, incl. .com/.co aliases)
        - Zippy is in CC or BCC (tracked via Zippy inbox)
    - Anything else: do not count.
    """
    if _is_instantly_email(row):
        return "instantly"
    domain = _email_from_domain(row)
    if domain == "beacon.li":
        source = str(getattr(row, "source", None) or "").strip().lower()
        if source in {"personal_email_sync", "gmail_sync"} or _zippy_in_cc_or_bcc(row):
            return "manual"
    return None


def _should_count_as_email_out(row) -> bool:
    return _email_out_bucket(row) is not None


def _email_addresses(value: str | None) -> list[str]:
    return [match.group(0).lower() for match in _EMAIL_RE.finditer(str(value or ""))]


def _normalized_email_subject(value: str | None) -> str:
    subject = str(value or "").strip().lower()
    while True:
        next_subject = _SUBJECT_PREFIX_RE.sub("", subject, count=1)
        if next_subject == subject:
            break
        subject = next_subject.strip()
    return " ".join(subject.split())


def _manual_email_dedupe_key(row, rep_key: str) -> tuple | None:
    """Same manual thread, same recipient, same UTC day should count once.

    Gmail can sync repeated manual-send rows with different timestamps and even
    different message ids for the same rep/person/day/thread. Message-id dedupe
    catches exact row duplication; this catches the remaining same-thread
    repetition without touching Instantly sends or different-subject manual mail.
    """
    if _email_out_bucket(row) != "manual":
        return None
    created_at = getattr(row, "created_at", None)
    if not created_at:
        return None
    recipient_key = None
    contact_id = getattr(row, "contact_id", None)
    if contact_id:
        recipient_key = f"contact:{contact_id}"
    else:
        to_addrs = _email_addresses(getattr(row, "email_to", None))
        if to_addrs:
            recipient_key = f"to:{','.join(sorted(set(to_addrs)))}"
    if not recipient_key:
        return None
    subject_key = _normalized_email_subject(getattr(row, "email_subject", None))
    if not subject_key:
        return None
    return ("manual-email", rep_key, created_at.date(), recipient_key, subject_key)


def _beacon_sender_local(email_from) -> "str | None":
    """Local-part of ``email_from`` when it is one of OUR sending identities,
    else None (received mail or a non-rep sender)."""
    local, _, domain = str(email_from or "").strip().lower().partition("@")
    if not local or not domain:
        return None
    if domain in BEACON_SENDING_DOMAINS or domain.startswith("beaconli."):
        return local
    return None


def _beacon_recipient_local(row) -> "str | None":
    """Local-part of the first OUR-domain address in email_to/email_cc — the rep
    who RECEIVED this mail (i.e. sent the outreach it replies to). The inbound
    mirror of _beacon_sender_local; used to credit a reply to the rep whose
    outreach earned it."""
    for field in (getattr(row, "email_to", None), getattr(row, "email_cc", None)):
        for addr in str(field or "").split(","):
            local = _beacon_sender_local(addr)
            if local:
                return local
    return None


def _email_event_kind(row) -> "str | None":
    """Classify an email Activity: 'send' | 'open' | 'reply' | None. Only sends
    count toward the ``emails`` metric; opens/replies feed open/reply-rate;
    other Instantly events (bounce, campaign_completed, lead_*) are ignored."""
    meta = row.event_metadata if isinstance(row.event_metadata, dict) else {}
    et = str(meta.get("event_type") or "").strip().lower()
    if et == "email_opened":
        return "open"
    if et == "reply_received" or str(row.source or "").strip().lower() == "email_reply":
        return "reply"
    if et in {"email_sent", ""}:
        return "send"
    return None


def _activity_rep_id(
    row,
    *,
    deal_owner: dict[UUID, UUID | None],
    contact_owner: dict[UUID, UUID | None],
    rep_id_by_local: "dict[str, UUID] | None" = None,
) -> UUID | None:
    source = str(row.source or "").strip().lower()
    medium = str(row.medium or "").strip().lower()
    kind = str(row.type or "").strip().lower()
    metadata = row.event_metadata if isinstance(row.event_metadata, dict) else {}

    # Email SENDS credit the actual sender — the rep whose address is in
    # email_from across our primary + outreach domains — NOT the deal/contact
    # owner. Received mail / non-rep senders return None so inbound is excluded.
    # Opens and replies are engagement events and keep owner-based attribution.
    if rep_id_by_local is not None and (medium == "email" or kind == "email"):
        email_kind = _email_event_kind(row)
        if email_kind == "send":
            full_from = str(row.email_from or "").strip().lower()
            local = _beacon_sender_local(full_from)
            if local:
                # Try full-address first (handles aliases with different local-parts),
                # then fall back to local-part lookup.
                return rep_id_by_local.get(full_from) or rep_id_by_local.get(local)
            return None
        if email_kind == "reply":
            # A reply credits the rep whose outreach earned it — the beacon
            # address it was sent TO (recipient-based; the mirror of the send
            # rule). Falls through to owner attribution when no rep recipient is
            # found, so nothing that counted before is lost.
            rlocal = _beacon_recipient_local(row)
            if rlocal and rlocal in rep_id_by_local:
                return rep_id_by_local[rlocal]
            # Instantly reply_received events store the rep's beaconli.com
            # address in email_from (the account that received the reply),
            # not in email_to. Try full address first, then local-part fallback.
            full_from = str(row.email_from or "").strip().lower()
            flocal = _beacon_sender_local(full_from)
            if flocal:
                return rep_id_by_local.get(full_from) or rep_id_by_local.get(flocal)

    # Manually logged calls/LinkedIn touches should credit the rep who logged
    # the action, even when the contacted person is assigned to a different rep.
    if row.created_by_id and source == "manual" and (medium in {"call", "linkedin"} or kind in {"call", "linkedin"}):
        return row.created_by_id

    # Personal inbox sync represents the rep's own mailbox activity, so the
    # syncing user should own the touch even when the deal/contact is assigned
    # to someone else in CRM.
    if source == "personal_email_sync":
        if row.created_by_id:
            return row.created_by_id
        synced_by_user_id = metadata.get("synced_by_user_id")
        if synced_by_user_id:
            try:
                return UUID(str(synced_by_user_id))
            except (TypeError, ValueError):
                pass

    return deal_owner.get(row.deal_id) or contact_owner.get(row.contact_id) or row.created_by_id


def _meeting_rep_id(row, *, deal_owner: dict[UUID, UUID | None]) -> UUID | None:
    return row.owner_user_id or deal_owner.get(row.deal_id)


def _is_crm_linked_meeting(row) -> bool:
    return bool(row.company_id or row.deal_id)


def _meeting_reporting_timestamp(row, *, window_end: datetime) -> datetime:
    if row.scheduled_at and row.scheduled_at <= window_end:
        return row.scheduled_at
    return row.created_at or row.scheduled_at


def _meeting_attendee_rep_ids(row, *, user_ids_by_email: dict[str, UUID]) -> list[UUID]:
    attendees = row.attendees if isinstance(row.attendees, list) else []
    rep_ids: list[UUID] = []
    seen: set[UUID] = set()
    for attendee in attendees:
        if not isinstance(attendee, dict):
            continue
        email = str(attendee.get("email") or "").strip().lower()
        rep_id = user_ids_by_email.get(email)
        if rep_id and rep_id not in seen:
            seen.add(rep_id)
            rep_ids.append(rep_id)
    return rep_ids


def _meeting_rep_ids(
    row,
    *,
    deal_owner: dict[UUID, UUID | None],
    user_ids_by_email: dict[str, UUID],
) -> list[UUID | None]:
    rep_ids: list[UUID | None] = []
    seen: set[UUID] = set()
    primary_id = row.owner_user_id or deal_owner.get(row.deal_id)
    if primary_id:
        rep_ids.append(primary_id)
        seen.add(primary_id)
    for attendee_rep_id in _meeting_attendee_rep_ids(row, user_ids_by_email=user_ids_by_email):
        if attendee_rep_id not in seen:
            seen.add(attendee_rep_id)
            rep_ids.append(attendee_rep_id)
    return rep_ids or [None]


async def _build_meeting_stage_gate(session, stage_settings):
    """Return a predicate `gate(meeting_row) -> bool` keeping only early-funnel
    meetings (deal at or before `demo_done`).

    A rep's meeting metric should reflect prospecting/demo meetings, not the
    recurring daily/weekly syncs that accumulate once an account is deep in POC
    or already a customer. The early-stage set follows the configured stage
    ORDER (custom/renamed stages still work), and the stage maps are built from
    an UNFILTERED deal scan so rep/geo filters can't hide a late-stage deal.
    """
    ordered = [s["id"] for s in stage_settings if s.get("group") != "closed"]
    if "demo_done" in ordered:
        early = set(ordered[: ordered.index("demo_done") + 1])
    else:
        early = {"reprospect", "demo_scheduled", "demo_done"}
    rows = (await session.execute(select(Deal.id, Deal.stage, Deal.company_id))).all()
    deal_stage: dict[UUID, str] = {r.id: (r.stage or "") for r in rows}
    company_stages: dict[UUID, set[str]] = {}
    for r in rows:
        if r.company_id is not None:
            company_stages.setdefault(r.company_id, set()).add(r.stage or "")

    def gate(row) -> bool:
        if row.deal_id is not None:
            stage = deal_stage.get(row.deal_id)
            # Unknown deal (shouldn't happen) → keep rather than silently drop.
            return stage in early if stage is not None else True
        if row.company_id is not None:
            stages = company_stages.get(row.company_id)
            # No deal yet = fresh prospect (keep); deals exist but none early =
            # a customer/late account doing syncs (drop).
            if not stages:
                return True
            return any(s in early for s in stages)
        return True

    return gate


def _normalize_geography_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "Unassigned"
    # This function normalizes BOTH raw region values (e.g. "US", "APAC") AND the
    # filter param the UI sends, which is the already-bucketed label itself
    # ("America", "Rest of the World", "unassigned"). So the bucket labels must map
    # to themselves — "america" was missing from the set below, so selecting the
    # America filter normalized to "Rest of the World" and returned the wrong region.
    if raw == "unassigned":
        return "Unassigned"
    if raw in {"america", "us", "usa", "united states", "united states of america", "na", "north america", "americas", "latam", "latin america", "canada", "mexico"}:
        return "America"
    if raw in {"india", "in", "apac", "asia pacific", "asia-pacific", "anz", "australia", "new zealand", "singapore", "japan", "rest of world", "rest of the world", "row"}:
        return "Rest of the World"
    return "Rest of the World"


def _contact_meeting_signal(contact_row) -> bool:
    status_blob = " ".join(
        str(value or "").strip().lower()
        for value in (contact_row.outreach_lane, contact_row.sequence_status, contact_row.instantly_status)
    )
    return any(marker in status_blob for marker in HOT_MEETING_MARKERS)


def _rolling_month_keys(months: int, *, end: date | None = None) -> list[str]:
    cursor = end or date.today()
    year = cursor.year
    month = cursor.month
    keys: list[str] = []
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    keys.reverse()
    return keys


def _build_monthly_unique_funnel_rows(
    milestone_rows,
    *,
    months: int,
) -> list[MonthlyUniqueFunnelRow]:
    keys = _rolling_month_keys(months)
    counts = {
        month_key: {
            "demo_done": 0,
            "poc_agreed": 0,
            "poc_wip": 0,
            "poc_done": 0,
            "closed_won": 0,
        }
        for month_key in keys
    }
    for row in milestone_rows:
        month_key = _month_key_for_datetime(row.first_reached_at)
        if month_key not in counts or row.milestone_key not in counts[month_key]:
            continue
        counts[month_key][row.milestone_key] += 1

    return [
        MonthlyUniqueFunnelRow(
            month_key=month_key,
            label=_month_label(month_key),
            demo_done=counts[month_key]["demo_done"],
            poc_agreed=counts[month_key]["poc_agreed"],
            poc_wip=counts[month_key]["poc_wip"],
            poc_done=counts[month_key]["poc_done"],
            closed_won=counts[month_key]["closed_won"],
        )
        for month_key in keys
    ]


async def _load_monthly_unique_funnel(
    session: DBSession,
    *,
    months: int = 12,
    rep_ids: list[UUID] | None = None,
    geography: list[str] | None = None,
) -> list[MonthlyUniqueFunnelRow]:
    await backfill_company_stage_milestones(session)
    month_keys = _rolling_month_keys(months)
    earliest_month = datetime.strptime(month_keys[0], "%Y-%m")
    stmt = (
        select(
            CompanyStageMilestone.milestone_key,
            CompanyStageMilestone.first_reached_at,
            Deal.geography.label("deal_geography"),
        )
        .outerjoin(Deal, CompanyStageMilestone.deal_id == Deal.id)
        .where(
            CompanyStageMilestone.first_reached_at >= earliest_month,
            CompanyStageMilestone.milestone_key.in_(list(MILESTONE_LABELS.keys())),
        )
    )
    if rep_ids:
        stmt = stmt.where(Deal.assigned_to_id.in_(rep_ids))
    milestone_rows = (
        await session.execute(stmt)
    ).all()
    if geography:
        normalized_geos = {_normalize_geography_key(g) for g in geography}
        milestone_rows = [row for row in milestone_rows if _normalize_geography_key(row.deal_geography) in normalized_geos]
    return _build_monthly_unique_funnel_rows(milestone_rows, months=months)


@router.get("/monthly-funnel-summary", response_model=list[MonthlyUniqueFunnelRow])
async def monthly_funnel_summary(
    session: DBSession,
    _user: CurrentUser,
    months: Annotated[int, Query(ge=3, le=24)] = 12,
):
    return await _load_monthly_unique_funnel(session, months=months)


SOURCE_LABELS: dict[str, str] = {
    "personal_email_sync": "Email Sync",
    "gmail_sync": "Gmail Sync",
    "manual": "Manual Entry",
    "system": "System",
    "system_task": "System Task",
    "instantly": "Instantly",
    "aircall": "Aircall",
    "tldv": "tl;dv",
    "clickup_import": "ClickUp Import",
}


def _source_label(source: str | None) -> str | None:
    if not source:
        return None
    return SOURCE_LABELS.get(source.lower(), source)


@router.get("/sales-activity-drilldown", response_model=SalesActivityDrilldownRead)
async def sales_activity_drilldown(
    session: DBSession,
    _user: CurrentUser,
    metric: Annotated[
        Literal[
            "emails", "manual_emails", "instantly_emails", "email_replies", "calls", "connected_calls", "live_calls",
            "linkedin_reachouts", "meetings", "total", "demos_scheduled", "demos_done",
            "demos_converted", "ae_demos_scheduled", "ae_demos_done", "ae_demos_converted",
        ],
        Query(description="Activity metric to inspect"),
    ],
    window_days: Annotated[int, Query(ge=1, le=36500)] = 90,
    rep_id: Annotated[Optional[UUID], Query()] = None,
    geography: Annotated[list[str], Query()] = [],
    from_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window start")] = None,
    to_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window end")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    window_start, window_end = _resolve_analytics_window(window_days, from_date, to_date)
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}

    analytics_settings = await get_analytics_settings(session)
    user_rows = (await session.execute(select(User.id, User.name, User.email, User.role, User.is_active))).all()
    users = {row.id: row.name for row in user_rows}
    user_emails = {row.id: str(row.email or "").strip().lower() for row in user_rows}
    user_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in user_rows if row.email}
    # email_from local-part -> rep id, for sender-based attribution of email sends.
    # Seed from primary @beacon.li addresses first, then layer in all aliases so
    # that .com/.co senders resolve even when their local-part differs from the
    # primary address.
    rep_id_by_local: dict[str, UUID] = {
        e.split("@")[0]: uid for uid, e in user_emails.items() if e.endswith("@beacon.li")
    }
    alias_rows = (await session.execute(select(UserAlias.user_id, UserAlias.email))).all()
    for alias in alias_rows:
        alias_email = str(alias.email or "").strip().lower()
        if not alias_email or "@" not in alias_email:
            continue
        alias_local = alias_email.split("@")[0]
        alias_domain = alias_email.split("@")[1]
        if alias_domain in BEACON_SENDING_DOMAINS:
            # Full address lookup (handles different local-parts across domains)
            rep_id_by_local[alias_email] = alias.user_id
            # Local-part lookup (only add if not already claimed by primary)
            rep_id_by_local.setdefault(alias_local, alias.user_id)
    # AE/SDR users plus any admins explicitly configured for Sales Analytics.
    rep_user_ids = _sales_analytics_rep_user_ids(user_rows, analytics_settings)

    # Geography must be applied via _normalize_geography_key in Python, exactly
    # like sales_dashboard does — the filter param holds bucket labels
    # ("America", "Rest of the World", "Unassigned") while the DB columns hold
    # raw values ("US", "APAC", NULL), so an IN() on the raw columns matches
    # nothing and the drilldown disagrees with the dashboard tiles.
    deal_stmt = select(Deal.id, Deal.name, Deal.assigned_to_id, Deal.company_id, Deal.geography)
    if rep_id:
        deal_stmt = deal_stmt.where(Deal.assigned_to_id == rep_id)
    scoped_deal_rows = (await session.execute(deal_stmt)).all()
    if filter_geographies:
        scoped_deal_rows = [
            row for row in scoped_deal_rows
            if _normalize_geography_key(row.geography) in filter_geographies
        ]
    scoped_deal_ids = {row.id for row in scoped_deal_rows}

    contact_stmt = (
        select(Contact.id, Contact.assigned_to_id, Contact.company_id, Company.region.label("company_region"))
        .outerjoin(Company, Contact.company_id == Company.id)
    )
    if rep_id:
        contact_stmt = contact_stmt.where(Contact.assigned_to_id == rep_id)
    scoped_contact_rows = (await session.execute(contact_stmt)).all()
    if filter_geographies:
        scoped_contact_rows = [
            row for row in scoped_contact_rows
            if _normalize_geography_key(row.company_region) in filter_geographies
        ]
    scoped_contact_ids = {row.id for row in scoped_contact_rows}

    def activity_metric_filter():
        type_lower = func.lower(Activity.type)
        medium_lower = func.lower(Activity.medium)
        if metric in EMAIL_SEND_METRICS or metric == "email_replies":
            return or_(type_lower == "email", medium_lower == "email")
        if metric in {"calls", "connected_calls", "live_calls"}:
            base = or_(type_lower == "call", medium_lower == "call")
            if metric == "connected_calls":
                return base & func.lower(Activity.call_outcome).in_(["connected", "callback", "answered"])
            if metric == "live_calls":
                return base & func.lower(Activity.call_outcome).in_(["connected", "answered"])
            return base
        if metric == "linkedin_reachouts":
            return or_(type_lower == "linkedin", medium_lower == "linkedin")
        return or_(
            type_lower.in_(["email", "call", "linkedin"]),
            medium_lower.in_(["email", "call", "linkedin"]),
        )

    rows: list[SalesActivityDrilldownRow] = []
    # Demo-funnel metrics are NOT activity-table backed; the activity stream must
    # only run for the activity metrics (and "total", which merges in meetings).
    if metric in EMAIL_SEND_METRICS or metric in {"email_replies", "calls", "connected_calls", "live_calls", "linkedin_reachouts", "total"}:
        activity_stmt = (
            select(Activity)
            .where(Activity.created_at >= window_start, Activity.created_at <= window_end)
            .where(activity_metric_filter())
        )
        if rep_id and metric in EMAIL_SEND_METRICS:
            # Emails are sender-based: fetch this rep's OUTBOUND sends across all
            # our sending domains (beacon.li + outreach lookalikes), not emails
            # that merely sit on deals/contacts they own.
            rep_local = (user_emails.get(rep_id) or "").split("@")[0]
            rep_from = {f"{rep_local}@{d}" for d in BEACON_SENDING_DOMAINS} if rep_local else set()
            # Also include any alias emails registered for this rep — an alias can
            # have a different local-part (e.g. primary "sipra.palta@beacon.li" but
            # Instantly account "sipra@beaconli.com").  Without this, the SQL IN()
            # clause never fetches those rows regardless of the rep_id_by_local fix.
            for alias in alias_rows:
                if alias.user_id == rep_id:
                    alias_email = str(alias.email or "").strip().lower()
                    if alias_email and "@" in alias_email:
                        rep_from.add(alias_email)
            activity_stmt = activity_stmt.where(func.lower(Activity.email_from).in_(rep_from or {"__none__"}))
        elif rep_id and metric == "email_replies":
            # Replies are credited recipient-based (the beacon address the
            # outreach was sent TO), with an owner fallback — NOT by who the row
            # sits on. A reply can therefore belong to the rep via the beacon
            # recipient local-part even when its deal/contact is owned elsewhere,
            # so narrowing in SQL by owner/created_by would drop rows the
            # dashboard's email_replies count includes. We fetch the full email
            # window and attribute in Python with the SAME _activity_rep_id the
            # dashboard uses, exactly like _meeting_rep_ids does for meetings.
            pass
        elif rep_id:
            activity_stmt = activity_stmt.where(
                or_(
                    Activity.created_by_id == rep_id,
                    Activity.deal_id.in_(scoped_deal_ids or {UUID(int=0)}),
                    Activity.contact_id.in_(scoped_contact_ids or {UUID(int=0)}),
                )
            )
        elif filter_geographies:
            activity_stmt = activity_stmt.where(
                or_(
                    Activity.deal_id.in_(scoped_deal_ids or {UUID(int=0)}),
                    Activity.contact_id.in_(scoped_contact_ids or {UUID(int=0)}),
                )
            )
        # For metric="total" the activity and meeting streams are merged and
        # paginated ONCE on the combined list below, so we must NOT pre-offset
        # this stream — fetch from the top through offset+limit (+1 sentinel).
        # metric="email_replies" is reply-vs-send + recipient-credited and cannot
        # be expressed in SQL, so it is filtered in Python and paginated on the
        # filtered list below — it likewise must fetch the whole email window
        # un-offset (no SQL limit, since SQL rows are pre-filter).
        # Other metrics paginate this stream alone, so keep the SQL offset.
        if metric == "total":
            activity_stmt = activity_stmt.order_by(Activity.created_at.desc()).limit(offset + limit + 1)
        elif metric in EMAIL_SEND_METRICS or metric == "email_replies":
            activity_stmt = activity_stmt.order_by(Activity.created_at.desc())
        else:
            activity_stmt = activity_stmt.order_by(Activity.created_at.desc()).offset(offset).limit(limit + 1)
        activities = (await session.execute(activity_stmt)).scalars().all()

        send_has_more = False
        if metric in EMAIL_SEND_METRICS:
            send_filtered = []
            seen_send_msg_ids: set[str] = set()
            seen_send_manual_keys: dict[str, set[tuple]] = {}
            for activity in activities:
                if _email_event_kind(activity) != "send":
                    continue
                bucket = _email_out_bucket(activity)
                if bucket is None:
                    continue
                if metric == "manual_emails" and bucket != "manual":
                    continue
                if metric == "instantly_emails" and bucket != "instantly":
                    continue
                msg_id = str(activity.email_message_id or "").strip()
                if msg_id and msg_id in seen_send_msg_ids:
                    continue
                if msg_id:
                    seen_send_msg_ids.add(msg_id)
                row_rep_id = _activity_rep_id(
                    activity,
                    deal_owner={},
                    contact_owner={},
                    rep_id_by_local=rep_id_by_local,
                )
                if not _is_rep(row_rep_id, rep_user_ids):
                    continue
                if rep_id and row_rep_id != rep_id:
                    continue
                row_rep_key = _label_for_rep(row_rep_id, users)[0]
                manual_key = _manual_email_dedupe_key(activity, row_rep_key)
                if manual_key:
                    rep_manual_seen = seen_send_manual_keys.setdefault(row_rep_key, set())
                    if manual_key in rep_manual_seen:
                        continue
                    rep_manual_seen.add(manual_key)
                send_filtered.append(activity)
            send_has_more = len(send_filtered) > offset + limit
            activities = send_filtered[offset:offset + limit + 1]

        # email_replies: keep only inbound replies credited to the requested rep,
        # using the SAME _email_event_kind/_activity_rep_id the dashboard uses, so
        # the row set == the dashboard email_replies count. Owner maps for
        # attribution are built below; replies fall back to owner attribution only
        # when no beacon recipient is found, matching the dashboard exactly. The
        # filtered list is paginated (offset/limit + sentinel) before row-building.
        reply_has_more = False
        if metric == "email_replies":
            reply_deal_ids = {a.deal_id for a in activities if a.deal_id}
            reply_contact_ids = {a.contact_id for a in activities if a.contact_id}
            reply_deal_owner: dict[UUID, UUID | None] = {row.id: row.assigned_to_id for row in scoped_deal_rows}
            reply_contact_owner: dict[UUID, UUID | None] = {row.id: row.assigned_to_id for row in scoped_contact_rows}
            if reply_deal_ids:
                for row in (
                    await session.execute(
                        select(Deal.id, Deal.assigned_to_id).where(Deal.id.in_(reply_deal_ids))
                    )
                ).all():
                    reply_deal_owner[row.id] = row.assigned_to_id
            if reply_contact_ids:
                for row in (
                    await session.execute(
                        select(Contact.id, Contact.assigned_to_id).where(Contact.id.in_(reply_contact_ids))
                    )
                ).all():
                    reply_contact_owner[row.id] = row.assigned_to_id
            reply_filtered = []
            for activity in activities:
                if _email_event_kind(activity) != "reply":
                    continue
                row_rep_id = _activity_rep_id(
                    activity,
                    deal_owner=reply_deal_owner,
                    contact_owner=reply_contact_owner,
                    rep_id_by_local=rep_id_by_local,
                )
                if not _is_rep(row_rep_id, rep_user_ids):
                    continue
                if rep_id and row_rep_id != rep_id:
                    continue
                reply_filtered.append(activity)
            reply_has_more = len(reply_filtered) > offset + limit
            activities = reply_filtered[offset:offset + limit + 1]

        # total merges globally, so every fetched activity must participate;
        # single-metric pages are already the final slice (cap at limit).
        activity_page = activities if metric == "total" else activities[:limit]
        contact_ids = {activity.contact_id for activity in activity_page if activity.contact_id}
        deal_ids = {activity.deal_id for activity in activity_page if activity.deal_id}

        contact_owner: dict[UUID, UUID | None] = {row.id: row.assigned_to_id for row in scoped_contact_rows}
        contact_names: dict[UUID, str | None] = {}
        contact_emails: dict[UUID, str | None] = {}
        contact_company_ids: dict[UUID, UUID | None] = {row.id: row.company_id for row in scoped_contact_rows}
        if contact_ids:
            detail_contacts = (
                await session.execute(
                    select(Contact.id, Contact.first_name, Contact.last_name, Contact.email, Contact.assigned_to_id, Contact.company_id)
                    .where(Contact.id.in_(contact_ids))
                )
            ).all()
            for row in detail_contacts:
                contact_owner[row.id] = row.assigned_to_id
                contact_names[row.id] = " ".join(part for part in [row.first_name, row.last_name] if part).strip() or row.email
                contact_emails[row.id] = row.email
                contact_company_ids[row.id] = row.company_id

        deal_owner: dict[UUID, UUID | None] = {row.id: row.assigned_to_id for row in scoped_deal_rows}
        deal_names: dict[UUID, str | None] = {row.id: row.name for row in scoped_deal_rows}
        deal_company_ids: dict[UUID, UUID | None] = {row.id: row.company_id for row in scoped_deal_rows}
        if deal_ids:
            detail_deals = (
                await session.execute(
                    select(Deal.id, Deal.name, Deal.assigned_to_id, Deal.company_id).where(Deal.id.in_(deal_ids))
                )
            ).all()
            for row in detail_deals:
                deal_owner[row.id] = row.assigned_to_id
                deal_names[row.id] = row.name
                deal_company_ids[row.id] = row.company_id

        company_ids = {cid for cid in deal_company_ids.values() if cid} | {cid for cid in contact_company_ids.values() if cid}
        company_names: dict[UUID, str] = {}
        if company_ids:
            company_names = {
                row.id: row.name
                for row in (await session.execute(select(Company.id, Company.name).where(Company.id.in_(company_ids)))).all()
            }

        seen_drilldown_msg_ids: set[str] = set()
        for activity in activity_page:
            is_email = str(activity.type or "").strip().lower() == "email" or str(activity.medium or "").strip().lower() == "email"
            if metric in EMAIL_SEND_METRICS:
                # The emails metric is sender-based and SENDS only — independent
                # of contact/deal linkage; opens/replies and inbound are excluded.
                if _email_event_kind(activity) != "send":
                    continue
                bucket = _email_out_bucket(activity)
                if bucket is None:
                    continue
                if metric == "manual_emails" and bucket != "manual":
                    continue
                if metric == "instantly_emails" and bucket != "instantly":
                    continue
                # Dedup: same email can be captured by both personal_email_sync
                # and gmail_sync — skip if we've already shown this message.
                msg_id = str(activity.email_message_id or "").strip()
                if msg_id and msg_id in seen_drilldown_msg_ids:
                    continue
                if msg_id:
                    seen_drilldown_msg_ids.add(msg_id)
            elif metric == "email_replies":
                # Replies were already classified, credited and rep-matched (and
                # the page already paginated) above — independent of contact/deal
                # linkage, exactly like the dashboard email_replies count. Do not
                # re-gate on contact/deal here or beacon-recipient-credited
                # replies with no linked deal/contact would be dropped.
                pass
            elif not activity.contact_id and not activity.deal_id:
                continue
            row_rep_id = _activity_rep_id(
                activity, deal_owner=deal_owner, contact_owner=contact_owner, rep_id_by_local=rep_id_by_local
            )
            if not _is_rep(row_rep_id, rep_user_ids):
                continue
            if rep_id and row_rep_id != rep_id:
                continue
            rep_email = user_emails.get(row_rep_id) if row_rep_id else None
            direction = None
            if metric == "email_replies":
                # Inbound by definition — the prospect (email_from) is the
                # counterparty; the beacon rep is the recipient.
                direction = "inbound"
            elif is_email:
                # outbound iff it came from one of OUR sending identities.
                direction = "outbound" if _beacon_sender_local(activity.email_from) else "inbound"
            company_id = contact_company_ids.get(activity.contact_id) or deal_company_ids.get(activity.deal_id)
            activity_type = str(activity.type or activity.medium or "activity").strip().lower() or "activity"
            source = activity.source
            source_label = _source_label(source)
            rows.append(
                SalesActivityDrilldownRow(
                    id=activity.id,
                    kind="activity",
                    activity_type=activity_type,
                    occurred_at=activity.created_at,
                    rep_user_id=row_rep_id,
                    rep_name=_label_for_rep(row_rep_id, users)[2],
                    source=source,
                    source_label=source_label,
                    subject=activity.email_subject,
                    direction=direction,
                    from_email=activity.email_from,
                    to_email=activity.email_to,
                    email_body=activity.content,
                    call_outcome=activity.call_outcome,
                    call_duration=activity.call_duration,
                    contact_name=contact_names.get(activity.contact_id),
                    contact_email=contact_emails.get(activity.contact_id),
                    company_name=company_names.get(company_id),
                    deal_name=deal_names.get(activity.deal_id),
                    deal_id=str(activity.deal_id) if activity.deal_id else None,
                    company_id=str(company_id) if company_id else None,
                )
            )

    meeting_has_more = False
    if metric in {"meetings", "total"}:
        meeting_stmt = select(Meeting).where(
            Meeting.is_internal.is_(False),
            or_(Meeting.company_id.isnot(None), Meeting.deal_id.isnot(None)),
            or_(
                (Meeting.scheduled_at >= window_start) & (Meeting.scheduled_at <= window_end),
                Meeting.scheduled_at.is_(None) & (Meeting.created_at >= window_start) & (Meeting.created_at <= window_end),
            ),
        )
        # Geography scoping only when no rep is selected — matches the prior
        # behaviour. Rep attribution itself is NOT done in SQL here: a meeting
        # can belong to a rep purely via attendee email (not owner/synced_by/
        # deal owner), and the dashboard count attributes those via
        # _meeting_rep_ids. Filtering by owner/synced_by/deal in SQL silently
        # dropped attendee-only meetings, so the count said "1" while the
        # drilldown showed nothing. We fetch the window and attribute in Python
        # with the SAME _meeting_rep_ids the count uses, then paginate here.
        # Meeting volume is small (hundreds), so this stays cheap.
        if filter_geographies and not rep_id:
            meeting_stmt = meeting_stmt.where(Meeting.deal_id.in_(scoped_deal_ids or {UUID(int=0)}))
        all_meeting_rows = (
            await session.execute(
                meeting_stmt.order_by(Meeting.scheduled_at.desc(), Meeting.created_at.desc())
            )
        ).scalars().all()

        meeting_deal_ids = {m.deal_id for m in all_meeting_rows if m.deal_id}
        meeting_company_ids = {m.company_id for m in all_meeting_rows if m.company_id}
        deal_owner = {row.id: row.assigned_to_id for row in scoped_deal_rows}
        deal_names = {row.id: row.name for row in scoped_deal_rows}
        deal_company_ids = {row.id: row.company_id for row in scoped_deal_rows}
        if meeting_deal_ids:
            detail_deals = (
                await session.execute(
                    select(Deal.id, Deal.name, Deal.assigned_to_id, Deal.company_id).where(Deal.id.in_(meeting_deal_ids))
                )
            ).all()
            for row in detail_deals:
                deal_owner[row.id] = row.assigned_to_id
                deal_names[row.id] = row.name
                deal_company_ids[row.id] = row.company_id
                if row.company_id:
                    meeting_company_ids.add(row.company_id)
        company_names = {}
        if meeting_company_ids:
            company_names = {
                row.id: row.name
                for row in (await session.execute(select(Company.id, Company.name).where(Company.id.in_(meeting_company_ids)))).all()
            }

        # Same early-funnel gate as the dashboard so the drilldown list matches
        # the headline meeting count (≤ demo_done).
        _meeting_gate = await _build_meeting_stage_gate(
            session, await get_configured_deal_stages(session)
        )
        # Collapse cross-source duplicates (tl;dv + Google Calendar) then
        # attribute exactly like the count — so the drilldown can never disagree
        # with the dashboard number.
        _gated_meetings = [
            m
            for m in all_meeting_rows
            if m.status != "cancelled"
            and _meeting_gate(m)
            and str(m.external_source or "").strip().lower() in REAL_MEETING_SOURCES
        ]
        _deduped_meetings = _dedupe_meetings_across_sources(_gated_meetings)
        _meeting_entries = []
        for meeting in _deduped_meetings:
            for row_rep_id in _meeting_rep_ids(meeting, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email):
                if not _is_rep(row_rep_id, rep_user_ids):
                    continue
                if rep_id and row_rep_id != rep_id:
                    continue
                _meeting_entries.append((meeting, row_rep_id))
        _meeting_entries.sort(
            key=lambda entry: _meeting_reporting_timestamp(entry[0], window_end=window_end),
            reverse=True,
        )
        meeting_has_more = len(_meeting_entries) > offset + limit
        # metric="total" merges this stream with activities and slices once
        # below, so feed it the top offset+limit (+1 sentinel) un-offset; the
        # meetings-only page is its own final slice.
        meeting_slice = (
            _meeting_entries[: offset + limit + 1]
            if metric == "total"
            else _meeting_entries[offset:offset + limit]
        )
        for meeting, row_rep_id in meeting_slice:
            meeting_time = _meeting_reporting_timestamp(meeting, window_end=window_end)
            company_id = meeting.company_id or deal_company_ids.get(meeting.deal_id)
            rows.append(
                SalesActivityDrilldownRow(
                    id=meeting.id,
                    kind="meeting",
                    activity_type="meeting",
                    occurred_at=meeting_time,
                    rep_user_id=row_rep_id,
                    rep_name=_label_for_rep(row_rep_id, users)[2],
                    source=meeting.external_source,
                    subject=meeting.title,
                    company_name=company_names.get(company_id),
                    deal_name=deal_names.get(meeting.deal_id),
                    deal_id=str(meeting.deal_id) if meeting.deal_id else None,
                    company_id=str(company_id) if company_id else None,
                )
            )

    demo_has_more = False
    if metric == "demos_scheduled":
        # Demo Scheduled drilldown: deals that entered "demo_scheduled" stage
        # within the window, attributed to the account's SDR.
        if rep_id:
            sched_hist_rows = (
                await session.execute(
                    select(
                        DealStageHistory.deal_id,
                        DealStageHistory.changed_at,
                    ).where(
                        func.lower(DealStageHistory.to_stage) == "demo_scheduled",
                        DealStageHistory.changed_at >= window_start,
                        DealStageHistory.changed_at <= window_end,
                    ).order_by(DealStageHistory.changed_at.desc())
                )
            ).all()
            # Dedup: keep earliest entry per deal (first time it entered stage)
            seen_sched: set[UUID] = set()
            unique_sched: list = []
            for r in sorted(sched_hist_rows, key=lambda x: x.changed_at):
                if r.deal_id not in seen_sched:
                    seen_sched.add(r.deal_id)
                    unique_sched.append(r)
            unique_sched.sort(key=lambda r: r.changed_at, reverse=True)

            sched_deal_ids_dd = {r.deal_id for r in unique_sched}
            sched_deal_rows_dd: dict[UUID, object] = {}
            sched_company_ids_dd: set[UUID] = set()
            if sched_deal_ids_dd:
                for dr in (
                    await session.execute(
                        select(Deal.id, Deal.name, Deal.company_id).where(Deal.id.in_(sched_deal_ids_dd))
                    )
                ).all():
                    sched_deal_rows_dd[dr.id] = dr
                    if dr.company_id:
                        sched_company_ids_dd.add(dr.company_id)

            sched_comp_sdr_dd: dict[UUID, UUID | None] = {}
            sched_comp_name_dd: dict[UUID, str] = {}
            sched_comp_region_dd: dict[UUID, str | None] = {}
            if sched_company_ids_dd:
                for cr in (
                    await session.execute(
                        select(Company.id, Company.name, Company.sdr_id, Company.region).where(
                            Company.id.in_(sched_company_ids_dd)
                        )
                    )
                ).all():
                    sched_comp_sdr_dd[cr.id] = cr.sdr_id
                    sched_comp_name_dd[cr.id] = cr.name or ""
                    sched_comp_region_dd[cr.id] = cr.region

            demo_entries_sched = []
            for hist_r in unique_sched:
                dr = sched_deal_rows_dd.get(hist_r.deal_id)
                if not dr:
                    continue
                cid = dr.company_id
                if not cid:
                    continue
                if sched_comp_sdr_dd.get(cid) != rep_id:
                    continue
                if filter_geographies:
                    region_key = _normalize_geography_key(sched_comp_region_dd.get(cid))
                    if region_key not in filter_geographies:
                        continue
                demo_entries_sched.append((hist_r, dr))

            demo_has_more = len(demo_entries_sched) > offset + limit
            for hist_r, dr in demo_entries_sched[offset:offset + limit]:
                rows.append(
                    SalesActivityDrilldownRow(
                        id=hist_r.deal_id,
                        kind="activity",
                        activity_type="demo_scheduled",
                        occurred_at=hist_r.changed_at,
                        rep_user_id=rep_id,
                        rep_name=_label_for_rep(rep_id, users)[2],
                        source="pipeline",
                        subject=dr.name,
                        company_name=sched_comp_name_dd.get(dr.company_id or UUID(int=0)),
                        deal_name=dr.name,
                        deal_id=str(dr.id),
                        company_id=str(dr.company_id) if dr.company_id else None,
                    )
                )

    if metric == "demos_done":
        # Demo Done drilldown: deals that moved into demo_done within window.
        # No demo_scheduled subquery — backfill_current only created one entry
        # per deal so historical deals in demo_done have no scheduled entry.
        if rep_id:
            done_hist_rows = (
                await session.execute(
                    select(
                        DealStageHistory.deal_id,
                        DealStageHistory.changed_at,
                    ).where(
                        func.lower(DealStageHistory.to_stage) == "demo_done",
                        DealStageHistory.changed_at >= window_start,
                        DealStageHistory.changed_at <= window_end,
                    ).order_by(DealStageHistory.changed_at.desc())
                )
            ).all()
            # Dedup: keep first transition per deal
            seen_done_dd: set[UUID] = set()
            unique_done: list = []
            for r in sorted(done_hist_rows, key=lambda x: x.changed_at):
                if r.deal_id not in seen_done_dd:
                    seen_done_dd.add(r.deal_id)
                    unique_done.append(r)
            unique_done.sort(key=lambda r: r.changed_at, reverse=True)

            done_deal_ids_dd = {r.deal_id for r in unique_done}
            done_deal_rows_dd: dict[UUID, object] = {}
            done_company_ids_dd: set[UUID] = set()
            if done_deal_ids_dd:
                for dr in (
                    await session.execute(
                        select(Deal.id, Deal.name, Deal.company_id).where(Deal.id.in_(done_deal_ids_dd))
                    )
                ).all():
                    done_deal_rows_dd[dr.id] = dr
                    if dr.company_id:
                        done_company_ids_dd.add(dr.company_id)

            done_comp_sdr_dd: dict[UUID, UUID | None] = {}
            done_comp_name_dd: dict[UUID, str] = {}
            done_comp_region_dd: dict[UUID, str | None] = {}
            if done_company_ids_dd:
                for cr in (
                    await session.execute(
                        select(Company.id, Company.name, Company.sdr_id, Company.region).where(
                            Company.id.in_(done_company_ids_dd)
                        )
                    )
                ).all():
                    done_comp_sdr_dd[cr.id] = cr.sdr_id
                    done_comp_name_dd[cr.id] = cr.name or ""
                    done_comp_region_dd[cr.id] = cr.region

            demo_entries_done = []
            for hist_r in unique_done:
                dr = done_deal_rows_dd.get(hist_r.deal_id)
                if not dr:
                    continue
                cid = dr.company_id
                if not cid:
                    continue
                if done_comp_sdr_dd.get(cid) != rep_id:
                    continue
                if filter_geographies:
                    region_key = _normalize_geography_key(done_comp_region_dd.get(cid))
                    if region_key not in filter_geographies:
                        continue
                demo_entries_done.append((hist_r, dr))

            demo_has_more = len(demo_entries_done) > offset + limit
            for hist_r, dr in demo_entries_done[offset:offset + limit]:
                rows.append(
                    SalesActivityDrilldownRow(
                        id=hist_r.deal_id,
                        kind="activity",
                        activity_type="demo_done",
                        occurred_at=hist_r.changed_at,
                        rep_user_id=rep_id,
                        rep_name=_label_for_rep(rep_id, users)[2],
                        source="pipeline",
                        subject=dr.name,
                        company_name=done_comp_name_dd.get(dr.company_id or UUID(int=0)),
                        deal_name=dr.name,
                        deal_id=str(dr.id),
                        company_id=str(dr.company_id) if dr.company_id else None,
                    )
                )

    if metric == "demos_converted":
        # Demos converted drilldown — DealStageHistory-based.
        # A deal counts as converted when it enters "qualified_lead" within window.
        # Attributed to deal.sdr_id first, then Company.sdr_id.
        if rep_id:
            conv_dd_rows = (
                await session.execute(
                    select(
                        DealStageHistory.deal_id,
                        DealStageHistory.changed_at,
                    ).where(
                        func.lower(DealStageHistory.to_stage) == "qualified_lead",
                        DealStageHistory.changed_at >= window_start,
                        DealStageHistory.changed_at <= window_end,
                    ).order_by(DealStageHistory.changed_at.desc())
                )
            ).all()
            # Dedup: keep first transition per deal
            seen_conv_dd: set[UUID] = set()
            unique_conv: list = []
            for r in sorted(conv_dd_rows, key=lambda x: x.changed_at):
                if r.deal_id not in seen_conv_dd:
                    seen_conv_dd.add(r.deal_id)
                    unique_conv.append(r)
            conv_dd_deal_ids = {r.deal_id for r in unique_conv}
            conv_dd_deal_rows = (
                await session.execute(
                    select(Deal.id, Deal.name, Deal.company_id, Deal.sdr_id).where(
                        Deal.id.in_(conv_dd_deal_ids)
                    )
                )
            ).all()
            conv_dd_deal_company: dict[UUID, UUID | None] = {r.id: r.company_id for r in conv_dd_deal_rows}
            conv_dd_deal_sdr: dict[UUID, UUID | None] = {r.id: r.sdr_id for r in conv_dd_deal_rows}
            conv_dd_deal_name: dict[UUID, str | None] = {r.id: r.name for r in conv_dd_deal_rows}
            conv_dd_company_ids = {cid for cid in conv_dd_deal_company.values() if cid}
            conv_dd_company_sdr: dict[UUID, UUID | None] = {}
            conv_dd_company_name: dict[UUID, str | None] = {}
            conv_dd_company_region: dict[UUID, str | None] = {}
            if conv_dd_company_ids:
                conv_dd_comp_rows = (
                    await session.execute(
                        select(Company.id, Company.name, Company.sdr_id, Company.region).where(
                            Company.id.in_(conv_dd_company_ids)
                        )
                    )
                ).all()
                conv_dd_company_sdr = {r.id: r.sdr_id for r in conv_dd_comp_rows}
                conv_dd_company_name = {r.id: r.name for r in conv_dd_comp_rows}
                conv_dd_company_region = {r.id: r.region for r in conv_dd_comp_rows}

            demo_has_more = len(unique_conv) > offset + limit
            for hist_row in sorted(unique_conv, key=lambda r: r.changed_at, reverse=True)[offset:offset + limit]:
                cid = conv_dd_deal_company.get(hist_row.deal_id)
                sdr_id = conv_dd_deal_sdr.get(hist_row.deal_id) or (conv_dd_company_sdr.get(cid) if cid else None)
                if sdr_id != rep_id:
                    continue
                if filter_geographies:
                    region_key = _normalize_geography_key(conv_dd_company_region.get(cid) if cid else None)
                    if region_key not in filter_geographies:
                        continue
                rows.append(
                    SalesActivityDrilldownRow(
                        id=hist_row.deal_id,
                        kind="activity",
                        activity_type="qualified_lead",
                        occurred_at=hist_row.changed_at,
                        rep_user_id=rep_id,
                        rep_name=_label_for_rep(rep_id, users)[2],
                        source="pipeline",
                        subject=conv_dd_deal_name.get(hist_row.deal_id),
                        company_name=conv_dd_company_name.get(cid) if cid else None,
                        deal_name=conv_dd_deal_name.get(hist_row.deal_id),
                        deal_id=str(hist_row.deal_id),
                        company_id=str(cid) if cid else None,
                    )
                )

    # ── AE demo funnel drilldowns ────────────────────────────────────────────────
    # Only deals where assigned_to_id == sdr_id, attributed to the AE.
    if metric in {"ae_demos_scheduled", "ae_demos_done", "ae_demos_converted"} and rep_id:
        stage_map = {
            "ae_demos_scheduled": "demo_scheduled",
            "ae_demos_done": "demo_done",
            "ae_demos_converted": "qualified_lead",
        }
        target_stage = stage_map[metric]
        # Self-sourced deals for this AE
        ae_self_rows = (
            await session.execute(
                select(Deal.id, Deal.name, Deal.company_id).where(
                    Deal.assigned_to_id == rep_id,
                    Deal.sdr_id == rep_id,
                )
            )
        ).all()
        ae_self_ids = {r.id for r in ae_self_rows}
        ae_self_name: dict[UUID, str | None] = {r.id: r.name for r in ae_self_rows}
        ae_self_company: dict[UUID, UUID | None] = {r.id: r.company_id for r in ae_self_rows}
        ae_company_ids = {cid for cid in ae_self_company.values() if cid}
        ae_comp_name: dict[UUID, str] = {}
        if ae_company_ids:
            for cr in (await session.execute(
                select(Company.id, Company.name).where(Company.id.in_(ae_company_ids))
            )).all():
                ae_comp_name[cr.id] = cr.name or ""

        ae_hist_rows = (
            await session.execute(
                select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                    func.lower(DealStageHistory.to_stage) == target_stage,
                    DealStageHistory.changed_at >= window_start,
                    DealStageHistory.changed_at <= window_end,
                    DealStageHistory.deal_id.in_(ae_self_ids),
                ).order_by(DealStageHistory.changed_at.desc())
            )
        ).all()
        seen_ae_dd: set[UUID] = set()
        unique_ae: list = []
        for r in sorted(ae_hist_rows, key=lambda x: x.changed_at):
            if r.deal_id not in seen_ae_dd:
                seen_ae_dd.add(r.deal_id)
                unique_ae.append(r)
        demo_has_more = len(unique_ae) > offset + limit
        for hist_row in sorted(unique_ae, key=lambda r: r.changed_at, reverse=True)[offset:offset + limit]:
            cid = ae_self_company.get(hist_row.deal_id)
            rows.append(
                SalesActivityDrilldownRow(
                    id=hist_row.deal_id,
                    kind="activity",
                    activity_type=target_stage,
                    occurred_at=hist_row.changed_at,
                    rep_user_id=rep_id,
                    rep_name=_label_for_rep(rep_id, users)[2],
                    source="pipeline",
                    subject=ae_self_name.get(hist_row.deal_id),
                    company_name=ae_comp_name.get(cid) if cid else None,
                    deal_name=ae_self_name.get(hist_row.deal_id),
                    deal_id=str(hist_row.deal_id),
                    company_id=str(cid) if cid else None,
                )
            )

    rows.sort(key=lambda row: row.occurred_at, reverse=True)
    selected_rep_name = _label_for_rep(rep_id, users)[2] if rep_id else None
    has_more = False
    if metric == "total":
        # Both streams were fetched un-offset, merged and sorted above; paginate
        # the combined list exactly once so a page is never larger than `limit`
        # and interleaved rows are not dropped/duplicated across page boundaries.
        has_more = len(rows) > offset + limit
        rows = rows[offset:offset + limit]
    elif metric == "meetings":
        has_more = meeting_has_more
    elif metric in EMAIL_SEND_METRICS:
        # Pagination happened on the Python-filtered send list because send
        # classification is source/domain based.
        has_more = locals().get("send_has_more", False)
    elif metric == "email_replies":
        # Pagination happened on the Python-filtered reply list (offset/limit +
        # sentinel), so use that list's overflow flag, not the SQL page size.
        has_more = locals().get("reply_has_more", False)
    elif metric in {"demos_scheduled", "demos_done", "demos_converted", "ae_demos_scheduled", "ae_demos_done", "ae_demos_converted"}:
        has_more = demo_has_more
    else:
        has_more = len(locals().get("activities", [])) > limit
    return SalesActivityDrilldownRead(
        generated_at=_utcnow(),
        metric=metric,
        window_days=window_days,
        from_date=from_date,
        to_date=to_date,
        rep_user_id=rep_id,
        rep_name=selected_rep_name,
        returned_count=len(rows),
        has_more=has_more,
        limit=limit,
        offset=offset,
        rows=rows,
    )


@router.get("/sales-dashboard", response_model=SalesDashboardRead)
async def sales_dashboard(
    session: DBSession,
    _user: CurrentUser,
    window_days: Annotated[int, Query(ge=1, le=36500)] = 90,
    rep_id: Annotated[list[UUID], Query()] = [],
    geography: Annotated[list[str], Query()] = [],
    from_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window start")] = None,
    to_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window end")] = None,
    forecast_granularity: Annotated[str, Query(pattern="^(week|month)$", description="Bucket size for forecast_buckets")] = "month",
):
    filter_rep_ids = rep_id or []
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}

    # Snapshot cache — return a recent identical computation if one is fresh.
    cache_key = (
        window_days,
        tuple(sorted(str(r) for r in filter_rep_ids)),
        tuple(sorted(filter_geographies)),
        from_date,
        to_date,
        forecast_granularity,
    )
    cached = _dashboard_cache_get(cache_key)
    if cached is not None:
        return cached

    now = _utcnow()
    today = date.today()

    window_start, window_end = _resolve_analytics_window(window_days, from_date, to_date)
    monthly_unique_funnel = await _load_monthly_unique_funnel(
        session,
        months=12,
        rep_ids=filter_rep_ids or None,
        geography=list(filter_geographies) if filter_geographies else None,
    )

    stage_settings = await get_configured_deal_stages(session)
    stage_map = {stage["id"]: stage for stage in stage_settings}
    active_stage_ids = {stage["id"] for stage in stage_settings if stage.get("group") != "closed"}

    # Weighted pipeline / forecast use admin-configured stage probabilities when
    # present, falling back to the hardcoded defaults per stage. Previously the
    # configured `stage_probabilities` setting was ignored, so any admin tuning
    # (or a custom stage) silently had no effect on weighted numbers.
    analytics_settings = await get_analytics_settings(session)
    configured_probabilities = {
        **DEFAULT_STAGE_PROBABILITIES,
        **(analytics_settings.get("stage_probabilities") or {}),
    }

    def stage_probability(stage_id: str) -> float:
        return float(configured_probabilities.get(stage_id, 0.0))

    deal_stmt = select(
        Deal.id,
        Deal.name,
        Deal.stage,
        Deal.value,
        Deal.close_date_est,
        Deal.days_in_stage,
        Deal.stage_entered_at,
        Deal.assigned_to_id,
        Deal.company_id,
        Deal.created_at,
        Deal.updated_at,
        Deal.geography,
    )
    if filter_rep_ids:
        deal_stmt = deal_stmt.where(Deal.assigned_to_id.in_(filter_rep_ids))
    deal_rows = (await session.execute(deal_stmt)).all()
    # Keep the pre-geography list: the conversion funnel applies region by
    # ACCOUNT (not by Deal.geography), so it reuses these rep-scoped rows
    # instead of re-running an identical full deals query further down.
    raw_deal_rows = deal_rows
    if filter_geographies:
        deal_rows = [row for row in deal_rows if _normalize_geography_key(row.geography) in filter_geographies]
    allowed_deal_ids = {row.id for row in deal_rows}

    contact_stmt = select(
        Contact.id,
        Contact.assigned_to_id,
        Contact.created_at,
        Contact.outreach_lane,
        Contact.sequence_status,
        Contact.instantly_status,
        Company.region.label("company_region"),
    )
    contact_stmt = contact_stmt.outerjoin(Company, Contact.company_id == Company.id)
    if filter_rep_ids:
        contact_stmt = contact_stmt.where(Contact.assigned_to_id.in_(filter_rep_ids))
    contact_rows = (await session.execute(contact_stmt)).all()
    if filter_geographies:
        contact_rows = [row for row in contact_rows if _normalize_geography_key(row.company_region) in filter_geographies]
    allowed_contact_ids = {row.id for row in contact_rows}
    contact_owner = {row.id: row.assigned_to_id for row in contact_rows}
    deal_owner = {row.id: row.assigned_to_id for row in deal_rows}
    # Bound the weekly-activity chart to at most ~1 year of buckets. Totals are
    # still computed over the full window (activities older than this still count
    # toward the leaderboard); only the per-week breakdown is capped so "All time"
    # doesn't generate thousands of weekly buckets per rep.
    week_window_start = max(window_start, window_end - timedelta(weeks=52))
    week_starts = _rolling_week_starts(week_window_start, window_end)
    user_rows = (await session.execute(select(User.id, User.name, User.email, User.role, User.is_active))).all()
    users = {row.id: row.name for row in user_rows}
    user_emails = {row.id: str(row.email or "").strip().lower() for row in user_rows}
    user_roles = {row.id: str(row.role or "").strip().lower() for row in user_rows}
    user_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in user_rows if row.email}
    # email_from local-part -> rep id, for sender-based attribution of email sends.
    # Seed from primary @beacon.li addresses first, then layer in all aliases so
    # that .com/.co senders resolve even when their local-part differs.
    rep_id_by_local: dict[str, UUID] = {
        e.split("@")[0]: uid for uid, e in user_emails.items() if e.endswith("@beacon.li")
    }
    alias_rows_dd = (await session.execute(select(UserAlias.user_id, UserAlias.email))).all()
    for alias in alias_rows_dd:
        alias_email = str(alias.email or "").strip().lower()
        if not alias_email or "@" not in alias_email:
            continue
        alias_local = alias_email.split("@")[0]
        alias_domain = alias_email.split("@")[1]
        if alias_domain in BEACON_SENDING_DOMAINS:
            rep_id_by_local[alias_email] = alias.user_id
            rep_id_by_local.setdefault(alias_local, alias.user_id)
    # AE/SDR users plus any admins explicitly configured for Sales Analytics.
    rep_user_ids = _sales_analytics_rep_user_ids(user_rows, analytics_settings)
    seed_rep_user_ids = _sales_analytics_seed_user_ids(user_rows, analytics_settings)

    activity_rows = (
        await session.execute(
            select(
                Activity.deal_id,
                Activity.contact_id,
                Activity.type,
                Activity.source,
                Activity.medium,
                Activity.event_metadata,
                Activity.created_at,
                Activity.created_by_id,
                Activity.aircall_user_name,
                Activity.call_outcome,
                Activity.call_duration,
                Activity.content,
                Activity.email_from,
                Activity.email_to,
                Activity.email_cc,
                Activity.email_bcc,
                Activity.email_message_id,
                Activity.email_subject,
            ).where(Activity.created_at >= window_start, Activity.created_at <= window_end)
        )
    ).all()
    # Apply rep filter on activities by checking ownership
    if filter_rep_ids:
        activity_rows = [
            row for row in activity_rows
            if _activity_rep_id(row, deal_owner=deal_owner, contact_owner=contact_owner, rep_id_by_local=rep_id_by_local) in filter_rep_ids
        ]
    if filter_geographies:
        activity_rows = [row for row in activity_rows if row.deal_id in allowed_deal_ids or row.contact_id in allowed_contact_ids]

    # Meetings are counted when they happen. Counting future calendar events by
    # created_at inflates rep activity for recurring series that sync in bulk.
    # Manual/legacy records without scheduled_at still fall back to created_at.
    meetings_rows = (
        await session.execute(
            select(
                Meeting.deal_id,
                Meeting.company_id,
                Meeting.owner_user_id,
                Meeting.scheduled_at,
                Meeting.created_at,
                Meeting.status,
                Meeting.external_source,
                Meeting.attendees,
                Meeting.is_internal,
                Meeting.meeting_type,
            ).where(
                Meeting.is_internal.is_(False),
                or_(Meeting.company_id.isnot(None), Meeting.deal_id.isnot(None)),
                or_(
                    (Meeting.scheduled_at >= window_start) & (Meeting.scheduled_at <= window_end),
                    Meeting.scheduled_at.is_(None) & (Meeting.created_at >= window_start) & (Meeting.created_at <= window_end),
                )
            )
        )
    ).all()
    # The conversion funnel needs the same window of meetings WITHOUT the
    # rep/geography filters below — keep the raw result so it doesn't re-run
    # this identical query.
    raw_meeting_rows = meetings_rows
    if filter_rep_ids:
        meetings_rows = [
            row
            for row in meetings_rows
            if any(
                rep_id in filter_rep_ids
                for rep_id in _meeting_rep_ids(row, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email)
            )
        ]
    if filter_geographies:
        meetings_rows = [row for row in meetings_rows if row.deal_id in allowed_deal_ids]

    pipeline_by_stage: dict[str, dict[str, float | int | str]] = {}
    pipeline_by_owner: dict[str, dict] = {}
    velocity_by_stage: dict[str, dict[str, object]] = {}
    forecast_by_month: dict[str, dict[str, float | int | str]] = {}
    # Both week and month buckets are ALWAYS built so the client can toggle
    # granularity instantly without refetching the whole dashboard. `forecast_buckets`
    # in the payload points at whichever the caller requested (back-compat).
    forecast_by_week: dict[str, dict[str, float | int | str]] = {}
    rep_activity: dict[str, dict[str, object]] = {}
    weekly_rep_activity: dict[str, dict[str, object]] = {}

    pipeline_amount = 0.0
    weighted_pipeline_amount = 0.0
    forecast_amount = 0.0
    overdue_close_count = 0
    missing_close_date_count = 0
    stale_deal_count = 0
    active_deals = 0

    for row in deal_rows:
        stage_id = row.stage or "unknown"
        if stage_id not in active_stage_ids:
            continue

        active_deals += 1
        amount = _to_float(row.value)
        probability = stage_probability(stage_id)
        weighted_amount = round(amount * probability, 2)
        pipeline_amount += amount
        weighted_pipeline_amount += weighted_amount

        if not row.close_date_est:
            missing_close_date_count += 1
        else:
            if row.close_date_est < today:
                overdue_close_count += 1
            if row.close_date_est <= today + timedelta(days=window_days):
                forecast_amount += weighted_amount
            month_key = row.close_date_est.strftime("%Y-%m")
            month_bucket = forecast_by_month.setdefault(
                month_key,
                {
                    "key": month_key,
                    "label": _month_label(month_key),
                    "deal_count": 0,
                    "amount": 0.0,
                    "weighted_amount": 0.0,
                },
            )
            month_bucket["deal_count"] += 1
            month_bucket["amount"] += amount
            month_bucket["weighted_amount"] += weighted_amount

            # Week bucket — always built. ISO weeks make the key unambiguous
            # across years (2026-W01, etc.); the label is the Monday of the week.
            iso_year, iso_week, _ = row.close_date_est.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            week_start = row.close_date_est - timedelta(days=row.close_date_est.weekday())
            week_bucket = forecast_by_week.setdefault(
                week_key,
                {
                    "key": week_key,
                    "label": f"Week of {week_start.strftime('%b %d')}",
                    "deal_count": 0,
                    "amount": 0.0,
                    "weighted_amount": 0.0,
                },
            )
            week_bucket["deal_count"] += 1
            week_bucket["amount"] += amount
            week_bucket["weighted_amount"] += weighted_amount

        if (row.days_in_stage or 0) >= 30:
            stale_deal_count += 1

        stage_info = _stage_meta(stage_map, stage_id)
        stage_bucket = pipeline_by_stage.setdefault(
            stage_id,
            {
                "key": stage_id,
                "label": stage_info["label"],
                "color": stage_info["color"],
                "deal_count": 0,
                "amount": 0.0,
                "weighted_amount": 0.0,
            },
        )
        stage_bucket["deal_count"] += 1
        stage_bucket["amount"] += amount
        stage_bucket["weighted_amount"] += weighted_amount

        velocity_bucket = velocity_by_stage.setdefault(
            stage_id,
            {
                "key": stage_id,
                "label": stage_info["label"],
                "color": stage_info["color"],
                "days": [],
                "stale_deals": 0,
            },
        )
        velocity_bucket["days"].append(int(row.days_in_stage or 0))
        if (row.days_in_stage or 0) >= 30:
            velocity_bucket["stale_deals"] += 1

        rep_key, rep_user_id, rep_name = _label_for_rep(row.assigned_to_id, users)
        owner_bucket = pipeline_by_owner.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "deal_count": 0,
                "amount": 0.0,
                "weighted_amount": 0.0,
                "stages": {},
            },
        )
        owner_bucket["deal_count"] += 1
        owner_bucket["amount"] += amount
        owner_bucket["weighted_amount"] += weighted_amount
        owner_stage = owner_bucket["stages"].setdefault(
            stage_id,
            {
                "key": stage_id,
                "label": stage_info["label"],
                "color": stage_info["color"],
                "deal_count": 0,
                "amount": 0.0,
                "weighted_amount": 0.0,
            },
        )
        owner_stage["deal_count"] += 1
        owner_stage["amount"] += amount
        owner_stage["weighted_amount"] += weighted_amount

    for rep_key, owner_bucket in pipeline_by_owner.items():
        # Don't seed a rep-activity row for a non-rep (admin) deal owner — they
        # aren't a sales rep, so they must not appear in the rep leaderboard.
        if owner_bucket["user_id"] is not None and owner_bucket["user_id"] not in rep_user_ids:
            continue
        rep_activity[rep_key] = {
            "key": rep_key,
            "user_id": owner_bucket["user_id"],
            "rep_name": owner_bucket["rep_name"],
            "calls": 0,
            "connected_calls": 0,
            "live_calls": 0,
            "emails": 0,
            "manual_emails": 0,
            "instantly_emails": 0,
            "linkedin_reachouts": 0,
            "meetings": 0,
            "total": 0,
            "active_deals": owner_bucket["deal_count"],
            "pipeline_amount": round(float(owner_bucket["amount"]), 2),
        }
        weekly_rep_activity[rep_key] = {
            "key": rep_key,
            "user_id": owner_bucket["user_id"],
            "rep_name": owner_bucket["rep_name"],
            "active_deals": owner_bucket["deal_count"],
            "pipeline_amount": round(float(owner_bucket["amount"]), 2),
            "weeks": {
                _week_key(week_start): {
                    "week_key": _week_key(week_start),
                    "label": _week_label(week_start),
                    "week_start": week_start.isoformat(),
                    "week_end": (week_start + timedelta(days=6)).isoformat(),
                    "emails": 0,
                    "manual_emails": 0,
                    "instantly_emails": 0,
                    "calls": 0,
                    "connected_calls": 0,
                    "live_calls": 0,
                    "linkedin_reachouts": 0,
                    "meetings": 0,
                    "total": 0,
                }
                for week_start in week_starts
            },
        }

    # Per-rep set of email_message_ids already counted — prevents double-counting
    # when the same email is captured by both personal_email_sync (rep's own inbox)
    # and gmail_sync (Zippy's shared inbox, e.g. because a beacon rep was in To/CC).
    seen_email_ids: dict[str, set[str]] = {}
    seen_manual_email_keys: dict[str, set[tuple]] = {}

    # Touchpoint breakdown tracking:
    # call_contact_seen[rep_key] = set of contact_ids already called (for first vs 2nd+ detection)
    call_contact_seen: dict[str, set[str]] = {}
    # email_contact_counts[rep_key][contact_id] = number of emails sent to that contact
    email_contact_counts: dict[str, dict[str, int]] = {}

    # Sort by created_at so first/subsequent detection is chronologically accurate
    for row in sorted(activity_rows, key=lambda r: r.created_at or datetime.min):
        row_rep_id = _activity_rep_id(
            row,
            deal_owner=deal_owner,
            contact_owner=contact_owner,
            rep_id_by_local=rep_id_by_local,
        )
        if not _is_rep(row_rep_id, rep_user_ids):
            continue
        if filter_rep_ids and row_rep_id not in filter_rep_ids:
            continue
        rep_key, rep_user_id, rep_name = _label_for_rep(row_rep_id, users)
        activity_bucket = rep_activity.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "calls": 0,
                "connected_calls": 0,
                "live_calls": 0,
                "emails": 0,
                "manual_emails": 0,
                "instantly_emails": 0,
                "linkedin_reachouts": 0,
                "meetings": 0,
                "total": 0,
                "active_deals": 0,
                "pipeline_amount": 0.0,
            },
        )
        weekly_bucket = weekly_rep_activity.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "active_deals": int(activity_bucket["active_deals"]),
                "pipeline_amount": float(activity_bucket["pipeline_amount"]),
                "weeks": {
                    _week_key(week_start): {
                        "week_key": _week_key(week_start),
                        "label": _week_label(week_start),
                        "week_start": week_start.isoformat(),
                        "week_end": (week_start + timedelta(days=6)).isoformat(),
                        "emails": 0,
                        "manual_emails": 0,
                        "instantly_emails": 0,
                        "calls": 0,
                        "connected_calls": 0,
                        "live_calls": 0,
                        "linkedin_reachouts": 0,
                        "meetings": 0,
                        "total": 0,
                    }
                    for week_start in week_starts
                },
            },
        )
        week_key = _week_key(_start_of_week(row.created_at))
        week_counts = weekly_bucket["weeks"].get(week_key)
        medium = str(row.medium or "").strip().lower()
        kind = str(row.type or "").strip().lower()
        outcome = str(row.call_outcome or "").strip().lower()
        if medium == "call" or kind == "call":
            activity_bucket["calls"] += 1
            if week_counts is not None:
                week_counts["calls"] += 1
            if outcome in {"connected", "callback", "answered"}:
                activity_bucket["connected_calls"] += 1
                if week_counts is not None:
                    week_counts["connected_calls"] += 1
            if outcome in {"connected", "answered"}:
                activity_bucket["live_calls"] += 1
                if week_counts is not None:
                    week_counts["live_calls"] += 1
            # Breakdown: first call vs 2nd+ per contact
            contact_id_str = str(row.contact_id or "").strip()
            if contact_id_str:
                rep_call_seen = call_contact_seen.setdefault(rep_key, set())
                if contact_id_str not in rep_call_seen:
                    activity_bucket["call_first_attempt"] = activity_bucket.get("call_first_attempt", 0) + 1
                    rep_call_seen.add(contact_id_str)
                else:
                    activity_bucket["call_second_plus"] = activity_bucket.get("call_second_plus", 0) + 1
            else:
                # No contact_id → count as first attempt (can't track uniqueness)
                activity_bucket["call_first_attempt"] = activity_bucket.get("call_first_attempt", 0) + 1
        elif medium == "email" or kind == "email":
            # All email events share type="email"; the sent/opened/replied
            # distinction lives in event_metadata.event_type (Instantly) or
            # source (replies). Count SENT for the emails total, and tally
            # opens/replies separately so the cards can show open/reply rate
            # over emails sent. Personal-sync rows carry no event_type → treated
            # as sent (they ARE real sent/received emails).
            # Email attribution (see _activity_rep_id): a SEND credits the rep
            # who sent it (sender-based across our primary + outreach domains;
            # inbound / non-rep senders are dropped by the _is_rep guard above).
            # A REPLY credits the rep it was sent TO (recipient-based) — i.e. the
            # rep whose outreach earned it; that count is the "Emails In" metric.
            # Opens keep owner attribution and feed the open-rate card only.
            meta = row.event_metadata if isinstance(row.event_metadata, dict) else {}
            event_type = str(meta.get("event_type") or "").strip().lower()
            src = str(row.source or "").strip().lower()
            if event_type == "email_opened":
                activity_bucket["email_opens"] = activity_bucket.get("email_opens", 0) + 1
            elif event_type == "reply_received" or src == "email_reply":
                activity_bucket["email_replies"] = activity_bucket.get("email_replies", 0) + 1
            elif event_type in {"email_sent", ""}:
                # Outbound send by this rep — apply Emails Out rule (1.1):
                # beaconli.com/beaconli.co always count; beacon.li only when
                # Zippy is in CC or BCC, or source is personal_email_sync.
                # Dedup by email_message_id per rep — the same email can be
                # captured by both personal_email_sync and gmail_sync.
                msg_id = str(row.email_message_id or "").strip()
                rep_seen = seen_email_ids.setdefault(rep_key, set())
                if msg_id and msg_id in rep_seen:
                    continue
                if msg_id:
                    rep_seen.add(msg_id)
                email_bucket = _email_out_bucket(row)
                if email_bucket:
                    manual_key = _manual_email_dedupe_key(row, rep_key)
                    if manual_key:
                        rep_manual_seen = seen_manual_email_keys.setdefault(rep_key, set())
                        if manual_key in rep_manual_seen:
                            continue
                        rep_manual_seen.add(manual_key)
                    activity_bucket["emails"] += 1
                    activity_bucket[f"{email_bucket}_emails"] = int(activity_bucket.get(f"{email_bucket}_emails", 0)) + 1
                    if week_counts is not None:
                        week_counts["emails"] += 1
                        week_counts[f"{email_bucket}_emails"] = int(week_counts.get(f"{email_bucket}_emails", 0)) + 1
                    # Breakdown: first email and 3+ emails per contact
                    contact_id_str = str(row.contact_id or "").strip()
                    if contact_id_str:
                        rep_email_counts = email_contact_counts.setdefault(rep_key, {})
                        prev_count = rep_email_counts.get(contact_id_str, 0)
                        new_count = prev_count + 1
                        rep_email_counts[contact_id_str] = new_count
                        if prev_count == 0:
                            activity_bucket["email_first_attempt"] = activity_bucket.get("email_first_attempt", 0) + 1
                        if new_count == 3:
                            # Contact just reached 3 emails — count them in the "min 3 attempts" bucket
                            activity_bucket["email_min_3_attempts"] = activity_bucket.get("email_min_3_attempts", 0) + 1
                    else:
                        # No contact_id → count as first attempt
                        activity_bucket["email_first_attempt"] = activity_bucket.get("email_first_attempt", 0) + 1
        elif medium == "linkedin" or kind == "linkedin":
            activity_bucket["linkedin_reachouts"] += 1
            if week_counts is not None:
                week_counts["linkedin_reachouts"] += 1
            # Parse outcome from Activity.content (set by the frontend log-touch modal)
            li_content = str(row.content or "").strip().lower()
            if "meeting booked via linkedin" in li_content:
                activity_bucket["linkedin_meeting_booked"] = activity_bucket.get("linkedin_meeting_booked", 0) + 1
                activity_bucket["linkedin_accepted"] = activity_bucket.get("linkedin_accepted", 0) + 1
                # meeting booked implies accepted + intro sent
                activity_bucket["linkedin_intro_msg"] = activity_bucket.get("linkedin_intro_msg", 0) + 1
            elif "follow-up" in li_content or "follow_up" in li_content:
                activity_bucket["linkedin_accepted"] = activity_bucket.get("linkedin_accepted", 0) + 1
                activity_bucket["linkedin_followup_msg"] = activity_bucket.get("linkedin_followup_msg", 0) + 1
            elif "accepted" in li_content:
                activity_bucket["linkedin_accepted"] = activity_bucket.get("linkedin_accepted", 0) + 1
                activity_bucket["linkedin_intro_msg"] = activity_bucket.get("linkedin_intro_msg", 0) + 1
            else:
                # "Sent" / connection request (no acceptance yet)
                activity_bucket["linkedin_connection_requested"] = activity_bucket.get("linkedin_connection_requested", 0) + 1
        # Touches = calls + emails + LinkedIn only. A meeting is the OUTCOME of
        # those touches, not a touch itself, so it is excluded from the touch
        # total (meetings stay reported via the separate `meetings` metric).
        activity_bucket["total"] = (
            activity_bucket["calls"]
            + activity_bucket["emails"]
            + activity_bucket["linkedin_reachouts"]
        )
        if week_counts is not None:
            week_counts["total"] = (
                week_counts["calls"]
                + week_counts["emails"]
                + week_counts["linkedin_reachouts"]
            )

    def bump_meeting(row_rep_id: UUID | None, meeting_timestamp: datetime) -> None:
        # Admin/non-rep attendees don't earn a rep meeting credit.
        if not _is_rep(row_rep_id, rep_user_ids):
            return
        rep_key, rep_user_id, rep_name = _label_for_rep(row_rep_id, users)
        meeting_bucket = rep_activity.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "calls": 0,
                "connected_calls": 0,
                "live_calls": 0,
                "emails": 0,
                "manual_emails": 0,
                "instantly_emails": 0,
                "linkedin_reachouts": 0,
                "meetings": 0,
                "total": 0,
                "active_deals": 0,
                "pipeline_amount": 0.0,
            },
        )
        weekly_bucket = weekly_rep_activity.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "active_deals": int(meeting_bucket["active_deals"]),
                "pipeline_amount": float(meeting_bucket["pipeline_amount"]),
                "weeks": {
                    _week_key(week_start): {
                        "week_key": _week_key(week_start),
                        "label": _week_label(week_start),
                        "week_start": week_start.isoformat(),
                        "week_end": (week_start + timedelta(days=6)).isoformat(),
                        "emails": 0,
                        "manual_emails": 0,
                        "instantly_emails": 0,
                        "calls": 0,
                        "connected_calls": 0,
                        "live_calls": 0,
                        "linkedin_reachouts": 0,
                        "meetings": 0,
                        "total": 0,
                    }
                    for week_start in week_starts
                },
            },
        )
        week_counts = weekly_bucket["weeks"].get(_week_key(_start_of_week(meeting_timestamp)))
        meeting_bucket["meetings"] += 1
        # Meetings do not add to the touch total (see activity bump). A meeting
        # bump leaves `total` unchanged — it only increments the meetings metric.
        meeting_bucket["total"] = (
            meeting_bucket["calls"]
            + meeting_bucket["emails"]
            + meeting_bucket["linkedin_reachouts"]
        )
        if week_counts is not None:
            week_counts["meetings"] += 1
            week_counts["total"] = (
                week_counts["calls"]
                + week_counts["emails"]
                + week_counts["linkedin_reachouts"]
            )

    # tl;dv and Google Calendar both ingest the same real-world meeting from
    # different sources, producing two Meeting rows with different
    # external_source values but (near-)identical scheduled_at + customer.
    # Without dedup, recorded calls double-count (the "157 meetings" inflation
    # Rakesh saw in prod). _dedupe_meetings_across_sources collapses them on
    # (company, deal, owner) within a time-tolerance window — so even a 1-minute
    # skew between sources counts once — preferring the tl;dv row.
    # Only count early-funnel meetings (deal ≤ demo_done) — see helper docstring.
    _meeting_within_sales_funnel = await _build_meeting_stage_gate(session, stage_settings)

    candidate_rows = []
    for row in meetings_rows:
        if not _is_crm_linked_meeting(row):
            continue
        if row.status == "cancelled":
            continue
        if not _meeting_within_sales_funnel(row):
            continue
        source = str(row.external_source or "").strip().lower()
        if source not in REAL_MEETING_SOURCES:
            continue
        candidate_rows.append(row)

    deduped_meetings = _dedupe_meetings_across_sources(candidate_rows)

    for row in deduped_meetings:
        meeting_timestamp = _meeting_reporting_timestamp(row, window_end=window_end)
        for row_rep_id in _meeting_rep_ids(row, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email):
            if filter_rep_ids and row_rep_id not in filter_rep_ids:
                continue
            bump_meeting(row_rep_id, meeting_timestamp)

    # ── SDR demo funnel ──────────────────────────────────────────────────────
    # Demos scheduled / done / converted, attributed to the account's SDR (the
    # rep who books and owns the prospect), NOT the AE running the call. This is
    # a dedicated query rather than a reuse of `meetings_rows` for two reasons:
    #  1) meetings_rows is pre-filtered by _meeting_rep_ids (owner/AE/attendees),
    #     which would drop an SDR's demos whenever the dashboard is rep-filtered.
    #  2) demos count regardless of the deal's current stage (the rep-activity
    #     stage gate excludes meetings once an account is deep in POC).
    # A demo "converts" when its account has any deal at qualified_lead or beyond.
    demo_meeting_rows = (
        await session.execute(
            select(
                Meeting.company_id,
                Meeting.deal_id,
                Meeting.scheduled_at,
                Meeting.created_at,
                Meeting.status,
                Meeting.external_source,
            ).where(
                Meeting.is_internal.is_(False),
                func.lower(func.coalesce(Meeting.meeting_type, "")).in_(
                    ["demo", "discovery", "introductory call", "discovery call"]
                ),
                Meeting.company_id.isnot(None),
                or_(
                    (Meeting.scheduled_at >= window_start) & (Meeting.scheduled_at <= window_end),
                    Meeting.scheduled_at.is_(None)
                    & (Meeting.created_at >= window_start)
                    & (Meeting.created_at <= window_end),
                ),
            )
        )
    ).all()
    deduped_demos = _dedupe_meetings_across_sources(
        [row for row in demo_meeting_rows if str(row.external_source or "").strip().lower() in REAL_MEETING_SOURCES]
    )
    demo_company_ids = {row.company_id for row in deduped_demos if row.company_id}

    company_sdr: dict[UUID, UUID | None] = {}
    company_region_for_demo: dict[UUID, str | None] = {}
    converted_company_ids: set[UUID] = set()
    if demo_company_ids:
        sdr_rows = (
            await session.execute(
                select(Company.id, Company.sdr_id, Company.region).where(Company.id.in_(demo_company_ids))
            )
        ).all()
        company_sdr = {row.id: row.sdr_id for row in sdr_rows}
        company_region_for_demo = {row.id: row.region for row in sdr_rows}
        conv_rows = (
            await session.execute(
                select(Deal.company_id, Deal.stage).where(Deal.company_id.in_(demo_company_ids))
            )
        ).all()
        for crow in conv_rows:
            if crow.company_id and str(crow.stage or "").strip().lower() in CONVERTED_DEAL_STAGES:
                converted_company_ids.add(crow.company_id)

    def bump_demo(sdr_id: UUID | None, *, done: bool, converted: bool) -> None:
        if not sdr_id:
            return
        # The account's SDR must be an actual rep (ae/sdr). Without this, a
        # company whose sdr_id points to an admin (or any non-rep) would mint a
        # leaderboard row for them — unlike every other bump path, which gates on
        # _is_rep. The row stays hidden by the client's role filter today, but it
        # should never be created in the first place.
        if not _is_rep(sdr_id, rep_user_ids):
            return
        if filter_rep_ids and sdr_id not in filter_rep_ids:
            return
        rep_key, rep_user_id, rep_name = _label_for_rep(sdr_id, users)
        bucket = rep_activity.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "calls": 0,
                "connected_calls": 0,
                "live_calls": 0,
                "emails": 0,
                "manual_emails": 0,
                "instantly_emails": 0,
                "linkedin_reachouts": 0,
                "meetings": 0,
                "total": 0,
                "active_deals": 0,
                "pipeline_amount": 0.0,
            },
        )
    for row in deduped_demos:
        if filter_geographies:
            region_key = _normalize_geography_key(company_region_for_demo.get(row.company_id))
            if region_key not in filter_geographies:
                continue
        # A demo counts as "done" when explicitly completed/scored, OR when its
        # scheduled time has already passed and it wasn't cancelled. Reps almost
        # never flip status to "completed" (prod: ~41 demo/scheduled vs ~6
        # demo/completed), so requiring the manual flag left demos_done ~0
        # board-wide. Time-based inference treats a past, non-cancelled demo as
        # held. Trade-off: a no-show left in "scheduled" is counted as done.
        # Meeting loop retained for potential future use (demos_converted is now
        # DealStageHistory-based). Nothing is bumped here anymore.
        pass

    # ── Demo Scheduled: deal entered "demo_scheduled" stage within window ──────
    # A deal must actually exist in the Demo Schedule pipeline stage — booking a
    # call alone is not enough. Date = when the deal was created/moved there
    # (DealStageHistory.changed_at), not the meeting date.
    demo_sched_hist = (
        await session.execute(
            select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                func.lower(DealStageHistory.to_stage) == "demo_scheduled",
                DealStageHistory.changed_at >= window_start,
                DealStageHistory.changed_at <= window_end,
            )
        )
    ).all()
    if demo_sched_hist:
        sched_deal_ids = {row.deal_id for row in demo_sched_hist}
        sched_deal_rows = (
            await session.execute(
                select(Deal.id, Deal.company_id, Deal.sdr_id).where(Deal.id.in_(sched_deal_ids))
            )
        ).all()
        sched_deal_company: dict[UUID, UUID | None] = {r.id: r.company_id for r in sched_deal_rows}
        sched_deal_sdr: dict[UUID, UUID | None] = {r.id: r.sdr_id for r in sched_deal_rows}
        sched_company_ids = {cid for cid in sched_deal_company.values() if cid}
        sched_company_sdr: dict[UUID, UUID | None] = {}
        sched_company_region: dict[UUID, str | None] = {}
        if sched_company_ids:
            sched_comp_rows = (
                await session.execute(
                    select(Company.id, Company.sdr_id, Company.region).where(
                        Company.id.in_(sched_company_ids)
                    )
                )
            ).all()
            sched_company_sdr = {r.id: r.sdr_id for r in sched_comp_rows}
            sched_company_region = {r.id: r.region for r in sched_comp_rows}
        # Dedup: one deal should only count once even if it re-entered the stage
        seen_sched_deal_ids: set[UUID] = set()
        for hist_row in sorted(demo_sched_hist, key=lambda r: r.changed_at):
            if hist_row.deal_id in seen_sched_deal_ids:
                continue
            seen_sched_deal_ids.add(hist_row.deal_id)
            cid = sched_deal_company.get(hist_row.deal_id)
            # SDR: deal.sdr_id takes priority, fall back to Company.sdr_id
            sdr_id = sched_deal_sdr.get(hist_row.deal_id) or (sched_company_sdr.get(cid) if cid else None)
            if filter_geographies:
                region_key = _normalize_geography_key(sched_company_region.get(cid) if cid else None)
                if region_key not in filter_geographies:
                    continue
            if not sdr_id or not _is_rep(sdr_id, rep_user_ids):
                continue
            if filter_rep_ids and sdr_id not in filter_rep_ids:
                continue
            rep_key, rep_user_id, rep_name = _label_for_rep(sdr_id, users)
            bucket = rep_activity.setdefault(
                rep_key,
                {
                    "key": rep_key,
                    "user_id": rep_user_id,
                    "rep_name": rep_name,
                    "calls": 0,
                    "connected_calls": 0,
                    "live_calls": 0,
                    "emails": 0,
                    "manual_emails": 0,
                    "instantly_emails": 0,
                    "linkedin_reachouts": 0,
                    "meetings": 0,
                    "total": 0,
                    "active_deals": 0,
                    "pipeline_amount": 0.0,
                },
            )
            bucket["demos_scheduled"] = int(bucket.get("demos_scheduled", 0)) + 1

    # ── Demo Done: deal moved into demo_done within window.
    # We match on to_stage="demo_done" only — the backfill_current migration
    # created just one entry per deal (its current stage), so historical deals
    # in demo_done have no corresponding demo_scheduled entry. Requiring that
    # subquery would exclude all pre-migration data.
    demo_done_hist = (
        await session.execute(
            select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                func.lower(DealStageHistory.to_stage) == "demo_done",
                DealStageHistory.changed_at >= window_start,
                DealStageHistory.changed_at <= window_end,
            )
        )
    ).all()
    if demo_done_hist:
        done_deal_ids = {row.deal_id for row in demo_done_hist}
        done_deal_rows = (
            await session.execute(
                select(Deal.id, Deal.company_id, Deal.sdr_id).where(Deal.id.in_(done_deal_ids))
            )
        ).all()
        done_deal_company: dict[UUID, UUID | None] = {r.id: r.company_id for r in done_deal_rows}
        done_deal_sdr: dict[UUID, UUID | None] = {r.id: r.sdr_id for r in done_deal_rows}
        done_company_ids = {cid for cid in done_deal_company.values() if cid}
        done_company_sdr: dict[UUID, UUID | None] = {}
        done_company_region: dict[UUID, str | None] = {}
        if done_company_ids:
            done_comp_rows = (
                await session.execute(
                    select(Company.id, Company.sdr_id, Company.region).where(
                        Company.id.in_(done_company_ids)
                    )
                )
            ).all()
            done_company_sdr = {r.id: r.sdr_id for r in done_comp_rows}
            done_company_region = {r.id: r.region for r in done_comp_rows}
        seen_done_deal_ids: set[UUID] = set()
        for hist_row in sorted(demo_done_hist, key=lambda r: r.changed_at):
            if hist_row.deal_id in seen_done_deal_ids:
                continue
            seen_done_deal_ids.add(hist_row.deal_id)
            cid = done_deal_company.get(hist_row.deal_id)
            # SDR: deal.sdr_id takes priority, fall back to Company.sdr_id
            sdr_id = done_deal_sdr.get(hist_row.deal_id) or (done_company_sdr.get(cid) if cid else None)
            if filter_geographies:
                region_key = _normalize_geography_key(done_company_region.get(cid) if cid else None)
                if region_key not in filter_geographies:
                    continue
            if not sdr_id or not _is_rep(sdr_id, rep_user_ids):
                continue
            if filter_rep_ids and sdr_id not in filter_rep_ids:
                continue
            rep_key, rep_user_id, rep_name = _label_for_rep(sdr_id, users)
            bucket = rep_activity.setdefault(
                rep_key,
                {
                    "key": rep_key,
                    "user_id": rep_user_id,
                    "rep_name": rep_name,
                    "calls": 0,
                    "connected_calls": 0,
                    "live_calls": 0,
                    "emails": 0,
                    "manual_emails": 0,
                    "instantly_emails": 0,
                    "linkedin_reachouts": 0,
                    "meetings": 0,
                    "total": 0,
                    "active_deals": 0,
                    "pipeline_amount": 0.0,
                },
            )
            bucket["demos_done"] = int(bucket.get("demos_done", 0)) + 1

    # ── Demo Converted: deal entered "qualified_lead" stage within window ───────
    # Converted = deal moved from demo_done → qualified_lead. We match on
    # to_stage="qualified_lead" only (no from_stage constraint) since the
    # backfill_current migration only created one entry per deal.
    conv_hist = (
        await session.execute(
            select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                func.lower(DealStageHistory.to_stage) == "qualified_lead",
                DealStageHistory.changed_at >= window_start,
                DealStageHistory.changed_at <= window_end,
            )
        )
    ).all()
    if conv_hist:
        conv_deal_ids = {row.deal_id for row in conv_hist}
        conv_deal_rows = (
            await session.execute(
                select(Deal.id, Deal.company_id, Deal.sdr_id).where(Deal.id.in_(conv_deal_ids))
            )
        ).all()
        conv_deal_company: dict[UUID, UUID | None] = {r.id: r.company_id for r in conv_deal_rows}
        conv_deal_sdr: dict[UUID, UUID | None] = {r.id: r.sdr_id for r in conv_deal_rows}
        conv_company_ids = {cid for cid in conv_deal_company.values() if cid}
        conv_company_sdr: dict[UUID, UUID | None] = {}
        conv_company_region: dict[UUID, str | None] = {}
        if conv_company_ids:
            conv_comp_rows = (
                await session.execute(
                    select(Company.id, Company.sdr_id, Company.region).where(
                        Company.id.in_(conv_company_ids)
                    )
                )
            ).all()
            conv_company_sdr = {r.id: r.sdr_id for r in conv_comp_rows}
            conv_company_region = {r.id: r.region for r in conv_comp_rows}
        seen_conv_deal_ids: set[UUID] = set()
        for hist_row in sorted(conv_hist, key=lambda r: r.changed_at):
            if hist_row.deal_id in seen_conv_deal_ids:
                continue
            seen_conv_deal_ids.add(hist_row.deal_id)
            cid = conv_deal_company.get(hist_row.deal_id)
            # SDR: deal.sdr_id takes priority, fall back to Company.sdr_id
            sdr_id = conv_deal_sdr.get(hist_row.deal_id) or (conv_company_sdr.get(cid) if cid else None)
            if filter_geographies:
                region_key = _normalize_geography_key(conv_company_region.get(cid) if cid else None)
                if region_key not in filter_geographies:
                    continue
            if not sdr_id or not _is_rep(sdr_id, rep_user_ids):
                continue
            if filter_rep_ids and sdr_id not in filter_rep_ids:
                continue
            rep_key, rep_user_id, rep_name = _label_for_rep(sdr_id, users)
            bucket = rep_activity.setdefault(
                rep_key,
                {
                    "key": rep_key,
                    "user_id": rep_user_id,
                    "rep_name": rep_name,
                    "calls": 0,
                    "connected_calls": 0,
                    "live_calls": 0,
                    "emails": 0,
                    "manual_emails": 0,
                    "instantly_emails": 0,
                    "linkedin_reachouts": 0,
                    "meetings": 0,
                    "total": 0,
                    "active_deals": 0,
                    "pipeline_amount": 0.0,
                },
            )
            bucket["demos_converted"] = int(bucket.get("demos_converted", 0)) + 1

    # ── AE Demo Funnel: only deals where sdr_id == assigned_to_id ───────────────
    # Fetch all three stages in one deal query to avoid repeated round-trips.
    ae_funnel_deal_rows = (
        await session.execute(
            select(Deal.id, Deal.assigned_to_id, Deal.sdr_id, Deal.company_id).where(
                Deal.assigned_to_id.isnot(None),
                Deal.sdr_id.isnot(None),
                Deal.assigned_to_id == Deal.sdr_id,
            )
        )
    ).all()
    # Map deal_id → assigned_to_id for quick lookup; only self-sourced deals.
    ae_deal_ae: dict[UUID, UUID] = {r.id: r.assigned_to_id for r in ae_funnel_deal_rows}
    ae_self_sourced_ids: set[UUID] = set(ae_deal_ae.keys())

    def _bump_ae_demo(deal_id: UUID, field: str) -> None:
        ae_id = ae_deal_ae.get(deal_id)
        if not ae_id or not _is_rep(ae_id, rep_user_ids):
            return
        if filter_rep_ids and ae_id not in filter_rep_ids:
            return
        rep_key, rep_user_id, rep_name = _label_for_rep(ae_id, users)
        bucket = rep_activity.setdefault(
            rep_key,
            {
                "key": rep_key,
                "user_id": rep_user_id,
                "rep_name": rep_name,
                "calls": 0, "connected_calls": 0, "live_calls": 0,
                "emails": 0, "manual_emails": 0, "instantly_emails": 0,
                "linkedin_reachouts": 0, "meetings": 0,
                "total": 0, "active_deals": 0, "pipeline_amount": 0.0,
            },
        )
        bucket[field] = int(bucket.get(field, 0)) + 1

    # AE demos_scheduled
    ae_sched_hist = (
        await session.execute(
            select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                func.lower(DealStageHistory.to_stage) == "demo_scheduled",
                DealStageHistory.changed_at >= window_start,
                DealStageHistory.changed_at <= window_end,
                DealStageHistory.deal_id.in_(ae_self_sourced_ids),
            )
        )
    ).all()
    seen_ae_sched: set[UUID] = set()
    for r in sorted(ae_sched_hist, key=lambda x: x.changed_at):
        if r.deal_id not in seen_ae_sched:
            seen_ae_sched.add(r.deal_id)
            _bump_ae_demo(r.deal_id, "ae_demos_scheduled")

    # AE demos_done
    ae_done_hist = (
        await session.execute(
            select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                func.lower(DealStageHistory.to_stage) == "demo_done",
                DealStageHistory.changed_at >= window_start,
                DealStageHistory.changed_at <= window_end,
                DealStageHistory.deal_id.in_(ae_self_sourced_ids),
            )
        )
    ).all()
    seen_ae_done: set[UUID] = set()
    for r in sorted(ae_done_hist, key=lambda x: x.changed_at):
        if r.deal_id not in seen_ae_done:
            seen_ae_done.add(r.deal_id)
            _bump_ae_demo(r.deal_id, "ae_demos_done")

    # AE demos_converted (qualified_lead)
    ae_conv_hist = (
        await session.execute(
            select(DealStageHistory.deal_id, DealStageHistory.changed_at).where(
                func.lower(DealStageHistory.to_stage) == "qualified_lead",
                DealStageHistory.changed_at >= window_start,
                DealStageHistory.changed_at <= window_end,
                DealStageHistory.deal_id.in_(ae_self_sourced_ids),
            )
        )
    ).all()
    seen_ae_conv: set[UUID] = set()
    for r in sorted(ae_conv_hist, key=lambda x: x.changed_at):
        if r.deal_id not in seen_ae_conv:
            seen_ae_conv.add(r.deal_id)
            _bump_ae_demo(r.deal_id, "ae_demos_converted")

    # Per-rep meeting-booked count from Contact.sequence_status — covers meetings
    # booked through any channel (call, email, LinkedIn). Used for the "meeting
    # booked" stat in the Calls box.
    call_meeting_booked_by_uid: dict[UUID, int] = {}
    if rep_user_ids:
        cmb_rows = (
            await session.execute(
                select(Contact.assigned_to_id, func.count().label("cnt"))
                .where(
                    Contact.assigned_to_id.in_(list(rep_user_ids)),
                    func.lower(Contact.sequence_status) == "meeting_booked",
                )
                .group_by(Contact.assigned_to_id)
            )
        ).all()
        for cmb in cmb_rows:
            if cmb.assigned_to_id:
                call_meeting_booked_by_uid[cmb.assigned_to_id] = cmb.cnt

    # ── upcoming scheduled meetings per rep, bucketed forward from today ─────
    meetings_next_1w_by_uid: dict[UUID, int] = {}
    meetings_next_2w_by_uid: dict[UUID, int] = {}
    meetings_beyond_2w_by_uid: dict[UUID, int] = {}
    if rep_user_ids:
        _today_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        _today_dt = _today_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        _week1_dt = _today_dt + timedelta(days=7)
        _week2_dt = _today_dt + timedelta(days=14)
        upcoming_mtg_rows = (await session.execute(
            select(Meeting.owner_user_id, Meeting.scheduled_at)
            .where(
                Meeting.owner_user_id.in_(list(rep_user_ids)),
                Meeting.status == "scheduled",
                Meeting.scheduled_at >= _today_dt,
            )
        )).all()
        for um in upcoming_mtg_rows:
            uid = um.owner_user_id
            if uid is None:
                continue
            sat = um.scheduled_at
            if sat is None:
                continue
            if sat < _week1_dt:
                meetings_next_1w_by_uid[uid] = meetings_next_1w_by_uid.get(uid, 0) + 1
            elif sat < _week2_dt:
                meetings_next_2w_by_uid[uid] = meetings_next_2w_by_uid.get(uid, 0) + 1
            else:
                meetings_beyond_2w_by_uid[uid] = meetings_beyond_2w_by_uid.get(uid, 0) + 1

    # ── Direct SQL: scheduled meetings with VP/SVP/Head/Chief within window ──
    _DIRECT_SQL_TITLES = ("VP", "SVP", "Head/Chief")
    direct_sql_by_uid: dict[UUID, int] = {}
    if rep_user_ids:
        _direct_sql_end = _today_dt + timedelta(days=window_days)
        direct_sql_rows = (await session.execute(
            select(Meeting.owner_user_id, func.count().label("cnt"))
            .join(Deal, Meeting.deal_id == Deal.id)
            .where(
                Meeting.owner_user_id.in_(list(rep_user_ids)),
                Meeting.status == "scheduled",
                Meeting.scheduled_at >= _today_dt,
                Meeting.scheduled_at <= _direct_sql_end,
                Deal.meeting_booked_with.in_(list(_DIRECT_SQL_TITLES)),
            )
            .group_by(Meeting.owner_user_id)
        )).all()
        for ds in direct_sql_rows:
            if ds.owner_user_id:
                direct_sql_by_uid[ds.owner_user_id] = ds.cnt

    # ── Prospect count & mobile coverage per rep ─────────────────────────────
    # Count contacts owned via sdr_id (for SDR reps) — primary attribution.
    # Fall back to assigned_to_id for reps who appear as AE but not SDR.
    prospect_count_by_uid: dict[UUID, int] = {}
    mobile_count_by_uid: dict[UUID, int] = {}
    if rep_user_ids:
        _uid_list = list(rep_user_ids)
        # sdr_id-based (SDRs / prospecting owners)
        sdr_prospect_rows = (await session.execute(
            select(
                Contact.sdr_id.label("uid"),
                func.count(Contact.id.distinct()).label("cnt"),
                func.count(Contact.id.distinct()).filter(
                    Contact.phone.isnot(None), Contact.phone != ""
                ).label("with_phone"),
            )
            .where(Contact.sdr_id.in_(_uid_list))
            .group_by(Contact.sdr_id)
        )).all()
        for r in sdr_prospect_rows:
            if r.uid:
                prospect_count_by_uid[r.uid] = r.cnt
                mobile_count_by_uid[r.uid] = r.with_phone
        # assigned_to_id-based (AEs) — only fills in reps not already counted
        ae_prospect_rows = (await session.execute(
            select(
                Contact.assigned_to_id.label("uid"),
                func.count(Contact.id.distinct()).label("cnt"),
                func.count(Contact.id.distinct()).filter(
                    Contact.phone.isnot(None), Contact.phone != ""
                ).label("with_phone"),
            )
            .where(Contact.assigned_to_id.in_(_uid_list))
            .group_by(Contact.assigned_to_id)
        )).all()
        for r in ae_prospect_rows:
            if r.uid and r.uid not in prospect_count_by_uid:
                prospect_count_by_uid[r.uid] = r.cnt
                mobile_count_by_uid[r.uid] = r.with_phone

    if seed_rep_user_ids and not filter_geographies:
        for rep_user_id in sorted(seed_rep_user_ids, key=lambda uid: users.get(uid, "").lower()):
            if filter_rep_ids and rep_user_id not in filter_rep_ids:
                continue
            rep_key, _, rep_name = _label_for_rep(rep_user_id, users)
            rep_activity.setdefault(
                rep_key,
                {
                    "key": rep_key,
                    "user_id": rep_user_id,
                    "rep_name": rep_name,
                    "calls": 0,
                    "connected_calls": 0,
                    "live_calls": 0,
                    "emails": 0,
                    "manual_emails": 0,
                    "instantly_emails": 0,
                    "linkedin_reachouts": 0,
                    "meetings": 0,
                    "total": 0,
                    "active_deals": 0,
                    "pipeline_amount": 0.0,
                },
            )
            weekly_rep_activity.setdefault(
                rep_key,
                {
                    "key": rep_key,
                    "user_id": rep_user_id,
                    "rep_name": rep_name,
                    "active_deals": 0,
                    "pipeline_amount": 0.0,
                    "weeks": {
                        _week_key(week_start): {
                            "week_key": _week_key(week_start),
                            "label": _week_label(week_start),
                            "week_start": week_start.isoformat(),
                            "week_end": (week_start + timedelta(days=6)).isoformat(),
                            "emails": 0,
                            "manual_emails": 0,
                            "instantly_emails": 0,
                            "calls": 0,
                            "connected_calls": 0,
                            "live_calls": 0,
                            "linkedin_reachouts": 0,
                            "meetings": 0,
                            "total": 0,
                        }
                        for week_start in week_starts
                    },
                },
            )

    rep_activity_rows = [
        RepActivityRow(
            key=str(bucket["key"]),
            user_id=bucket["user_id"],
            rep_name=str(bucket["rep_name"]),
            role=(user_roles.get(bucket["user_id"]) or None) if bucket["user_id"] else None,
            calls=int(bucket["calls"]),
            connected_calls=int(bucket["connected_calls"]),
            live_calls=int(bucket["live_calls"]),
            emails=int(bucket["emails"]),
            manual_emails=int(bucket.get("manual_emails", 0)),
            instantly_emails=int(bucket.get("instantly_emails", 0)),
            email_opens=int(bucket.get("email_opens", 0)),
            email_replies=int(bucket.get("email_replies", 0)),
            linkedin_reachouts=int(bucket["linkedin_reachouts"]),
            linkedin_accepted=int(bucket.get("linkedin_accepted", 0)),
            linkedin_meeting_booked=int(bucket.get("linkedin_meeting_booked", 0)),
            call_meeting_booked=call_meeting_booked_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            meetings=int(bucket["meetings"]),
            total=int(bucket["total"]),
            active_deals=int(bucket["active_deals"]),
            pipeline_amount=round(float(bucket["pipeline_amount"]), 2),
            demos_scheduled=int(bucket.get("demos_scheduled", 0)),
            demos_done=int(bucket.get("demos_done", 0)),
            demos_converted=int(bucket.get("demos_converted", 0)),
            ae_demos_scheduled=int(bucket.get("ae_demos_scheduled", 0)),
            ae_demos_done=int(bucket.get("ae_demos_done", 0)),
            ae_demos_converted=int(bucket.get("ae_demos_converted", 0)),
            meetings_next_1w=meetings_next_1w_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            meetings_next_2w=meetings_next_2w_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            meetings_beyond_2w=meetings_beyond_2w_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            direct_sql=direct_sql_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            call_first_attempt=int(bucket.get("call_first_attempt", 0)),
            call_second_plus=int(bucket.get("call_second_plus", 0)),
            email_first_attempt=int(bucket.get("email_first_attempt", 0)),
            email_min_3_attempts=int(bucket.get("email_min_3_attempts", 0)),
            linkedin_connection_requested=int(bucket.get("linkedin_connection_requested", 0)),
            linkedin_intro_msg=int(bucket.get("linkedin_intro_msg", 0)),
            linkedin_followup_msg=int(bucket.get("linkedin_followup_msg", 0)),
            total_prospects=prospect_count_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            total_mobile_numbers=mobile_count_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
        )
        for bucket in sorted(
            rep_activity.values(),
            key=lambda value: (-int(value["total"]), -float(value["pipeline_amount"]), str(value["rep_name"]).lower()),
        )
    ]

    rep_totals_by_key = {row.key: row for row in rep_activity_rows}
    rep_weekly_activity_rows = [
        RepWeeklyActivityRow(
            key=str(bucket["key"]),
            user_id=bucket["user_id"],
            rep_name=str(bucket["rep_name"]),
            active_deals=int(bucket["active_deals"]),
            pipeline_amount=round(float(bucket["pipeline_amount"]), 2),
            totals=rep_totals_by_key.get(
                str(bucket["key"]),
                RepActivityRow(
                    key=str(bucket["key"]),
                    user_id=bucket["user_id"],
                    rep_name=str(bucket["rep_name"]),
                    calls=0,
                    connected_calls=0,
                    live_calls=0,
                    emails=0,
                    manual_emails=0,
                    instantly_emails=0,
                    linkedin_reachouts=0,
                    meetings=0,
                    total=0,
                    active_deals=int(bucket["active_deals"]),
                    pipeline_amount=round(float(bucket["pipeline_amount"]), 2),
                ),
            ),
            weeks=[
                RepActivityWeekRow(
                    week_key=str(week["week_key"]),
                    label=str(week["label"]),
                    week_start=str(week["week_start"]),
                    week_end=str(week["week_end"]),
                    emails=int(week["emails"]),
                    manual_emails=int(week.get("manual_emails", 0)),
                    instantly_emails=int(week.get("instantly_emails", 0)),
                    calls=int(week["calls"]),
                    connected_calls=int(week["connected_calls"]),
                    live_calls=int(week["live_calls"]),
                    linkedin_reachouts=int(week["linkedin_reachouts"]),
                    meetings=int(week["meetings"]),
                    total=int(week["total"]),
                )
                for week in bucket["weeks"].values()
            ],
        )
        for bucket in sorted(
            weekly_rep_activity.values(),
            key=lambda value: (
                -rep_totals_by_key.get(str(value["key"]), RepActivityRow(
                    key=str(value["key"]),
                    rep_name=str(value["rep_name"]),
                    calls=0,
                    connected_calls=0,
                    live_calls=0,
                    emails=0,
                    manual_emails=0,
                    instantly_emails=0,
                    linkedin_reachouts=0,
                    meetings=0,
                    total=0,
                    active_deals=int(value["active_deals"]),
                    pipeline_amount=float(value["pipeline_amount"]),
                )).total,
                -float(value["pipeline_amount"]),
                str(value["rep_name"]).lower(),
            ),
        )
    ]

    pipeline_stage_rows = [
        StageBucket(
            key=stage["key"],
            label=stage["label"],
            color=stage["color"],
            deal_count=int(stage["deal_count"]),
            amount=round(float(stage["amount"]), 2),
            weighted_amount=round(float(stage["weighted_amount"]), 2),
        )
        for stage in sorted(
            pipeline_by_stage.values(),
            key=lambda value: (-float(value["amount"]), -int(value["deal_count"])),
        )
    ]

    owner_rows = [
        PipelineOwnerRow(
            key=str(bucket["key"]),
            user_id=bucket["user_id"],
            rep_name=str(bucket["rep_name"]),
            deal_count=int(bucket["deal_count"]),
            amount=round(float(bucket["amount"]), 2),
            weighted_amount=round(float(bucket["weighted_amount"]), 2),
            stages=[
                StageBucket(
                    key=str(stage["key"]),
                    label=str(stage["label"]),
                    color=str(stage["color"]),
                    deal_count=int(stage["deal_count"]),
                    amount=round(float(stage["amount"]), 2),
                    weighted_amount=round(float(stage["weighted_amount"]), 2),
                )
                for stage in sorted(
                    bucket["stages"].values(),
                    key=lambda value: (-float(value["amount"]), str(value["label"]).lower()),
                )
            ],
        )
        for bucket in sorted(
            pipeline_by_owner.values(),
            key=lambda value: (-float(value["amount"]), str(value["rep_name"]).lower()),
        )
    ]

    velocity_rows = [
        VelocityRow(
            key=str(bucket["key"]),
            label=str(bucket["label"]),
            color=str(bucket["color"]),
            deal_count=len(bucket["days"]),
            average_days_in_stage=_average(bucket["days"]),
            stale_deals=int(bucket["stale_deals"]),
        )
        for bucket in sorted(
            velocity_by_stage.values(),
            key=lambda value: (-_average(value["days"]), -len(value["days"])),
        )
    ]

    forecast_rows = [
        ForecastRow(
            key=str(bucket["key"]),
            label=str(bucket["label"]),
            deal_count=int(bucket["deal_count"]),
            amount=round(float(bucket["amount"]), 2),
            weighted_amount=round(float(bucket["weighted_amount"]), 2),
        )
        for bucket in sorted(forecast_by_month.values(), key=lambda value: str(value["key"]))
    ]

    forecast_week_rows = [
        ForecastRow(
            key=str(bucket["key"]),
            label=str(bucket["label"]),
            deal_count=int(bucket["deal_count"]),
            amount=round(float(bucket["amount"]), 2),
            weighted_amount=round(float(bucket["weighted_amount"]), 2),
        )
        for bucket in sorted(forecast_by_week.values(), key=lambda value: str(value["key"]))
    ]
    # `forecast_buckets` mirrors the requested granularity for back-compat; the
    # client also receives both forecast_by_month and forecast_by_week so it can
    # switch week/month with no extra request.
    forecast_bucket_rows = forecast_week_rows if forecast_granularity == "week" else forecast_rows

    # ── Conversion funnel — ACCOUNT-based, uniformly region-filtered ─────────
    # Each step counts distinct ACCOUNTS (companies), all filtered by the account's
    # region (Company.region). Before: 'Lead' counted contacts (~3.4k prospects),
    # and the deal/meeting steps filtered on Deal.geography (~80% null in prod) — so
    # the funnel mixed entity types and the America / Rest-of-World filter applied
    # unevenly (it also dropped company-only meetings). Counting accounts on one
    # well-populated region source makes the funnel and its filter coherent.
    company_meta_rows = (
        await session.execute(
            select(Company.id, Company.region, Company.created_at, Company.assigned_to_id, Company.sdr_id)
        )
    ).all()
    funnel_company_region = {row.id: _normalize_geography_key(row.region) for row in company_meta_rows}

    def _funnel_account_in_geo(company_id) -> bool:
        return (not filter_geographies) or funnel_company_region.get(company_id) in filter_geographies

    # Lead = accounts sourced (company created) in the window; rep filter scopes to
    # the account's AE/SDR so a rep-filtered funnel shows that rep's accounts.
    lead_accounts = {
        row.id
        for row in company_meta_rows
        if row.created_at is not None
        and window_start <= row.created_at <= window_end
        and _funnel_account_in_geo(row.id)
        and (not filter_rep_ids or row.assigned_to_id in filter_rep_ids or row.sdr_id in filter_rep_ids)
    }

    # Proposal / Closed Won = distinct accounts with a deal at the stage, last
    # updated in the window. Rep-scoped by deal owner, region by the account.
    # Reuses the rep-scoped, pre-geography deal fetch from above (raw_deal_rows
    # carries id/company_id/stage/updated_at with the identical rep filter) so
    # it isn't perturbed by the Deal.geography pre-filter and doesn't re-scan
    # the deals table.
    funnel_deal_rows = raw_deal_rows
    funnel_deal_company = {row.id: row.company_id for row in funnel_deal_rows}
    proposal_accounts: set = set()
    won_accounts: set = set()
    for row in funnel_deal_rows:
        cid = row.company_id
        if (
            cid is None
            or row.updated_at < window_start
            or row.updated_at > window_end
            or not _funnel_account_in_geo(cid)
        ):
            continue
        if row.stage in PROPOSAL_STAGES:
            proposal_accounts.add(cid)
        if row.stage == "closed_won":
            won_accounts.add(cid)

    # Meeting = distinct accounts with a qualifying meeting in the window. Dedicated
    # rep-scoped query (NOT pre-filtered by Deal.geography) so the region filter is
    # applied by account, matching the other steps; same gating + cross-source dedup
    # as the rep-activity meeting count.
    # Identical window/columns to the rep-activity meetings query above —
    # reuse its raw (pre rep/geo filter) result instead of re-querying.
    funnel_meeting_rows = raw_meeting_rows
    funnel_meeting_candidates = [
        row
        for row in funnel_meeting_rows
        if _is_crm_linked_meeting(row)
        and row.status != "cancelled"
        and _meeting_within_sales_funnel(row)
        and str(row.external_source or "").strip().lower() in REAL_MEETING_SOURCES
    ]
    meeting_accounts: set = set()
    for row in _dedupe_meetings_across_sources(funnel_meeting_candidates):
        cid = row.company_id or funnel_deal_company.get(row.deal_id)
        if cid is None or not _funnel_account_in_geo(cid):
            continue
        if filter_rep_ids:
            mreps = set(_meeting_rep_ids(row, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email))
            if not (mreps & filter_rep_ids):
                continue
        meeting_accounts.add(cid)

    leads_count = len(lead_accounts)
    meetings_count = len(meeting_accounts)
    proposal_count = len(proposal_accounts)
    closed_won_count = len(won_accounts)

    # Milestone-based deduplicated counts for the selected window
    # Each company counted only once (first time it reached the milestone)
    AEUser = aliased(User)
    milestone_stmt = (
        select(
            CompanyStageMilestone.milestone_key,
            CompanyStageMilestone.first_reached_at,
            Deal.name.label("deal_name"),
            Deal.value.label("deal_value"),
            Deal.close_date_est.label("close_date_est"),
            Deal.geography.label("deal_geography"),
            Company.name.label("company_name"),
            AEUser.name.label("ae_name"),
            Company.sdr_name.label("sdr_name"),
        )
        .outerjoin(Deal, CompanyStageMilestone.deal_id == Deal.id)
        .outerjoin(Company, CompanyStageMilestone.company_id == Company.id)
        .outerjoin(AEUser, Deal.assigned_to_id == AEUser.id)
        .where(
            CompanyStageMilestone.first_reached_at >= window_start,
            CompanyStageMilestone.first_reached_at <= window_end,
            CompanyStageMilestone.milestone_key.in_(["demo_scheduled", "qualified_lead", "demo_done", "poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "workshop_msa", "closed_won"]),
        )
    )
    if filter_rep_ids:
        milestone_stmt = milestone_stmt.where(Deal.assigned_to_id.in_(filter_rep_ids))
    milestone_summary_rows = (await session.execute(milestone_stmt)).all()
    if filter_geographies:
        milestone_summary_rows = [
            row for row in milestone_summary_rows
            if _normalize_geography_key(row.deal_geography) in filter_geographies
        ]

    ms_demo_scheduled = sum(1 for r in milestone_summary_rows if r.milestone_key == "demo_scheduled")
    ms_qualified_lead = sum(1 for r in milestone_summary_rows if r.milestone_key == "qualified_lead")
    ms_demo_done = sum(1 for r in milestone_summary_rows if r.milestone_key == "demo_done")
    ms_poc_agreed = sum(1 for r in milestone_summary_rows if r.milestone_key == "poc_agreed")
    ms_poc_wip = sum(1 for r in milestone_summary_rows if r.milestone_key == "poc_wip")
    ms_poc_done = sum(1 for r in milestone_summary_rows if r.milestone_key == "poc_done")
    ms_commercial_negotiation = sum(1 for r in milestone_summary_rows if r.milestone_key == "commercial_negotiation")
    ms_workshop_msa = sum(1 for r in milestone_summary_rows if r.milestone_key == "workshop_msa")
    ms_closed_won = sum(1 for r in milestone_summary_rows if r.milestone_key == "closed_won")
    ms_closed_won_value = sum(_to_float(r.deal_value) for r in milestone_summary_rows if r.milestone_key == "closed_won")

    # Previous window of equal length, immediately before this one — powers the
    # period-over-period trend deltas on the milestone KPI cards. Same rep +
    # geography filters so the comparison is apples-to-apples.
    prev_window_len = window_end - window_start
    prev_window_end = window_start
    prev_window_start = window_start - prev_window_len
    prev_stmt = (
        select(
            CompanyStageMilestone.milestone_key,
            Deal.value.label("deal_value"),
            Deal.geography.label("deal_geography"),
        )
        .outerjoin(Deal, CompanyStageMilestone.deal_id == Deal.id)
        .where(
            CompanyStageMilestone.first_reached_at >= prev_window_start,
            CompanyStageMilestone.first_reached_at < prev_window_end,
            CompanyStageMilestone.milestone_key.in_(["demo_scheduled", "qualified_lead", "demo_done", "poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "workshop_msa", "closed_won"]),
        )
    )
    if filter_rep_ids:
        prev_stmt = prev_stmt.where(Deal.assigned_to_id.in_(filter_rep_ids))
    prev_rows = (await session.execute(prev_stmt)).all()
    if filter_geographies:
        prev_rows = [r for r in prev_rows if _normalize_geography_key(r.deal_geography) in filter_geographies]

    prev_demo_scheduled = sum(1 for r in prev_rows if r.milestone_key == "demo_scheduled")
    prev_qualified_lead = sum(1 for r in prev_rows if r.milestone_key == "qualified_lead")
    prev_demo_done = sum(1 for r in prev_rows if r.milestone_key == "demo_done")
    prev_poc_agreed = sum(1 for r in prev_rows if r.milestone_key == "poc_agreed")
    prev_poc_wip = sum(1 for r in prev_rows if r.milestone_key == "poc_wip")
    prev_poc_done = sum(1 for r in prev_rows if r.milestone_key == "poc_done")
    prev_commercial_negotiation = sum(1 for r in prev_rows if r.milestone_key == "commercial_negotiation")
    prev_workshop_msa = sum(1 for r in prev_rows if r.milestone_key == "workshop_msa")
    prev_closed_won = sum(1 for r in prev_rows if r.milestone_key == "closed_won")
    prev_closed_won_value = sum(_to_float(r.deal_value) for r in prev_rows if r.milestone_key == "closed_won")
    ms_milestone_deals = [
        MilestoneDealRow(
            milestone_key=r.milestone_key,
            deal_name=r.deal_name,
            company_name=r.company_name,
            reached_at=r.first_reached_at.strftime("%Y-%m-%d"),
            close_date_est=r.close_date_est.strftime("%Y-%m-%d") if r.close_date_est else None,
            deal_value=_to_float(r.deal_value) if r.deal_value else None,
            assigned_ae=r.ae_name or None,
            assigned_sdr=r.sdr_name or None,
        )
        for r in milestone_summary_rows
    ]

    funnel_rows = [
        FunnelStep(key="lead", label="Lead", count=leads_count),
        FunnelStep(
            key="meeting",
            label="Meeting",
            count=meetings_count,
            conversion_from_previous=_conversion(leads_count, meetings_count),
        ),
        FunnelStep(
            key="proposal",
            label="Proposal",
            count=proposal_count,
            conversion_from_previous=_conversion(meetings_count, proposal_count),
        ),
        FunnelStep(
            key="closed_won",
            label="Closed Won",
            count=closed_won_count,
            conversion_from_previous=_conversion(proposal_count, closed_won_count),
        ),
    ]

    highlights: list[SalesHighlight] = []
    if rep_activity_rows:
        top_rep = rep_activity_rows[0]
        if top_rep.total > 0:
            highlights.append(
                SalesHighlight(
                    key="top_rep_activity",
                    message=f"{top_rep.rep_name} leads activity with {top_rep.total} touches in the last {window_days} days.",
                    title=f"{top_rep.rep_name} owned deals",
                    subtitle="Current pipeline owned by the rep highlighted in the readout.",
                    drilldown=SalesHighlightDrilldown(rep_user_id=top_rep.user_id),
                )
            )
    if velocity_rows:
        slowest_stage = velocity_rows[0]
        highlights.append(
            SalesHighlight(
                key="slowest_stage",
                message=f"{slowest_stage.label} is the slowest stage, averaging {slowest_stage.average_days_in_stage:.1f} days in stage.",
                title=f"{slowest_stage.label} deal aging",
                subtitle="Deals in the slowest stage, sorted by time spent in stage.",
                drilldown=SalesHighlightDrilldown(stage_key=slowest_stage.key),
            )
        )
    if overdue_close_count > 0:
        highlights.append(
            SalesHighlight(
                key="overdue_close_dates",
                message=f"{overdue_close_count} open deals have overdue close dates and need forecast cleanup.",
                title="Overdue close dates",
                subtitle="Open deals whose expected close date is already in the past.",
                drilldown=SalesHighlightDrilldown(overdue_close_date=True),
            )
        )
    if missing_close_date_count > 0:
        highlights.append(
            SalesHighlight(
                key="missing_close_dates",
                message=f"{missing_close_date_count} active deals are missing an expected close date.",
                title="Missing close dates",
                subtitle="Active deals that still do not have an expected close date.",
                drilldown=SalesHighlightDrilldown(missing_close_date=True),
            )
        )
    if forecast_rows:
        strongest_month = max(forecast_rows, key=lambda row: row.weighted_amount)
        if strongest_month.weighted_amount > 0:
            highlights.append(
                SalesHighlight(
                    key="strongest_forecast_month",
                    message=f"{strongest_month.label} carries the strongest weighted forecast at ${strongest_month.weighted_amount:,.0f}.",
                    title=f"{strongest_month.label} forecast coverage",
                    subtitle="Deals expected to close in the strongest weighted forecast month.",
                    drilldown=SalesHighlightDrilldown(close_month=strongest_month.key),
                )
            )
    if not highlights:
        highlights.append(
            SalesHighlight(
                key="no_signal_yet",
                message="Sales analytics is live, but the workspace needs more activity data before trends stand out.",
                title="Beacon Readout",
                subtitle="No related records are available for this readout item yet.",
            )
        )

    average_deal_size = round(pipeline_amount / active_deals, 2) if active_deals else 0.0

    # ── Accounts by status ───────────────────────────────────────────────────
    # Distribution of sourced accounts across the manual account_status field,
    # scoped to the selected reps (owner or SDR) and geography. Always emits the
    # 5 canonical statuses so the UI stays stable; "No status" only if non-zero.
    status_select = select(
        Company.account_status, Company.region, Company.assigned_to_id, Company.sdr_id
    )
    if filter_rep_ids:
        status_select = status_select.where(
            or_(Company.assigned_to_id.in_(filter_rep_ids), Company.sdr_id.in_(filter_rep_ids))
        )
    status_counts: dict[str, int] = {}
    for srow in (await session.execute(status_select)).all():
        if filter_geographies and _normalize_geography_key(srow.region) not in filter_geographies:
            continue
        key = str(srow.account_status or "").strip().lower() or "unset"
        status_counts[key] = status_counts.get(key, 0) + 1
    accounts_by_status = [
        AccountStatusRow(key=key, label=label, count=status_counts.get(key, 0))
        for key, label in ACCOUNT_STATUS_LABELS.items()
    ]
    if status_counts.get("unset"):
        accounts_by_status.append(
            AccountStatusRow(key="unset", label="No status", count=status_counts["unset"])
        )

    result = SalesDashboardRead(
        generated_at=now,
        window_days=window_days,
        from_date=from_date,
        to_date=to_date,
        summary=SalesSummary(
            pipeline_amount=round(pipeline_amount, 2),
            weighted_pipeline_amount=round(weighted_pipeline_amount, 2),
            forecast_amount=round(forecast_amount, 2),
            active_deals=active_deals,
            average_deal_size=average_deal_size,
            overdue_close_count=overdue_close_count,
            missing_close_date_count=missing_close_date_count,
            stale_deal_count=stale_deal_count,
            demo_scheduled_count=ms_demo_scheduled,
            qualified_lead_count=ms_qualified_lead,
            demo_done_count=ms_demo_done,
            poc_agreed_count=ms_poc_agreed,
            poc_wip_count=ms_poc_wip,
            poc_done_count=ms_poc_done,
            commercial_negotiation_count=ms_commercial_negotiation,
            workshop_msa_count=ms_workshop_msa,
            closed_won_count=ms_closed_won,
            closed_won_value=round(ms_closed_won_value, 2),
            milestone_deals=ms_milestone_deals,
            prev_demo_scheduled_count=prev_demo_scheduled,
            prev_qualified_lead_count=prev_qualified_lead,
            prev_demo_done_count=prev_demo_done,
            prev_poc_agreed_count=prev_poc_agreed,
            prev_poc_wip_count=prev_poc_wip,
            prev_poc_done_count=prev_poc_done,
            prev_commercial_negotiation_count=prev_commercial_negotiation,
            prev_workshop_msa_count=prev_workshop_msa,
            prev_closed_won_count=prev_closed_won,
            prev_closed_won_value=round(prev_closed_won_value, 2),
        ),
        highlights=highlights[:5],
        rep_activity=rep_activity_rows,
        rep_weekly_activity=rep_weekly_activity_rows,
        pipeline_by_stage=pipeline_stage_rows,
        pipeline_by_owner=owner_rows,
        velocity_by_stage=velocity_rows,
        forecast_by_month=forecast_rows,
        forecast_by_week=forecast_week_rows,
        forecast_buckets=forecast_bucket_rows,
        forecast_granularity=forecast_granularity,
        conversion_funnel=funnel_rows,
        monthly_unique_funnel=monthly_unique_funnel,
        accounts_by_status=accounts_by_status,
        quota=QuotaState(
            configured=False,
            title="Quota setup required",
            message="Add rep or team targets to unlock quota attainment and gap-to-goal charts.",
        ),
    )
    _dashboard_cache_set(cache_key, result)
    return result
