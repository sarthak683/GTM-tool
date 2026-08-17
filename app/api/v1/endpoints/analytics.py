from __future__ import annotations

import time
import re
from collections import defaultdict
# NOTE: never `from datetime import time` here — it shadows the stdlib `time`
# module imported above, and the dashboard cache's time.monotonic() then 500s
# every uncached request (bit us on 2026-08-16; caught in pre-deploy smoke).
from datetime import date, datetime, timezone, timedelta
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.core.analytics_defaults import DEFAULT_STAGE_PROBABILITIES
from app.core.dependencies import CurrentUser, DBSession
from app.models.activity import Activity
from app.models.company import Company
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.deal_stage_history import DealStageHistory
from app.models.meeting import Meeting
from app.models.user import User
from app.models.user_alias import UserAlias
from app.services.analytics_settings import get_analytics_settings
from app.services.company_stage_milestones import MILESTONE_LABELS, backfill_company_stage_milestones
from app.services.deal_stages import get_configured_deal_stages
from app.services.zippy_tagging import (
    BEACON_SENDING_DOMAINS as _SHARED_SENDING_DOMAINS,
    is_zippy_address,
)

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

def _is_rep(rep_id, rep_user_ids) -> bool:
    """True if this attributed id should count as rep activity.

    None (Unassigned) is preserved as-is — it's a separate existing bucket, not
    a non-rep person. Any concrete id must belong to an ae/sdr user.
    """
    return rep_id is None or rep_id in rep_user_ids

# Meeting dedupe/attribution definitions are SHARED with the performance
# scorecard — one metric dictionary, two query engines. Never redefine them
# here; both surfaces must agree on what a meeting IS.
from zoneinfo import ZoneInfo  # noqa: E402

from app.services.metric_definitions import (  # noqa: E402
    dedupe_meetings_across_sources as _dedupe_meetings_across_sources,
    local_midnight_utc,
    meeting_attendee_rep_ids as _meeting_attendee_rep_ids,
    meeting_rep_ids as _meeting_rep_ids,
    workspace_today,
    workspace_zoneinfo,
)


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
    email_meeting_booked: int = 0
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
    # Demo reschedules: deal in demo_scheduled stage with close_date_est updated
    # after it was already set. Logged as Activity(type="demo_rescheduled").
    demos_rescheduled: int = 0
    # Same headline metrics for the equal-length window immediately preceding
    # window_start, with identical rep/geography scoping — powers the
    # period-over-period deltas on the leaderboard rows. Follows the prev_*
    # naming convention established on SalesSummary.
    prev_total: int = 0
    prev_calls: int = 0
    prev_connected_calls: int = 0
    prev_live_calls: int = 0
    prev_emails: int = 0
    prev_linkedin_reachouts: int = 0
    prev_meetings: int = 0


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


class QuotaState(BaseModel):
    configured: bool
    title: str
    message: str
    # Target maps straight from analytics_settings (key → metric → target;
    # keys are the settings' grouping — role buckets like "sdr"/"ae" today,
    # rep ids if an admin configures per-rep maps). All optional so existing
    # clients and cached payloads keep validating.
    weekly_targets: Optional[dict[str, dict[str, float]]] = None
    monthly_targets: Optional[dict[str, dict[str, float]]] = None
    # Simple team aggregate: per metric, the sum of that metric's target across
    # every key in the corresponding map — the "team goal line" value.
    team_weekly_targets: Optional[dict[str, float]] = None
    team_monthly_targets: Optional[dict[str, float]] = None


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


class MeetingBucketTotals(BaseModel):
    meetings_next_1w: int = 0
    meetings_next_2w: int = 0
    meetings_beyond_2w: int = 0
    direct_sql: int = 0
    demo_rescheduled: int = 0


class SalesDashboardRead(BaseModel):
    generated_at: datetime
    window_days: int
    # The resolved window, inclusive on both ends — always populated, including
    # for preset picks where the request carried no explicit dates.
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    # Bucket size of rep_weekly_activity, derived from the RESOLVED window
    # (≤14 days → daily), so the client can label the chart correctly.
    activity_bucket_granularity: Literal["daily", "weekly"] = "weekly"
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
    monthly_unique_funnel: list[MonthlyUniqueFunnelRow]
    quota: QuotaState
    # Global (unique) counts for the Output "Meetings Booked" buckets. Each demo
    # is counted once here so the week buckets reconcile to the Demo Scheduled
    # total; rep_activity keeps per-rep attribution for the leaderboards.
    meeting_bucket_totals: "MeetingBucketTotals" = None


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
    # Deal-level AE/SDR assignment, populated for pipeline-backed drilldowns
    # (demo funnel) so leaderboard rows show who owns the deal.
    ae_name: Optional[str] = None
    sdr_name: Optional[str] = None


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


def _period_key(period_start: date) -> str:
    return period_start.isoformat()


def _period_label(period_start: date, daily: bool) -> str:
    if daily:
        return period_start.strftime("%b %d")
    return _week_label(period_start)


def _rolling_week_starts(start: datetime, end: datetime) -> list[date]:
    first_week = _start_of_week(start)
    last_week = _start_of_week(end)
    weeks: list[date] = []
    cursor = first_week
    while cursor <= last_week:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    return weeks


def _rolling_period_starts(start: datetime, end: datetime, *, daily: bool, bucket_end_date: date | None = None) -> list[date]:
    if daily:
        end_date = bucket_end_date or end.date()
        cursor = start.date()
        periods: list[date] = []
        while cursor <= end_date:
            periods.append(cursor)
            cursor += timedelta(days=1)
        return periods
    return _rolling_week_starts(start, end)


# Windows spanning at most this many days get DAILY activity buckets; anything
# longer gets weekly buckets. 14 keeps every short preset (Today/2D/3D/5D/1W/2W)
# on per-day bars instead of collapsing into a single "Week of …" bar.
_DAILY_BUCKET_MAX_WINDOW_DAYS = 14


def _activity_bucket_granularity(window_start: datetime, window_end: datetime) -> Literal["daily", "weekly"]:
    """Bucket size for the rep-activity chart, derived from the RESOLVED window.

    This must be computed AFTER _resolve_analytics_window, from its output.
    Deriving it from the raw ``window_days`` param broke two ways: the short
    presets (1/2/3/5/14 days) fell through the old ``window_days == 7`` check
    to weekly bucketing, and explicit ``from_date``/``to_date`` overrides were
    ignored entirely (a 3-day custom range still bucketed weekly).
    """
    return "daily" if (window_end - window_start).days <= _DAILY_BUCKET_MAX_WINDOW_DAYS else "weekly"


def _resolve_analytics_window(
    window_days: int,
    from_date: Optional[str],
    to_date: Optional[str],
    tz: Optional[ZoneInfo] = None,
) -> tuple[datetime, datetime]:
    """Window boundaries as naive-UTC instants, with DAYS meaning days in the
    WORKSPACE timezone.

    Storage stays UTC; only the boundaries are computed in the workspace zone
    (analytics settings ``workspace_timezone``) and converted back. Before
    this, every boundary was a UTC midnight, so activity between 00:00 and
    05:30 IST landed on the previous reporting day for an IST workspace.
    """
    now = _utcnow()
    tz = tz or ZoneInfo("UTC")

    def _parse_day(value: str) -> date:
        # Accept plain dates AND full datetimes (older clients sent either);
        # either way the DAY in the workspace zone is what defines the window.
        try:
            return date.fromisoformat(value)
        except ValueError:
            return datetime.fromisoformat(value).date()

    try:
        if from_date:
            window_start = local_midnight_utc(_parse_day(from_date), tz)
        else:
            # Midnight-aligned in the workspace zone, matching the explicit
            # from_date branch. "N days" counts TODAY as day one — so
            # window_days=1 is exactly "today so far", and "Last 7 days"
            # covers the same 7 calendar days as an explicit 7-day range
            # ending today (the old -window_days form silently spanned 8).
            today_local = workspace_today(tz, now)
            window_start = local_midnight_utc(today_local - timedelta(days=window_days - 1), tz)
        if to_date:
            window_end = local_midnight_utc(_parse_day(to_date) + timedelta(days=1), tz)
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


def _label_for_rep(rep_id: UUID | None, users: dict[UUID, str]) -> tuple[str, Optional[UUID], str]:
    if rep_id and rep_id in users:
        return str(rep_id), rep_id, users[rep_id]
    return "unassigned", None, "Unassigned"


def _coerce_target_map(raw) -> dict[str, dict[str, float]]:
    """Defensive copy of a targets blob: keep only numeric metric values."""
    out: dict[str, dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for key, metrics in raw.items():
        if not isinstance(metrics, dict):
            continue
        clean: dict[str, float] = {}
        for metric, value in metrics.items():
            try:
                clean[str(metric)] = float(value)
            except (TypeError, ValueError):
                continue
        if clean:
            out[str(key)] = clean
    return out


def _team_target_totals(targets: dict[str, dict[str, float]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for metrics in targets.values():
        for metric, value in metrics.items():
            totals[metric] = round(totals.get(metric, 0.0) + value, 2)
    return totals


def _build_quota_state(analytics_settings: dict | None) -> QuotaState:
    """Quota payload from analytics_settings' weekly/monthly target maps.

    ``configured`` is True as soon as ANY positive target exists in either map
    (the defaults ship with targets, so a fresh workspace is configured). The
    raw maps plus per-metric team sums ride along so the client can draw goal
    lines on the rep-activity charts without a second request.
    """
    config = analytics_settings or {}
    weekly = _coerce_target_map(config.get("weekly_targets"))
    monthly = _coerce_target_map(config.get("monthly_targets"))
    configured = any(
        value > 0
        for metrics in (*weekly.values(), *monthly.values())
        for value in metrics.values()
    )
    if not configured:
        return QuotaState(
            configured=False,
            title="Quota setup required",
            message="Add rep or team targets to unlock quota attainment and gap-to-goal charts.",
        )
    return QuotaState(
        configured=True,
        title="Quota targets",
        message="Targets come from Analytics Settings; goal lines reflect the configured weekly and monthly values.",
        weekly_targets=weekly,
        monthly_targets=monthly,
        team_weekly_targets=_team_target_totals(weekly),
        team_monthly_targets=_team_target_totals(monthly),
    )


# Our outbound email-sending identities: the primary domain plus the Instantly
# cold-outreach lookalike domains. A rep shares one local-part across all of them
# (annie@beacon.li == annie@beaconli.com == annie@beaconli.co), so an outbound
# email is mapped to its rep by local-part, not by the full address.
# Shared with the ingestion side so "CC'd to Zippy" cannot drift between the
# two. See app/services/zippy_tagging.py.
BEACON_SENDING_DOMAINS = set(_SHARED_SENDING_DOMAINS)
_is_zippy_address = is_zippy_address

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
            if _is_zippy_address(addr):
                return True
    return False


def _email_from_domain(row) -> str:
    from_addr = _reported_email_from(row).strip().lower()
    return from_addr.split("@", 1)[1] if "@" in from_addr else ""


def _reported_email_from(row) -> str:
    """Actual sender address, including for rows stored before raw preservation."""
    metadata = getattr(row, "event_metadata", None)
    if isinstance(metadata, dict):
        raw_from = str(metadata.get("raw_email_from") or "").strip()
        if raw_from:
            return raw_from
    return str(getattr(row, "email_from", None) or "").strip()


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
    Emails Out bucket rule, in priority order:
    1. source/external_source says Instantly -> Instantly, whatever the domain.
       Checked FIRST because many Instantly rows have a blank or prospect-domain
       sender; requiring one of our domains before this drops them.
    2. Not one of our sending domains -> not an outbound send, do not count.
    3. Positive manual evidence -> Manual, on ANY of our sending domains:
        - source personal_email_sync (rep's own inbox, already contact-filtered)
        - source gmail_sync (shared Zippy inbox)
        - Zippy in CC/BCC, plain or zippy+<deal-alias> form
    4. Otherwise, the beaconli.com/.co cold-outreach domains default to
       Instantly; a bare @beacon.li row with no signal is not counted.
    """
    source = str(getattr(row, "source", None) or "").strip().lower()
    external_source = str(getattr(row, "external_source", None) or "").strip().lower()

    # An Instantly row identifies itself by source/external_source. This MUST be
    # checked before any sending-domain requirement: prod has ~466 Instantly rows
    # whose email_from is blank or carries the prospect's domain (reply/webhook
    # shapes), and gating on our domains first silently dropped them.
    if source == "instantly" or external_source.startswith("instantly"):
        return "instantly"

    domain = _email_from_domain(row)
    if domain not in BEACON_SENDING_DOMAINS and not domain.startswith("beaconli."):
        return None

    # Positive evidence of a human send beats the sending-domain heuristic below.
    # A rep working from sipra@beaconli.com is still sending manually; Instantly
    # campaigns never CC Zippy and never arrive via a rep's own inbox sync. This
    # is why the domain check is no longer restricted to beacon.li — that
    # restriction silently filed alternate-domain manual sends under Instantly.
    if source in {"personal_email_sync", "gmail_sync"} or _zippy_in_cc_or_bcc(row):
        return "manual"

    # No manual signal: the cold-outreach lookalike domains are Instantly by
    # default (unattributed sends on them are campaign traffic).
    if domain in INSTANTLY_DOMAINS:
        return "instantly"
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
    """Fallback dedupe for legacy manual rows without a message id.

    Distinct Gmail message ids are distinct sends, even when recipient, subject,
    and day match. Collapsing those rows hides legitimate same-thread follow-ups.
    """
    if _email_out_bucket(row) != "manual":
        return None
    if str(getattr(row, "email_message_id", None) or "").strip():
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
    subject_key = _normalized_email_subject(getattr(row, "email_subject", None)) or "<blank-subject>"
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
            full_from = _reported_email_from(row).lower()
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
            full_from = _reported_email_from(row).lower()
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


def _ae_demo_funnel_deal_conditions(rep_id: UUID | None = None) -> list:
    """Scope of the AE demo funnel: every deal the AE OWNS, whoever sourced it.

    It deliberately does NOT constrain `sdr_id`. Requiring `sdr_id ==
    assigned_to_id` (self-sourced) hid an AE's demos on SDR-sourced deals, which
    is most of an AE's work — Pravalika ran 4 demos in a week and her row showed
    1 (2026-08-07). The SDR leaderboard credits `deal.sdr_id` for the very same
    transition, and that is intended: the SDR sourced it, the AE ran it.

    Shared by the dashboard aggregate and the drilldown so the pill and the list
    it opens can never disagree.
    """
    conditions = [Deal.assigned_to_id.isnot(None)]
    if rep_id is not None:
        conditions.append(Deal.assigned_to_id == rep_id)
    return conditions


def _advanced_stage_ids(stage_settings) -> set[str]:
    """Stages that mean an account has genuinely progressed PAST the demo.

    Only these suppress a rep's prospecting touches. Derived as a blocklist, not
    an allowlist, because `stage_order` strips every `group == "closed"` stage
    before slicing — so an allowlist silently lumps `cold`, `nurture`, `backlog`,
    `not_a_fit` and `closed_lost` in with "moved beyond demo_done". Dialing a
    cold account is re-prospecting, which is exactly the work this metric exists
    to measure (Mahesh, 2026-08-06: 116 of 317 calls in a week vanished this way,
    113 of them on `cold`/`backlog`/`nurture` accounts).

    A blocklist also fails open: an unknown or newly added stage id counts
    instead of disappearing without a trace.
    """
    ordered = [s["id"] for s in stage_settings if s.get("group") != "closed"]
    if "demo_done" in ordered:
        advanced = set(ordered[ordered.index("demo_done") + 1 :])
    else:
        advanced = {
            "qualified_lead",
            "poc_agreed",
            "poc_wip",
            "poc_done",
            "commercial_negotiation",
            "msa_review",
        }
    advanced.add("closed_won")  # already a customer, not a prospecting target
    return advanced


def _activity_row_is_early_funnel(
    row,
    *,
    deal_stage_by_id: dict[UUID, str],
    contact_company: dict[UUID, UUID | None],
    company_stages: dict[UUID, set[str]],
    early_funnel_stage_ids: set[str],
    advanced_stage_ids: set[str],
) -> bool:
    if row.deal_id is not None:
        stage = deal_stage_by_id.get(row.deal_id)
        # Unknown deal → keep rather than silently drop.
        return stage not in advanced_stage_ids if stage is not None else True
    if row.contact_id is not None:
        company_id = contact_company.get(row.contact_id)
        if company_id is not None:
            stages = company_stages.get(company_id)
            # Drop only when the whole account has moved past the demo with
            # nothing early still in flight. A company holding both a poc_wip
            # and a fresh reprospect deal is still being prospected.
            if stages and (stages & advanced_stage_ids) and not (stages & early_funnel_stage_ids):
                return False
    return True


def _headline_activity_counts(
    activity_rows,
    *,
    deal_owner: dict[UUID, UUID | None],
    contact_owner: dict[UUID, UUID | None],
    rep_id_by_local: dict[str, UUID],
    rep_user_ids: set[UUID],
    filter_rep_ids,
    users: dict[UUID, str],
    deal_stage_by_id: dict[UUID, str],
    contact_company: dict[UUID, UUID | None],
    company_stages: dict[UUID, set[str]],
    early_funnel_stage_ids: set[str],
    advanced_stage_ids: set[str],
) -> dict[str, dict[str, int]]:
    """Per-rep HEADLINE counts (calls/connected/live/emails/linkedin/total) for
    one set of window-scoped activity rows, keyed by rep_key.

    A compact mirror of the counting rules in sales_dashboard's main
    aggregation loop — same attribution, early-funnel call gate, per-call
    rollup, and email send classification/dedupe — WITHOUT the weekly buckets
    and breakdown metrics. It exists so the previous-window leaderboard deltas
    are computed by the exact rules of the numbers they are compared against.
    Keep these rules in lockstep with the main loop.
    """
    counts: dict[str, dict[str, int]] = {}
    seen_email_ids: dict[str, set[str]] = {}
    seen_manual_email_keys: dict[str, set[tuple]] = {}
    call_rollup: dict[tuple[str, str], dict] = {}

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
        rep_key, _, _ = _label_for_rep(row_rep_id, users)
        bucket = counts.setdefault(
            rep_key,
            {"calls": 0, "connected_calls": 0, "live_calls": 0, "emails": 0, "linkedin_reachouts": 0, "total": 0},
        )
        medium = str(row.medium or "").strip().lower()
        kind = str(row.type or "").strip().lower()
        outcome = str(row.call_outcome or "").strip().lower()
        if medium == "call" or kind == "call":
            if not _activity_row_is_early_funnel(
                row,
                deal_stage_by_id=deal_stage_by_id,
                contact_company=contact_company,
                company_stages=company_stages,
                early_funnel_stage_ids=early_funnel_stage_ids,
                advanced_stage_ids=advanced_stage_ids,
            ):
                continue
            call_key = str(getattr(row, "call_id", None) or "").strip()
            call_state = None
            if call_key:
                rollup_key = (rep_key, call_key)
                call_state = call_rollup.get(rollup_key)
                if call_state is not None:
                    # Later event row of an already-counted call: only upgrade
                    # its connected/live outcome.
                    if outcome in {"connected", "callback", "answered"} and not call_state["connected"]:
                        call_state["connected"] = True
                        call_state["bucket"]["connected_calls"] += 1
                    if outcome in {"connected", "answered"} and not call_state["live"]:
                        call_state["live"] = True
                        call_state["bucket"]["live_calls"] += 1
                    continue
                call_state = {"bucket": bucket, "connected": False, "live": False}
                call_rollup[rollup_key] = call_state
            bucket["calls"] += 1
            if outcome in {"connected", "callback", "answered"}:
                if call_state is not None:
                    call_state["connected"] = True
                bucket["connected_calls"] += 1
            if outcome in {"connected", "answered"}:
                if call_state is not None:
                    call_state["live"] = True
                bucket["live_calls"] += 1
        elif medium == "email" or kind == "email":
            # Only SENDS count toward the emails total (opens/replies feed the
            # rate cards, which have no prev_* counterpart).
            if _email_event_kind(row) != "send":
                continue
            msg_id = str(row.email_message_id or "").strip()
            rep_seen = seen_email_ids.setdefault(rep_key, set())
            if msg_id and msg_id in rep_seen:
                continue
            if msg_id:
                rep_seen.add(msg_id)
            if _email_out_bucket(row):
                manual_key = _manual_email_dedupe_key(row, rep_key)
                if manual_key:
                    rep_manual_seen = seen_manual_email_keys.setdefault(rep_key, set())
                    if manual_key in rep_manual_seen:
                        continue
                    rep_manual_seen.add(manual_key)
                bucket["emails"] += 1
        elif medium == "linkedin" or kind == "linkedin":
            bucket["linkedin_reachouts"] += 1
        bucket["total"] = bucket["calls"] + bucket["emails"] + bucket["linkedin_reachouts"]
    return counts


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
            # Canonical population rule shared with the sales-dashboard deal
            # scan and the milestone drilldowns: live "deal"-pipeline rows only.
            # Without it the monthly funnel counted milestones belonging to
            # soft-deleted or prospect-pipeline deals that no other surface
            # shows.
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
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
    analytics_settings = await get_analytics_settings(session)
    window_start, window_end = _resolve_analytics_window(
        window_days, from_date, to_date, tz=workspace_zoneinfo(analytics_settings)
    )
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}
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
        elif metric in {"calls", "connected_calls", "live_calls"}:
            # Call metrics dedupe per call_id in Python (one Aircall call spans
            # several rows), so fetch the window un-offset and paginate the
            # deduped list below — SQL offset over raw rows would misalign the
            # pages with the deduped card counts.
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

        # Call metrics: one row per CALL, not per Aircall event row — the rows
        # are ordered newest-first, so the surviving row is the final-state one
        # (call.ended, which carries the outcome). Mirrors the dashboard's
        # per-call rollup so drilldown rows == card count.
        call_has_more = False
        if metric in {"calls", "connected_calls", "live_calls"}:
            call_filtered = []
            seen_call_units: set[str] = set()
            for activity in activities:
                unit = (activity.call_id or "").strip()
                if unit:
                    if unit in seen_call_units:
                        continue
                    seen_call_units.add(unit)
                call_filtered.append(activity)
            call_has_more = len(call_filtered) > offset + limit
            activities = call_filtered[offset:offset + limit + 1]
        elif metric == "total":
            # The merged "total" stream must also count each call once.
            _seen_units: set[str] = set()
            _dedup_total = []
            for activity in activities:
                unit = (activity.call_id or "").strip()
                if unit:
                    if unit in _seen_units:
                        continue
                    _seen_units.add(unit)
                _dedup_total.append(activity)
            activities = _dedup_total

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
                direction = "outbound" if _beacon_sender_local(_reported_email_from(activity)) else "inbound"
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
                    from_email=_reported_email_from(activity) or None,
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
                        select(Deal.id, Deal.name, Deal.company_id, Deal.sdr_id, Deal.assigned_to_id).where(
                            Deal.id.in_(sched_deal_ids_dd)
                        )
                    )
                ).all():
                    sched_deal_rows_dd[dr.id] = dr
                    if dr.company_id:
                        sched_company_ids_dd.add(dr.company_id)

            sched_comp_sdr_dd: dict[UUID, UUID | None] = {}
            sched_comp_ae_dd: dict[UUID, UUID | None] = {}
            sched_comp_name_dd: dict[UUID, str] = {}
            sched_comp_region_dd: dict[UUID, str | None] = {}
            if sched_company_ids_dd:
                for cr in (
                    await session.execute(
                        select(Company.id, Company.name, Company.sdr_id, Company.assigned_to_id, Company.region).where(
                            Company.id.in_(sched_company_ids_dd)
                        )
                    )
                ).all():
                    sched_comp_sdr_dd[cr.id] = cr.sdr_id
                    sched_comp_ae_dd[cr.id] = cr.assigned_to_id
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
                sdr_id_dd = dr.sdr_id or sched_comp_sdr_dd.get(cid)
                if sdr_id_dd != rep_id:
                    continue
                if filter_geographies:
                    region_key = _normalize_geography_key(sched_comp_region_dd.get(cid))
                    if region_key not in filter_geographies:
                        continue
                demo_entries_sched.append((hist_r, dr))

            demo_has_more = len(demo_entries_sched) > offset + limit
            for hist_r, dr in demo_entries_sched[offset:offset + limit]:
                cid_dd = dr.company_id
                ae_uid_dd = dr.assigned_to_id or (sched_comp_ae_dd.get(cid_dd) if cid_dd else None)
                sdr_uid_dd = dr.sdr_id or (sched_comp_sdr_dd.get(cid_dd) if cid_dd else None)
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
                        ae_name=users.get(ae_uid_dd) if ae_uid_dd else None,
                        sdr_name=users.get(sdr_uid_dd) if sdr_uid_dd else None,
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
    # Every deal this AE owns, whoever sourced it. Must stay in lockstep with the
    # dashboard's `_bump_ae_demo` scope or the pill and the list it opens disagree.
    if metric in {"ae_demos_scheduled", "ae_demos_done", "ae_demos_converted"} and rep_id:
        stage_map = {
            "ae_demos_scheduled": "demo_scheduled",
            "ae_demos_done": "demo_done",
            "ae_demos_converted": "qualified_lead",
        }
        target_stage = stage_map[metric]
        ae_self_rows = (
            await session.execute(
                select(Deal.id, Deal.name, Deal.company_id, Deal.sdr_id, Deal.assigned_to_id).where(
                    *_ae_demo_funnel_deal_conditions(rep_id)
                )
            )
        ).all()
        ae_self_ids = {r.id for r in ae_self_rows}
        ae_self_name: dict[UUID, str | None] = {r.id: r.name for r in ae_self_rows}
        ae_self_company: dict[UUID, UUID | None] = {r.id: r.company_id for r in ae_self_rows}
        ae_self_sdr: dict[UUID, UUID | None] = {r.id: r.sdr_id for r in ae_self_rows}
        ae_company_ids = {cid for cid in ae_self_company.values() if cid}
        ae_comp_name: dict[UUID, str] = {}
        ae_comp_sdr: dict[UUID, UUID | None] = {}
        if ae_company_ids:
            for cr in (await session.execute(
                select(Company.id, Company.name, Company.sdr_id).where(Company.id.in_(ae_company_ids))
            )).all():
                ae_comp_name[cr.id] = cr.name or ""
                ae_comp_sdr[cr.id] = cr.sdr_id

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
            sdr_uid_ae = ae_self_sdr.get(hist_row.deal_id) or (ae_comp_sdr.get(cid) if cid else None)
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
                    ae_name=users.get(rep_id),
                    sdr_name=users.get(sdr_uid_ae) if sdr_uid_ae else None,
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
    elif metric in {"calls", "connected_calls", "live_calls"}:
        # Pagination happened on the per-call deduped list.
        has_more = locals().get("call_has_more", False)
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
    force_refresh: Annotated[bool, Query(description="Skip the snapshot cache and recompute; the fresh result still reseeds the cache")] = False,
):
    filter_rep_ids = rep_id or []
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}

    # Snapshot cache — return a recent identical computation if one is fresh.
    # force_refresh only bypasses the READ; the recomputed snapshot is written
    # back under the same key so followers get the fresh numbers.
    cache_key = (
        window_days,
        tuple(sorted(str(r) for r in filter_rep_ids)),
        tuple(sorted(filter_geographies)),
        from_date,
        to_date,
        forecast_granularity,
    )
    cached = None if force_refresh else _dashboard_cache_get(cache_key)
    if cached is not None:
        return cached

    now = _utcnow()
    # "Today" in the WORKSPACE timezone — it decides overdue/forecast day
    # boundaries. (Was server-local date.today(), then UTC; both put IST
    # mornings on the previous reporting day.)
    _tz = workspace_zoneinfo(await get_analytics_settings(session))
    today = workspace_today(_tz, now)

    window_start, window_end = _resolve_analytics_window(window_days, from_date, to_date, tz=_tz)
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
    ).where(
        # Same population as the board and the performance scorecard: real
        # LIVE deals only. Without this, prospect-pipeline rows leaked into
        # pipeline value here while every other surface excluded them — the
        # same "pipeline" number differed page to page. Soft-deleted deals are
        # out of every current-state number (their stage HISTORY still counts
        # in outcome metrics by design).
        Deal.pipeline_type == "deal",
        Deal.deleted_at.is_(None),
    )
    if filter_rep_ids:
        deal_stmt = deal_stmt.where(Deal.assigned_to_id.in_(filter_rep_ids))
    deal_rows = (await session.execute(deal_stmt)).all()
    if filter_geographies:
        deal_rows = [row for row in deal_rows if _normalize_geography_key(row.geography) in filter_geographies]
    allowed_deal_ids = {row.id for row in deal_rows}

    contact_stmt = select(
        Contact.id,
        Contact.assigned_to_id,
        Contact.company_id,
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
    contact_company = {row.id: row.company_id for row in contact_rows}
    deal_owner = {row.id: row.assigned_to_id for row in deal_rows}
    # Derive deal/company stage state for call exclusion once an account has
    # moved beyond the demo_done stage. Built from an UNFILTERED deal scan (like
    # _build_meeting_stage_gate does) — reading `deal_rows` here made the maps
    # rep/geo-scoped, so the same rep's call count changed depending on which
    # dashboard filter was applied.
    stage_scan_rows = (await session.execute(select(Deal.id, Deal.stage, Deal.company_id))).all()
    # Same reason: the contact→company hop must not be rep/geo-scoped either, or
    # a rep's call on someone else's contact resolves to no company and skips
    # the gate entirely.
    contact_company_all: dict[UUID, UUID | None] = {
        row.id: row.company_id
        for row in (await session.execute(select(Contact.id, Contact.company_id))).all()
    }
    deal_stage_by_id = {row.id: str(row.stage or "").strip().lower() for row in stage_scan_rows}
    company_stages: dict[UUID, set[str]] = {}
    for row in stage_scan_rows:
        if row.company_id is not None:
            company_stages.setdefault(row.company_id, set()).add(str(row.stage or "").strip().lower())

    stage_order = [stage["id"] for stage in stage_settings if stage.get("group") != "closed"]
    if "demo_done" in stage_order:
        early_funnel_stage_ids = set(stage_order[: stage_order.index("demo_done") + 1])
    else:
        early_funnel_stage_ids = {"reprospect", "demo_scheduled", "demo_done"}
    advanced_stage_ids = _advanced_stage_ids(stage_settings)

    # Bound the weekly-activity chart to at most ~1 year of buckets. Totals are
    # still computed over the full window (activities older than this still count
    # toward the leaderboard); only the per-week breakdown is capped so "All time"
    # doesn't generate thousands of weekly buckets per rep.
    # Bucket size comes from the RESOLVED window span (never the raw
    # window_days param) so short presets and explicit from/to ranges both get
    # daily bars — see _activity_bucket_granularity.
    activity_bucket_granularity = _activity_bucket_granularity(window_start, window_end)
    use_daily_activity_buckets = activity_bucket_granularity == "daily"
    bucket_end_date = (window_end - timedelta(days=1)).date() if to_date else window_end.date()
    if use_daily_activity_buckets:
        period_starts = _rolling_period_starts(window_start, window_end, daily=True, bucket_end_date=bucket_end_date)
    else:
        week_window_start = max(window_start, window_end - timedelta(weeks=52))
        period_starts = _rolling_week_starts(week_window_start, window_end)
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
                Activity.call_id,
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
            # Forecast horizon is capped: reusing the LOOKBACK window as the
            # FORWARD horizon meant "All time" (36,500 days) made forecast
            # equal total weighted pipeline — a number with no meaning.
            if row.close_date_est <= today + timedelta(days=min(window_days, 365)):
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

        # LIVE days-in-stage, same as the board computes it. The stored
        # days_in_stage column is refreshed nightly and only for open stages,
        # so reading it here showed different staleness than the board for the
        # same deal on the same screen.
        if row.stage_entered_at:
            live_days_in_stage = max(0, (now - row.stage_entered_at).days)
        else:
            live_days_in_stage = int(row.days_in_stage or 0)
        if live_days_in_stage >= 30:
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
        velocity_bucket["days"].append(live_days_in_stage)
        if live_days_in_stage >= 30:
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
                _period_key(period_start): {
                    "week_key": _period_key(period_start),
                    "label": _period_label(period_start, use_daily_activity_buckets),
                    "week_start": period_start.isoformat(),
                    "week_end": (period_start + timedelta(days=0 if use_daily_activity_buckets else 6)).isoformat(),
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
                for period_start in period_starts
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
    # One Aircall call produces SEVERAL activity rows, all medium="call":
    # call.created, call.answered, call.ended, plus a transcript row — so
    # counting rows triples connected calls and quadruples recorded ones.
    # Roll rows up per (rep, call_id): the first row (rows are sorted by
    # created_at, so the call-start row) counts the call and picks its bucket;
    # later rows of the same call only upgrade its connected/live outcome in
    # that same bucket. Rows without a call_id (manual call logs) are one row
    # per call already and keep the old per-row path.
    call_rollup: dict[tuple[str, str], dict] = {}
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
                    _period_key(period_start): {
                        "week_key": _period_key(period_start),
                        "label": _period_label(period_start, use_daily_activity_buckets),
                        "week_start": period_start.isoformat(),
                        "week_end": (period_start + timedelta(days=0 if use_daily_activity_buckets else 6)).isoformat(),
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
                    for period_start in period_starts
                },
            },
        )
        if use_daily_activity_buckets:
            period_start = row.created_at.date()
            period_key = _period_key(period_start)
        else:
            period_start = _start_of_week(row.created_at)
            period_key = _week_key(period_start)
        period_counts = weekly_bucket["weeks"].get(period_key)
        medium = str(row.medium or "").strip().lower()
        kind = str(row.type or "").strip().lower()
        outcome = str(row.call_outcome or "").strip().lower()
        if medium == "call" or kind == "call":
            if not _activity_row_is_early_funnel(
                row,
                deal_stage_by_id=deal_stage_by_id,
                contact_company=contact_company_all,
                company_stages=company_stages,
                early_funnel_stage_ids=early_funnel_stage_ids,
                advanced_stage_ids=advanced_stage_ids,
            ):
                continue
            call_key = str(getattr(row, "call_id", None) or "").strip()
            call_state = None
            if call_key:
                rollup_key = (rep_key, call_key)
                call_state = call_rollup.get(rollup_key)
                if call_state is not None:
                    # Later event row of an already-counted call: only upgrade
                    # its outcome inside the bucket it was first counted in.
                    if (
                        outcome in {"connected", "callback", "answered"}
                        and not call_state["connected"]
                    ):
                        call_state["connected"] = True
                        call_state["bucket"]["connected_calls"] += 1
                        if call_state["period"] is not None:
                            call_state["period"]["connected_calls"] += 1
                    if outcome in {"connected", "answered"} and not call_state["live"]:
                        call_state["live"] = True
                        call_state["bucket"]["live_calls"] += 1
                        if call_state["period"] is not None:
                            call_state["period"]["live_calls"] += 1
                    continue
                call_state = {
                    "bucket": activity_bucket,
                    "period": period_counts,
                    "connected": False,
                    "live": False,
                }
                call_rollup[rollup_key] = call_state
            activity_bucket["calls"] += 1
            if period_counts is not None:
                period_counts["calls"] += 1
            if outcome in {"connected", "callback", "answered"}:
                if call_state is not None:
                    call_state["connected"] = True
                activity_bucket["connected_calls"] += 1
                if period_counts is not None:
                    period_counts["connected_calls"] += 1
            if outcome in {"connected", "answered"}:
                if call_state is not None:
                    call_state["live"] = True
                activity_bucket["live_calls"] += 1
                if period_counts is not None:
                    period_counts["live_calls"] += 1
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
                    if period_counts is not None:
                        period_counts["emails"] += 1
                        period_counts[f"{email_bucket}_emails"] = int(period_counts.get(f"{email_bucket}_emails", 0)) + 1
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
            if period_counts is not None:
                period_counts["linkedin_reachouts"] += 1
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
        if period_counts is not None:
            period_counts["total"] = (
                period_counts["calls"]
                + period_counts["emails"]
                + period_counts["linkedin_reachouts"]
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
                    _period_key(period_start): {
                        "week_key": _period_key(period_start),
                        "label": _period_label(period_start, use_daily_activity_buckets),
                        "week_start": period_start.isoformat(),
                        "week_end": (period_start + timedelta(days=0 if use_daily_activity_buckets else 6)).isoformat(),
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
                    for period_start in period_starts
                },
            },
        )
        if use_daily_activity_buckets:
            period_start = meeting_timestamp.date()
            period_key = _period_key(period_start)
        else:
            period_key = _week_key(_start_of_week(meeting_timestamp))
        period_counts = weekly_bucket["weeks"].get(period_key)
        meeting_bucket["meetings"] += 1
        # Meetings do not add to the touch total (see activity bump). A meeting
        # bump leaves `total` unchanged — it only increments the meetings metric.
        meeting_bucket["total"] = (
            meeting_bucket["calls"]
            + meeting_bucket["emails"]
            + meeting_bucket["linkedin_reachouts"]
        )
        if period_counts is not None:
            period_counts["meetings"] += 1
            period_counts["total"] = (
                period_counts["calls"]
                + period_counts["emails"]
                + period_counts["linkedin_reachouts"]
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

    # ── AE Demo Funnel: every deal the AE OWNS, whoever sourced it ──────────────
    # This used to require `sdr_id == assigned_to_id` (self-sourced only), which
    # hid an AE's demos on SDR-sourced deals — i.e. most of their work. Pravalika
    # ran 4 demos in a week and her row showed 1 (2026-08-07: Ordway Labs and
    # Command Alkon, sourced by Mahesh and Jacob).
    #
    # The SDR leaderboard still credits `deal.sdr_id` for the same transition, and
    # that is deliberate, not double counting: the two tables measure two
    # different jobs — the SDR sourced it, the AE ran it.
    ae_funnel_deal_rows = (
        await session.execute(
            select(Deal.id, Deal.assigned_to_id, Deal.sdr_id, Deal.company_id).where(
                *_ae_demo_funnel_deal_conditions()
            )
        )
    ).all()
    # Map deal_id → owning AE. `_bump_ae_demo` skips any deal missing from here,
    # so the stage-history queries below need no deal-id IN() clause.
    ae_deal_ae: dict[UUID, UUID] = {r.id: r.assigned_to_id for r in ae_funnel_deal_rows}

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
            )
        )
    ).all()
    seen_ae_conv: set[UUID] = set()
    for r in sorted(ae_conv_hist, key=lambda x: x.changed_at):
        if r.deal_id not in seen_ae_conv:
            seen_ae_conv.add(r.deal_id)
            _bump_ae_demo(r.deal_id, "ae_demos_converted")

    # Per-rep meeting-booked count from call activities where the SDR picked
    # "Demo Scheduled/Booked" (or "Meeting Confirmed") disposition within the
    # analytics window. Credited to the rep who logged the call (created_by_id).
    #
    # Scoped exactly like the `meeting-booked-from-deals` drilldown this tile
    # opens: the call must resolve to a LIVE "deal"-pipeline deal (directly via
    # Activity.deal_id, or through the contact→deal link), and that deal must
    # pass the page's geography filter. Counted as DISTINCT DEALS per rep — the
    # same unit as the Email/LinkedIn siblings below and as the drilldown list,
    # so tile and modal reconcile. Previously this was a raw activity count with
    # no deal linkage at all, so it ignored geography entirely and double-counted
    # a rep who logged two booked calls against one deal.
    call_meeting_booked_by_uid: dict[UUID, int] = {}
    if rep_user_ids:
        type_lower = func.lower(Activity.type)
        medium_lower = func.lower(Activity.medium)
        outcome_lower = func.lower(Activity.call_outcome)
        content_lower = func.lower(Activity.content)
        is_call = or_(type_lower == "call", medium_lower == "call")
        is_booked = or_(
            outcome_lower.in_(["demo_scheduled_booked", "meeting_confirmed"]),
            and_(
                func.lower(Activity.source) == "manual",
                or_(
                    content_lower.startswith("demo scheduled/booked"),
                    content_lower.startswith("meeting confirmed"),
                ),
            ),
        )
        cmb_rows = (
            await session.execute(
                select(Activity.created_by_id, Activity.deal_id, Activity.contact_id)
                .where(
                    Activity.created_by_id.in_(list(rep_user_ids)),
                    Activity.created_at >= window_start,
                    Activity.created_at <= window_end,
                    is_call,
                    is_booked,
                    or_(Activity.deal_id.is_not(None), Activity.contact_id.is_not(None)),
                )
            )
        ).all()
        # Path 2 of the drilldown: calls logged against a contact resolve to
        # deals through deal_contacts.
        cmb_deals_by_contact: dict[UUID, set[UUID]] = {}
        cmb_contact_ids = {r.contact_id for r in cmb_rows if r.contact_id}
        if cmb_contact_ids:
            for dc in (await session.execute(
                select(DealContact.contact_id, DealContact.deal_id)
                .where(DealContact.contact_id.in_(list(cmb_contact_ids)))
            )).all():
                cmb_deals_by_contact.setdefault(dc.contact_id, set()).add(dc.deal_id)
        cmb_candidate_deal_ids = {r.deal_id for r in cmb_rows if r.deal_id}
        for _linked in cmb_deals_by_contact.values():
            cmb_candidate_deal_ids |= _linked
        allowed_cmb_deal_ids: set[UUID] = set()
        if cmb_candidate_deal_ids:
            cmb_deal_rows = (await session.execute(
                select(Deal.id, Deal.geography.label("deal_geography"))
                .where(
                    Deal.id.in_(list(cmb_candidate_deal_ids)),
                    Deal.pipeline_type == "deal",
                    Deal.deleted_at.is_(None),
                )
            )).all()
            allowed_cmb_deal_ids = {
                d.id for d in cmb_deal_rows
                if not filter_geographies
                or _normalize_geography_key(d.deal_geography) in filter_geographies
            }
        seen_cmb: set[tuple] = set()
        for cmb in cmb_rows:
            if not cmb.created_by_id:
                continue
            cmb_row_deal_ids: set[UUID] = set()
            if cmb.deal_id:
                cmb_row_deal_ids.add(cmb.deal_id)
            if cmb.contact_id:
                cmb_row_deal_ids |= cmb_deals_by_contact.get(cmb.contact_id, set())
            for cmb_deal_id in cmb_row_deal_ids & allowed_cmb_deal_ids:
                key = (cmb.created_by_id, cmb_deal_id)
                if key in seen_cmb:
                    continue
                seen_cmb.add(key)
                call_meeting_booked_by_uid[cmb.created_by_id] = (
                    call_meeting_booked_by_uid.get(cmb.created_by_id, 0) + 1
                )

    # ── Email / LinkedIn meetings booked: from Deal.meeting_booked_from ─────────
    # Count deals created in the window where meeting_booked_from matches,
    # attributed to both AE (assigned_to_id) and SDR (sdr_id). Deduped per rep.
    email_meeting_booked_by_uid: dict[UUID, int] = {}
    linkedin_meeting_booked_by_uid: dict[UUID, int] = {}
    if rep_user_ids:
        for channel, bucket_dict in [("Email", email_meeting_booked_by_uid), ("LinkedIn", linkedin_meeting_booked_by_uid)]:
            channel_rows = (await session.execute(
                select(
                    Deal.id,
                    Deal.assigned_to_id,
                    Deal.sdr_id,
                    Deal.geography.label("deal_geography"),
                )
                .where(
                    Deal.meeting_booked_from == channel,
                    Deal.created_at >= window_start,
                    Deal.created_at <= window_end,
                    # Canonical population rule, matching the
                    # `meeting-booked-from-deals` drilldown this tile opens.
                    Deal.pipeline_type == "deal",
                    Deal.deleted_at.is_(None),
                    or_(
                        Deal.assigned_to_id.in_(list(rep_user_ids)),
                        Deal.sdr_id.in_(list(rep_user_ids)),
                    ),
                )
            )).all()
            # Geography via _normalize_geography_key in Python, like every other
            # aggregation on this page (and like the drilldown).
            if filter_geographies:
                channel_rows = [
                    r for r in channel_rows
                    if _normalize_geography_key(r.deal_geography) in filter_geographies
                ]
            seen_channel: set[tuple] = set()
            for row in channel_rows:
                for uid in [row.assigned_to_id, row.sdr_id]:
                    if uid and uid in rep_user_ids:
                        key = (uid, row.id)
                        if key in seen_channel:
                            continue
                        seen_channel.add(key)
                        bucket_dict[uid] = bucket_dict.get(uid, 0) + 1

    # ── Output buckets: Demo Scheduled deals, bucketed by Meeting.scheduled_at ─
    # Source: the same window-scoped demo_scheduled milestones as the "Demo
    # Scheduled" KPI (deduplicated per company), split by meeting date so the
    # week buckets reconcile to the Demo Scheduled total. Each demo is counted
    # ONCE globally (meeting_bucket_totals); per-rep it is attributed to the
    # owning AE and/or SDR (deduped per rep per deal) for the leaderboards.
    _DIRECT_SQL_TITLES = ("VP", "SVP", "Head/Chief")
    meeting_bucket_totals: dict[str, int] = {
        "meetings_next_1w": 0,
        "meetings_next_2w": 0,
        "meetings_beyond_2w": 0,
        "direct_sql": 0,
    }
    meetings_next_1w_by_uid: dict[UUID, int] = {}
    meetings_next_2w_by_uid: dict[UUID, int] = {}
    meetings_beyond_2w_by_uid: dict[UUID, int] = {}
    direct_sql_by_uid: dict[UUID, int] = {}
    _today_dt = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
    _week1_dt = _today_dt + timedelta(days=7)
    _week2_dt = _today_dt + timedelta(days=14)

    demo_bucket_rows = (await session.execute(
        select(
            CompanyStageMilestone.deal_id,
            Deal.assigned_to_id,
            Deal.sdr_id,
            Deal.meeting_booked_with,
            Deal.geography.label("deal_geography"),
            Meeting.scheduled_at,
        )
        .outerjoin(Deal, CompanyStageMilestone.deal_id == Deal.id)
        .outerjoin(Meeting, Meeting.deal_id == CompanyStageMilestone.deal_id)
        .where(
            CompanyStageMilestone.milestone_key == "demo_scheduled",
            CompanyStageMilestone.first_reached_at >= window_start,
            CompanyStageMilestone.first_reached_at <= window_end,
            # Canonical population rule (see the deal scan above): live
            # "deal"-pipeline rows only. The meeting-bucket DRILLDOWN already
            # applies both, so without them a soft-deleted or prospect-pipeline
            # deal carrying a demo_scheduled milestone inflated the TILE above
            # the modal it opens.
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
        )
    )).all()
    # Same rep scope as the "Demo Scheduled" KPI (AE-assigned deals), so the
    # week buckets always sum to the KPI total for the active filters.
    if filter_rep_ids:
        demo_bucket_rows = [r for r in demo_bucket_rows if r.assigned_to_id in filter_rep_ids]
    if filter_geographies:
        demo_bucket_rows = [
            r for r in demo_bucket_rows
            if _normalize_geography_key(r.deal_geography) in filter_geographies
        ]
    # A deal may carry several meeting records (reschedules); the current
    # scheduled date is the latest one.
    latest_meeting_by_deal: dict[UUID, datetime] = {}
    for r in demo_bucket_rows:
        if r.scheduled_at is not None:
            cur = latest_meeting_by_deal.get(r.deal_id)
            if cur is None or r.scheduled_at > cur:
                latest_meeting_by_deal[r.deal_id] = r.scheduled_at
    seen_demo_buckets: set[UUID] = set()
    for row in demo_bucket_rows:
        if row.deal_id is None or row.deal_id in seen_demo_buckets:
            continue
        seen_demo_buckets.add(row.deal_id)
        sat = latest_meeting_by_deal.get(row.deal_id)
        if sat is None or sat >= _week2_dt:
            bucket_key = "meetings_beyond_2w"
        elif sat >= _week1_dt:
            bucket_key = "meetings_next_2w"
        else:
            bucket_key = "meetings_next_1w"
        meeting_bucket_totals[bucket_key] += 1
        is_direct_sql = row.meeting_booked_with in _DIRECT_SQL_TITLES
        if is_direct_sql:
            meeting_bucket_totals["direct_sql"] += 1
        owner_uids: set[UUID] = set()
        if row.assigned_to_id and row.assigned_to_id in rep_user_ids:
            owner_uids.add(row.assigned_to_id)
        if row.sdr_id and row.sdr_id in rep_user_ids:
            owner_uids.add(row.sdr_id)
        if filter_rep_ids:
            owner_uids &= set(filter_rep_ids)
        bucket_by_uid = {
            "meetings_next_1w": meetings_next_1w_by_uid,
            "meetings_next_2w": meetings_next_2w_by_uid,
            "meetings_beyond_2w": meetings_beyond_2w_by_uid,
        }[bucket_key]
        for uid in owner_uids:
            bucket_by_uid[uid] = bucket_by_uid.get(uid, 0) + 1
            if is_direct_sql:
                direct_sql_by_uid[uid] = direct_sql_by_uid.get(uid, 0) + 1

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

    # ── Demo reschedules: Activity(type="demo_rescheduled") within the window ──
    # Mirrors the Output drill-down source: distinct deals whose meeting date was
    # changed inside the window. The global total is deduplicated by deal; the
    # per-rep count credits the rep who logged the reschedule.
    demos_rescheduled_by_uid: dict[UUID, int] = {}
    demos_rescheduled_total = 0
    dr_rows = (await session.execute(
        select(
            Activity.created_by_id,
            Activity.deal_id,
            Deal.assigned_to_id,
            Deal.sdr_id,
            Deal.geography.label("deal_geography"),
        )
        .join(Deal, Activity.deal_id == Deal.id)
        .where(
            Activity.type == "demo_rescheduled",
            Activity.created_at >= window_start,
            Activity.created_at <= window_end,
            Activity.deal_id.is_not(None),
            # Canonical population rule, matching the "demo_rescheduled"
            # meeting-bucket drilldown, which already scopes to live
            # "deal"-pipeline rows.
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
        )
    )).all()
    if filter_rep_ids:
        dr_rows = [
            r for r in dr_rows
            if (r.assigned_to_id and r.assigned_to_id in filter_rep_ids)
            or (r.sdr_id and r.sdr_id in filter_rep_ids)
        ]
    if filter_geographies:
        dr_rows = [r for r in dr_rows if _normalize_geography_key(r.deal_geography) in filter_geographies]
    seen_dr_global: set[UUID] = set()
    seen_dr_rep: set[tuple] = set()
    for dr in dr_rows:
        if dr.deal_id not in seen_dr_global:
            seen_dr_global.add(dr.deal_id)
            demos_rescheduled_total += 1
        if (
            dr.created_by_id
            and dr.created_by_id in rep_user_ids
            and (not filter_rep_ids or dr.created_by_id in filter_rep_ids)
        ):
            key = (dr.created_by_id, dr.deal_id)
            if key not in seen_dr_rep:
                seen_dr_rep.add(key)
                demos_rescheduled_by_uid[dr.created_by_id] = demos_rescheduled_by_uid.get(dr.created_by_id, 0) + 1

    # Inject zero rows for seed reps who have no activity in the window
    for seed_uid in seed_rep_user_ids:
        rep_key, rep_user_id, rep_name = _label_for_rep(seed_uid, users)
        if rep_key not in rep_activity:
            rep_activity[rep_key] = {
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
            }

    # ── Previous-window headline comparison for the leaderboard ──────────────
    # Equal-length window immediately preceding window_start, same rep and
    # geography scoping — the leaderboard counterpart of SalesSummary's prev_*
    # milestone deltas. One extra activities query plus one extra meetings
    # query in total (never per-rep); the counting rules are shared with the
    # main loop via _headline_activity_counts.
    prev_window_len = window_end - window_start
    prev_window_end = window_start
    prev_window_start = window_start - prev_window_len
    prev_activity_rows = (
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
                Activity.call_id,
                Activity.call_outcome,
                Activity.email_from,
                Activity.email_to,
                Activity.email_cc,
                Activity.email_bcc,
                Activity.email_message_id,
                Activity.email_subject,
            ).where(
                # Half-open on the right: window_start belongs to the CURRENT
                # window, so the boundary instant is never counted twice.
                Activity.created_at >= prev_window_start,
                Activity.created_at < prev_window_end,
            )
        )
    ).all()
    if filter_rep_ids:
        prev_activity_rows = [
            row for row in prev_activity_rows
            if _activity_rep_id(row, deal_owner=deal_owner, contact_owner=contact_owner, rep_id_by_local=rep_id_by_local) in filter_rep_ids
        ]
    if filter_geographies:
        prev_activity_rows = [row for row in prev_activity_rows if row.deal_id in allowed_deal_ids or row.contact_id in allowed_contact_ids]
    prev_headline_counts = _headline_activity_counts(
        prev_activity_rows,
        deal_owner=deal_owner,
        contact_owner=contact_owner,
        rep_id_by_local=rep_id_by_local,
        rep_user_ids=rep_user_ids,
        filter_rep_ids=filter_rep_ids,
        users=users,
        deal_stage_by_id=deal_stage_by_id,
        contact_company=contact_company_all,
        company_stages=company_stages,
        early_funnel_stage_ids=early_funnel_stage_ids,
        advanced_stage_ids=advanced_stage_ids,
    )
    # Previous-window meetings, run through the identical qualification chain as
    # the current-window meetings metric (CRM link, not cancelled, early-funnel
    # stage gate, real sources, cross-source dedupe, rep attribution).
    prev_meeting_rows = (
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
                    (Meeting.scheduled_at >= prev_window_start) & (Meeting.scheduled_at < prev_window_end),
                    Meeting.scheduled_at.is_(None) & (Meeting.created_at >= prev_window_start) & (Meeting.created_at < prev_window_end),
                ),
            )
        )
    ).all()
    if filter_geographies:
        prev_meeting_rows = [row for row in prev_meeting_rows if row.deal_id in allowed_deal_ids]
    prev_meeting_candidates = [
        row
        for row in prev_meeting_rows
        if _is_crm_linked_meeting(row)
        and row.status != "cancelled"
        and _meeting_within_sales_funnel(row)
        and str(row.external_source or "").strip().lower() in REAL_MEETING_SOURCES
    ]
    prev_meetings_by_rep: dict[str, int] = {}
    for row in _dedupe_meetings_across_sources(prev_meeting_candidates):
        for row_rep_id in _meeting_rep_ids(row, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email):
            if not _is_rep(row_rep_id, rep_user_ids):
                continue
            if filter_rep_ids and row_rep_id not in filter_rep_ids:
                continue
            prev_rep_key, _, _ = _label_for_rep(row_rep_id, users)
            prev_meetings_by_rep[prev_rep_key] = prev_meetings_by_rep.get(prev_rep_key, 0) + 1

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
            linkedin_meeting_booked=linkedin_meeting_booked_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            email_meeting_booked=email_meeting_booked_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
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
            demos_rescheduled=demos_rescheduled_by_uid.get(bucket["user_id"], 0) if bucket["user_id"] else 0,
            prev_total=int(prev_headline_counts.get(str(bucket["key"]), {}).get("total", 0)),
            prev_calls=int(prev_headline_counts.get(str(bucket["key"]), {}).get("calls", 0)),
            prev_connected_calls=int(prev_headline_counts.get(str(bucket["key"]), {}).get("connected_calls", 0)),
            prev_live_calls=int(prev_headline_counts.get(str(bucket["key"]), {}).get("live_calls", 0)),
            prev_emails=int(prev_headline_counts.get(str(bucket["key"]), {}).get("emails", 0)),
            prev_linkedin_reachouts=int(prev_headline_counts.get(str(bucket["key"]), {}).get("linkedin_reachouts", 0)),
            prev_meetings=prev_meetings_by_rep.get(str(bucket["key"]), 0),
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
            # Canonical population rule (see the deal scan above): live
            # "deal"-pipeline rows only. These rows feed BOTH the milestone KPI
            # tiles and the milestone_deals drilldown they open, so scoping here
            # keeps the two in lockstep. Every milestone is written with a
            # deal_id (see app/services/company_stage_milestones.py), so the
            # inner-join effect of these predicates drops nothing legitimate.
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
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
    # geography filters so the comparison is apples-to-apples. Bounds
    # (prev_window_start/prev_window_end) were computed above for the
    # leaderboard prev_* comparison and are shared here.
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
            # Same canonical scoping as the current-window statement above, or
            # the period-over-period delta compares a scoped number against an
            # unscoped one and invents movement that never happened.
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
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
    # Echo the window that was RESOLVED, not the raw params — those are null for
    # every preset pick, which left the client's "as of" strip and its CSV
    # filenames with no dates in the common case. window_end is exclusive, so
    # step back to land on the last day actually covered ("now" -> today for the
    # default branch, to_date + 1 day -> to_date for the explicit one).
    resolved_from = window_start.date().isoformat()
    resolved_to = (window_end - timedelta(microseconds=1)).date().isoformat()

    result = SalesDashboardRead(
        generated_at=now,
        window_days=window_days,
        from_date=resolved_from,
        to_date=resolved_to,
        activity_bucket_granularity=activity_bucket_granularity,
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
        monthly_unique_funnel=monthly_unique_funnel,
        meeting_bucket_totals=MeetingBucketTotals(
            meetings_next_1w=meeting_bucket_totals["meetings_next_1w"],
            meetings_next_2w=meeting_bucket_totals["meetings_next_2w"],
            meetings_beyond_2w=meeting_bucket_totals["meetings_beyond_2w"],
            direct_sql=meeting_bucket_totals["direct_sql"],
            demo_rescheduled=demos_rescheduled_total,
        ),
        quota=_build_quota_state(analytics_settings),
    )
    _dashboard_cache_set(cache_key, result)
    return result


# ── Meeting-bucket drill-down ────────────────────────────────────────────────

class MeetingBucketDealRow(BaseModel):
    deal_id: str
    deal_name: str
    ae_name: str
    sdr_name: str
    date_of_meeting: Optional[str] = None
    meeting_booked_with: Optional[str] = None


class MeetingBucketDealsResponse(BaseModel):
    deals: list[MeetingBucketDealRow]


@router.get("/meeting-bucket-deals")
async def meeting_bucket_deals(
    session: DBSession,
    current_user: CurrentUser,
    bucket: Annotated[
        Literal["next_1w", "next_2w", "beyond_2w", "direct_sql", "demo_rescheduled"],
        Query(),
    ],
    user_id: Annotated[
        list[UUID],
        Query(description="Rep(s) whose per-rep pill is being opened — credited as AE or SDR. Repeatable; a single value still works."),
    ] = [],
    rep_id: Annotated[
        list[UUID],
        Query(description="The page's active rep filter — AE-assigned, exactly like sales-dashboard's rep_id. Repeatable."),
    ] = [],
    window_days: Annotated[int, Query(ge=1, le=36500)] = 90,
    geography: Annotated[list[str], Query()] = [],
    from_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window start")] = None,
    to_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window end")] = None,
) -> MeetingBucketDealsResponse:
    """Return the individual deals behind each Meetings Booked StatPill.
    Source: demo_scheduled milestones reached within the selected window joined
    to Meeting.scheduled_at — matches the dashboard pill counts.

    The window goes through _resolve_analytics_window — the same workspace-tz,
    today-counts-as-day-one math as the dashboard tiles — so this list can
    never disagree with the pill it drills into. The 1w/2w bucket BOUNDARIES
    stay UTC-midnight based, mirroring the dashboard's meeting_bucket_totals
    computation exactly.

    Rep scoping mirrors the dashboard's TWO levels, because the page has two
    kinds of Meetings Booked pill:
      * `rep_id`  — the page-level rep filter. sales_dashboard pre-filters the
        milestone rows on `Deal.assigned_to_id IN rep_id` before it builds
        meeting_bucket_totals, so the overall pills are AE-scoped. Sending the
        same ids here makes this list equal that pill.
      * `user_id` — the rep a PER-REP pill belongs to. The dashboard credits
        each demo to its AE *and* its SDR, so this one matches either side.
    They compose: `rep_id` alone is the overall pill under an active filter,
    `rep_id` + `user_id` is one rep's row inside that filter. Empty means
    unscoped, which keeps the old single-`user_id` callers working unchanged.
    """
    _tz = workspace_zoneinfo(await get_analytics_settings(session))
    window_start, window_end = _resolve_analytics_window(window_days, from_date, to_date, tz=_tz)
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}
    filter_rep_ids = [r for r in rep_id if r]
    pill_user_ids = [u for u in user_id if u]
    if filter_rep_ids and pill_user_ids:
        # sales_dashboard narrows a pill's credited reps to the active filter
        # (`owner_uids &= set(filter_rep_ids)`), so a rep whose leaderboard row
        # is still on screen while they sit OUTSIDE the filter shows a zero
        # pill. Intersect the same way, and short-circuit when nothing is left.
        allowed = set(filter_rep_ids)
        pill_user_ids = [u for u in pill_user_ids if u in allowed]
        if not pill_user_ids:
            return MeetingBucketDealsResponse(deals=[])
    today_dt = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week1_dt = today_dt + timedelta(days=7)
    week2_dt = today_dt + timedelta(days=14)

    AEUser = aliased(User)
    SDRUser = aliased(User)

    deals: list[MeetingBucketDealRow] = []

    if bucket in ("next_1w", "next_2w", "beyond_2w", "direct_sql"):
        _DIRECT_SQL_TITLES = ("VP", "SVP", "Head/Chief")
        stmt = (
            select(
                CompanyStageMilestone.deal_id.label("deal_id"),
                Deal.name.label("deal_name"),
                Deal.meeting_booked_with.label("meeting_booked_with"),
                Deal.geography.label("deal_geography"),
                Meeting.scheduled_at.label("scheduled_at"),
                AEUser.name.label("ae_name"),
                SDRUser.name.label("sdr_name"),
            )
            .select_from(CompanyStageMilestone)
            .outerjoin(Deal, CompanyStageMilestone.deal_id == Deal.id)
            .outerjoin(AEUser, Deal.assigned_to_id == AEUser.id)
            .outerjoin(SDRUser, Deal.sdr_id == SDRUser.id)
            .outerjoin(Meeting, Meeting.deal_id == CompanyStageMilestone.deal_id)
            .where(
                CompanyStageMilestone.milestone_key == "demo_scheduled",
                CompanyStageMilestone.first_reached_at >= window_start,
                CompanyStageMilestone.first_reached_at <= window_end,
                # Same population rule as the dashboard deal scan: live "deal"-
                # pipeline rows only (milestones without a deal are skipped in
                # the loop below anyway).
                Deal.pipeline_type == "deal",
                Deal.deleted_at.is_(None),
            )
        )
        # Page filter first (AE only — same as sales_dashboard's pre-filter on
        # the milestone rows), then the per-rep pill's AE-or-SDR attribution.
        if filter_rep_ids:
            stmt = stmt.where(Deal.assigned_to_id.in_(filter_rep_ids))
        if pill_user_ids:
            stmt = stmt.where(
                or_(Deal.assigned_to_id.in_(pill_user_ids), Deal.sdr_id.in_(pill_user_ids))
            )

        # Deduplicate by deal_id (a deal may have multiple meetings), keeping the
        # latest scheduled date.
        rows = (await session.execute(stmt)).all()
        if filter_geographies:
            # Geography is applied via _normalize_geography_key in Python,
            # exactly like the dashboard's meeting-bucket aggregation.
            rows = [r for r in rows if _normalize_geography_key(r.deal_geography) in filter_geographies]
        latest_by_deal: dict[UUID, datetime] = {}
        for row in rows:
            if row.scheduled_at is not None:
                cur = latest_by_deal.get(row.deal_id)
                if cur is None or row.scheduled_at > cur:
                    latest_by_deal[row.deal_id] = row.scheduled_at
        seen: set[UUID] = set()
        for row in rows:
            did = row.deal_id
            if did is None or did in seen:
                continue
            seen.add(did)
            sat = latest_by_deal.get(did)
            if bucket == "next_1w":
                if sat is None or sat >= week1_dt:
                    continue
            elif bucket == "next_2w":
                if sat is None or sat < week1_dt or sat >= week2_dt:
                    continue
            elif bucket == "beyond_2w":
                if sat is not None and sat < week2_dt:
                    continue
            elif bucket == "direct_sql":
                if row.meeting_booked_with not in _DIRECT_SQL_TITLES:
                    continue
            deals.append(MeetingBucketDealRow(
                deal_id=str(did),
                deal_name=str(row.deal_name or ""),
                ae_name=str(row.ae_name or "Unassigned"),
                sdr_name=str(row.sdr_name or ""),
                date_of_meeting=sat.date().isoformat() if sat else None,
                meeting_booked_with=row.meeting_booked_with,
            ))

    elif bucket == "demo_rescheduled":
        seen_deal_ids: set[str] = set()
        stmt = (
            select(
                Deal.id.label("deal_id"),
                Deal.name.label("deal_name"),
                Deal.close_date_est.label("close_date_est"),
                Deal.meeting_booked_with.label("meeting_booked_with"),
                Deal.geography.label("deal_geography"),
                AEUser.name.label("ae_name"),
                Company.sdr_name.label("sdr_name"),
            )
            .select_from(Activity)
            .join(Deal, Activity.deal_id == Deal.id)
            .outerjoin(Company, Deal.company_id == Company.id)
            .outerjoin(AEUser, Deal.assigned_to_id == AEUser.id)
            .where(
                Activity.type == "demo_rescheduled",
                Activity.created_at >= window_start,
                Activity.created_at <= window_end,
                Deal.pipeline_type == "deal",
                Deal.deleted_at.is_(None),
            )
        )
        # The dashboard's demos_rescheduled total keeps a reschedule when the
        # DEAL belongs to a filtered rep (AE or SDR), and credits the per-rep
        # count to whoever LOGGED it — mirror both.
        if filter_rep_ids:
            stmt = stmt.where(
                or_(Deal.assigned_to_id.in_(filter_rep_ids), Deal.sdr_id.in_(filter_rep_ids))
            )
        if pill_user_ids:
            stmt = stmt.where(Activity.created_by_id.in_(pill_user_ids))

        rows = (await session.execute(stmt)).all()
        if filter_geographies:
            rows = [r for r in rows if _normalize_geography_key(r.deal_geography) in filter_geographies]
        for row in rows:
            did = str(row.deal_id)
            if did in seen_deal_ids:
                continue
            seen_deal_ids.add(did)
            deals.append(MeetingBucketDealRow(
                deal_id=did,
                deal_name=str(row.deal_name or ""),
                ae_name=str(row.ae_name or "Unassigned"),
                sdr_name=str(row.sdr_name or ""),
                date_of_meeting=row.close_date_est.isoformat() if row.close_date_est else None,
                meeting_booked_with=row.meeting_booked_with,
            ))

    return MeetingBucketDealsResponse(deals=deals)


# ── Pipeline Deals drilldown ──────────────────────────────────────────────────


class PipelineDealItem(BaseModel):
    deal_id: str
    deal_name: str
    stage: str
    stage_label: str
    deal_value: float
    ae_name: Optional[str]


@router.get("/pipeline-deals", response_model=list[PipelineDealItem])
async def pipeline_deals(
    session: DBSession,
    current_user: CurrentUser,
    user_id: Annotated[list[UUID], Query(description="Rep(s) to scope to — AE-assigned. Repeatable; a single value still works.")] = [],
    geography: Annotated[list[str], Query()] = [],
) -> list[PipelineDealItem]:
    """Return active-stage deals for a rep (or all reps) for the Pipeline drilldown.

    There is no window param on purpose — the pipeline tile is a point-in-time
    snapshot of open deals, not a windowed aggregate. Geography DOES bind: the
    dashboard's per-rep `pipeline_amount` is computed off deal rows that were
    already narrowed by the page's geography filter, so this list has to apply
    the same bucket filter or it over-lists whenever a region is selected.
    """
    stage_settings = await get_configured_deal_stages(session)
    active_stage_ids = {s["id"] for s in stage_settings if s.get("group") != "closed"}
    stage_label_map = {s["id"]: s["label"] for s in stage_settings}
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}
    filter_user_ids = [u for u in user_id if u]

    AEUser = aliased(User)
    stmt = (
        select(
            Deal.id,
            Deal.name,
            Deal.stage,
            Deal.value,
            Deal.geography.label("deal_geography"),
            AEUser.name.label("ae_name"),
        )
        .outerjoin(AEUser, Deal.assigned_to_id == AEUser.id)
        .where(
            Deal.stage.in_(list(active_stage_ids)),
            # Same population rule as the dashboard scan/board: live deals only.
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
        )
    )
    if filter_user_ids:
        stmt = stmt.where(Deal.assigned_to_id.in_(filter_user_ids))

    rows = (await session.execute(stmt)).all()
    if filter_geographies:
        # Geography via _normalize_geography_key in Python, like the dashboard —
        # the param holds bucket labels while the column holds raw values.
        rows = [r for r in rows if _normalize_geography_key(r.deal_geography) in filter_geographies]
    result = [
        PipelineDealItem(
            deal_id=str(row.id),
            deal_name=str(row.name or "—"),
            stage=str(row.stage or ""),
            stage_label=stage_label_map.get(str(row.stage or ""), str(row.stage or "")),
            deal_value=float(row.value or 0),
            ae_name=row.ae_name,
        )
        for row in rows
    ]
    result.sort(key=lambda r: -r.deal_value)
    return result


# ── Meeting Booked From drilldown ─────────────────────────────────────────────


class MeetingBookedFromDealItem(BaseModel):
    deal_id: str
    deal_name: str
    meeting_booked_with: Optional[str]
    meeting_booked_from: Optional[str]
    ae_name: Optional[str]
    sdr_name: Optional[str]


@router.get("/meeting-booked-from-deals", response_model=list[MeetingBookedFromDealItem])
async def meeting_booked_from_deals(
    session: DBSession,
    current_user: CurrentUser,
    channel: Annotated[Literal["Email", "LinkedIn", "Call"], Query()],
    user_id: Annotated[Optional[UUID], Query()] = None,
    window_days: Annotated[int, Query(ge=1, le=36500)] = 90,
    geography: Annotated[list[str], Query()] = [],
    from_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window start")] = None,
    to_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window end")] = None,
) -> list[MeetingBookedFromDealItem]:
    """Return deals where meeting was booked from the given channel within the window.

    For Email/LinkedIn: uses Deal.meeting_booked_from field.
    For Call: uses activities with demo_scheduled_booked/meeting_confirmed disposition
              (same source as the call_meeting_booked stat) to avoid missing deals
              that pre-date the meeting_booked_from field.

    Window resolution goes through _resolve_analytics_window (workspace tz,
    today counts as day one, from/to overrides) — the same math as the
    dashboard stat this list drills into.
    """
    _tz = workspace_zoneinfo(await get_analytics_settings(session))
    window_start, window_end = _resolve_analytics_window(window_days, from_date, to_date, tz=_tz)
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}

    AEUser = aliased(User)
    SDRUser = aliased(User)

    if channel == "Call":
        # Find deals via activities with demo_scheduled_booked/meeting_confirmed
        # disposition — same logic as call_meeting_booked stat.
        # Activities may be linked via deal_id (direct) or contact_id (prospect view).
        type_lower = func.lower(Activity.type)
        medium_lower = func.lower(Activity.medium)
        outcome_lower = func.lower(Activity.call_outcome)
        content_lower = func.lower(Activity.content)
        is_call = or_(type_lower == "call", medium_lower == "call")
        is_booked = or_(
            outcome_lower.in_(["demo_scheduled_booked", "meeting_confirmed"]),
            and_(
                func.lower(Activity.source) == "manual",
                or_(
                    content_lower.startswith("demo scheduled/booked"),
                    content_lower.startswith("meeting confirmed"),
                ),
            ),
        )
        base_activity_filters = [
            Activity.created_at >= window_start,
            Activity.created_at <= window_end,
            is_call,
            is_booked,
        ]
        if user_id is not None:
            base_activity_filters.append(Activity.created_by_id == user_id)

        # Path 1: activities directly linked to a deal
        direct_act_rows = (
            await session.execute(
                select(Activity.deal_id.label("deal_id"))
                .where(*base_activity_filters, Activity.deal_id.isnot(None))
                .distinct()
            )
        ).all()
        deal_ids: set[UUID] = {r.deal_id for r in direct_act_rows}

        # Path 2: activities linked to a contact → resolve deals via deal_contacts
        contact_act_rows = (
            await session.execute(
                select(Activity.contact_id.label("contact_id"))
                .where(*base_activity_filters, Activity.contact_id.isnot(None))
                .distinct()
            )
        ).all()
        contact_ids = [r.contact_id for r in contact_act_rows]
        if contact_ids:
            dc_rows = (
                await session.execute(
                    select(DealContact.deal_id)
                    .where(DealContact.contact_id.in_(contact_ids))
                )
            ).all()
            for dc in dc_rows:
                deal_ids.add(dc.deal_id)

        if not deal_ids:
            return []

        stmt = (
            select(
                Deal.id.label("deal_id"),
                Deal.name.label("deal_name"),
                Deal.meeting_booked_with.label("meeting_booked_with"),
                Deal.meeting_booked_from.label("meeting_booked_from"),
                Deal.geography.label("deal_geography"),
                AEUser.name.label("ae_name"),
                SDRUser.name.label("sdr_name"),
            )
            .outerjoin(AEUser, Deal.assigned_to_id == AEUser.id)
            .outerjoin(SDRUser, Deal.sdr_id == SDRUser.id)
            .where(
                Deal.id.in_(list(deal_ids)),
                # Same population rule as the dashboard deal scan: live
                # "deal"-pipeline rows only.
                Deal.pipeline_type == "deal",
                Deal.deleted_at.is_(None),
            )
        )
    else:
        # Email / LinkedIn: use Deal.meeting_booked_from field
        stmt = (
            select(
                Deal.id.label("deal_id"),
                Deal.name.label("deal_name"),
                Deal.meeting_booked_with.label("meeting_booked_with"),
                Deal.meeting_booked_from.label("meeting_booked_from"),
                Deal.geography.label("deal_geography"),
                AEUser.name.label("ae_name"),
                SDRUser.name.label("sdr_name"),
            )
            .outerjoin(AEUser, Deal.assigned_to_id == AEUser.id)
            .outerjoin(SDRUser, Deal.sdr_id == SDRUser.id)
            .where(
                Deal.meeting_booked_from == channel,
                Deal.created_at >= window_start,
                Deal.created_at <= window_end,
                Deal.pipeline_type == "deal",
                Deal.deleted_at.is_(None),
            )
        )
        if user_id is not None:
            stmt = stmt.where(
                or_(Deal.assigned_to_id == user_id, Deal.sdr_id == user_id)
            )

    rows = (await session.execute(stmt)).all()
    if filter_geographies:
        # Geography via _normalize_geography_key in Python, like the dashboard.
        rows = [r for r in rows if _normalize_geography_key(r.deal_geography) in filter_geographies]
    seen: set[str] = set()
    result = []
    for row in rows:
        did = str(row.deal_id)
        if did in seen:
            continue
        seen.add(did)
        result.append(MeetingBookedFromDealItem(
            deal_id=did,
            deal_name=str(row.deal_name or ""),
            meeting_booked_with=row.meeting_booked_with,
            meeting_booked_from=row.meeting_booked_from,
            ae_name=row.ae_name,
            sdr_name=row.sdr_name,
        ))
    return result
