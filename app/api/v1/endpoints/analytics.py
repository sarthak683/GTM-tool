from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.core.analytics_defaults import DEFAULT_STAGE_PROBABILITIES
from app.core.dependencies import CurrentUser, DBSession
from app.models.activity import Activity
from app.models.company import Company
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.meeting import Meeting
from app.models.user import User
from app.services.analytics_settings import get_analytics_settings
from app.services.company_stage_milestones import MILESTONE_LABELS, backfill_company_stage_milestones
from app.services.deal_stages import get_configured_deal_stages
from app.services.outreach_analytics import build_outreach_overview

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

PROPOSAL_STAGES = {"poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop"}
HOT_MEETING_MARKERS = {"meeting_booked", "call booked", "demo booked"}
REAL_MEETING_SOURCES = {"", "google_calendar", "tldv", "manual"}

# Roles that count as a sales rep in activity analytics. Admins (and any other
# role) are NOT reps — their emails/calls/meetings must not inflate rep metrics
# or appear as a rep row. User.role is one of: admin | ae | sdr.
REP_ROLES = {"ae", "sdr"}

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
    demo_done_count: int = 0
    poc_agreed_count: int = 0
    poc_wip_count: int = 0
    poc_done_count: int = 0
    closed_won_count: int = 0
    closed_won_value: float = 0.0
    milestone_deals: list[MilestoneDealRow] = []
    # Same metrics for the immediately-preceding window of equal length, so the
    # UI can render period-over-period trend deltas on the milestone KPIs. These
    # are window-bound counts (point-in-time pipeline metrics are not compared).
    prev_demo_done_count: int = 0
    prev_poc_agreed_count: int = 0
    prev_poc_wip_count: int = 0
    prev_poc_done_count: int = 0
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
    email_opens: int = 0
    email_replies: int = 0
    linkedin_reachouts: int = 0
    meetings: int
    total: int
    active_deals: int
    pipeline_amount: float
    # SDR demo funnel (attributed to the account's SDR). demos_converted counts
    # done demos whose account reached a qualified deal or beyond.
    demos_scheduled: int = 0
    demos_done: int = 0
    demos_converted: int = 0


class RepActivityWeekRow(BaseModel):
    week_key: str
    label: str
    week_start: str
    week_end: str
    emails: int = 0
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


def _activity_rep_id(
    row,
    *,
    deal_owner: dict[UUID, UUID | None],
    contact_owner: dict[UUID, UUID | None],
) -> UUID | None:
    source = str(row.source or "").strip().lower()
    medium = str(row.medium or "").strip().lower()
    kind = str(row.type or "").strip().lower()
    metadata = row.event_metadata if isinstance(row.event_metadata, dict) else {}

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
        Literal["emails", "calls", "connected_calls", "live_calls", "linkedin_reachouts", "meetings", "total"],
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

    user_rows = (await session.execute(select(User.id, User.name, User.email, User.role))).all()
    users = {row.id: row.name for row in user_rows}
    user_emails = {row.id: str(row.email or "").strip().lower() for row in user_rows}
    user_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in user_rows if row.email}
    # Only ae/sdr users are reps; admin activity must not surface in the drilldown.
    rep_user_ids = {row.id for row in user_rows if str(row.role or "").strip().lower() in REP_ROLES}

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
        if metric == "emails":
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
    if metric != "meetings":
        activity_stmt = (
            select(Activity)
            .where(Activity.created_at >= window_start, Activity.created_at <= window_end)
            .where(activity_metric_filter())
        )
        if rep_id:
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
        # Other metrics paginate this stream alone, so keep the SQL offset.
        if metric == "total":
            activity_stmt = activity_stmt.order_by(Activity.created_at.desc()).limit(offset + limit + 1)
        else:
            activity_stmt = activity_stmt.order_by(Activity.created_at.desc()).offset(offset).limit(limit + 1)
        activities = (await session.execute(activity_stmt)).scalars().all()

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

        for activity in activity_page:
            if not activity.contact_id and not activity.deal_id:
                continue
            row_rep_id = _activity_rep_id(activity, deal_owner=deal_owner, contact_owner=contact_owner)
            if not _is_rep(row_rep_id, rep_user_ids):
                continue
            if rep_id and row_rep_id != rep_id:
                continue
            rep_email = user_emails.get(row_rep_id) if row_rep_id else None
            direction = None
            if str(activity.type or "").strip().lower() == "email" or str(activity.medium or "").strip().lower() == "email":
                direction = "outbound" if rep_email and str(activity.email_from or "").strip().lower() == rep_email else "inbound"
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
                    subject=activity.email_subject or activity.content,
                    direction=direction,
                    from_email=activity.email_from,
                    to_email=activity.email_to,
                    call_outcome=activity.call_outcome,
                    call_duration=activity.call_duration,
                    contact_name=contact_names.get(activity.contact_id),
                    contact_email=contact_emails.get(activity.contact_id),
                    company_name=company_names.get(company_id),
                    deal_name=deal_names.get(activity.deal_id),
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
    user_rows = (await session.execute(select(User.id, User.name, User.email, User.role))).all()
    users = {row.id: row.name for row in user_rows}
    user_emails = {row.id: str(row.email or "").strip().lower() for row in user_rows}
    user_roles = {row.id: str(row.role or "").strip().lower() for row in user_rows}
    user_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in user_rows if row.email}
    # Only ae/sdr users are reps; admin activity must not inflate rep metrics
    # or create an admin rep row (the "Rakesh 419 emails" leak).
    rep_user_ids = {row.id for row in user_rows if str(row.role or "").strip().lower() in REP_ROLES}

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
                Activity.email_from,
            ).where(Activity.created_at >= window_start, Activity.created_at <= window_end)
        )
    ).all()
    # Apply rep filter on activities by checking ownership
    if filter_rep_ids:
        activity_rows = [
            row for row in activity_rows
            if _activity_rep_id(row, deal_owner=deal_owner, contact_owner=contact_owner) in filter_rep_ids
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

    for row in activity_rows:
        row_rep_id = _activity_rep_id(
            row,
            deal_owner=deal_owner,
            contact_owner=contact_owner,
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
        elif medium == "email" or kind == "email":
            # All email events share type="email"; the sent/opened/replied
            # distinction lives in event_metadata.event_type (Instantly) or
            # source (replies). Count SENT for the emails total, and tally
            # opens/replies separately so the cards can show open/reply rate
            # over emails sent. Personal-sync rows carry no event_type → treated
            # as sent (they ARE real sent/received emails).
            meta = row.event_metadata if isinstance(row.event_metadata, dict) else {}
            event_type = str(meta.get("event_type") or "").strip().lower()
            src = str(row.source or "").strip().lower()
            if event_type == "email_opened":
                activity_bucket["email_opens"] = activity_bucket.get("email_opens", 0) + 1
            elif event_type == "reply_received" or src == "email_reply":
                activity_bucket["email_replies"] = activity_bucket.get("email_replies", 0) + 1
            elif event_type == "email_sent":
                # Instantly campaign send — always outbound.
                activity_bucket["emails"] += 1
                if week_counts is not None:
                    week_counts["emails"] += 1
            elif event_type == "":
                # Personal-sync (gmail) row carries no event_type and can be a
                # SENT or a RECEIVED email. Per the "outbound only" rule, count it
                # only when the attributed rep is the sender; received mail is not
                # a rep touch. (Same direction signal the drilldown uses.)
                rep_email = user_emails.get(row_rep_id, "")
                sender = str(row.email_from or "").strip().lower()
                if rep_email and sender == rep_email:
                    activity_bucket["emails"] += 1
                    if week_counts is not None:
                        week_counts["emails"] += 1
        elif medium == "linkedin" or kind == "linkedin":
            activity_bucket["linkedin_reachouts"] += 1
            if week_counts is not None:
                week_counts["linkedin_reachouts"] += 1
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
                func.lower(func.coalesce(Meeting.meeting_type, "")) == "demo",
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
                "linkedin_reachouts": 0,
                "meetings": 0,
                "total": 0,
                "active_deals": 0,
                "pipeline_amount": 0.0,
            },
        )
        bucket["demos_scheduled"] = int(bucket.get("demos_scheduled", 0)) + 1
        if done:
            bucket["demos_done"] = int(bucket.get("demos_done", 0)) + 1
            if converted:
                bucket["demos_converted"] = int(bucket.get("demos_converted", 0)) + 1

    for row in deduped_demos:
        if filter_geographies:
            region_key = _normalize_geography_key(company_region_for_demo.get(row.company_id))
            if region_key not in filter_geographies:
                continue
        is_done = str(row.status or "").strip().lower() == "completed"
        is_converted = bool(row.company_id and row.company_id in converted_company_ids)
        bump_demo(company_sdr.get(row.company_id), done=is_done, converted=is_converted)

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
            email_opens=int(bucket.get("email_opens", 0)),
            email_replies=int(bucket.get("email_replies", 0)),
            linkedin_reachouts=int(bucket["linkedin_reachouts"]),
            meetings=int(bucket["meetings"]),
            total=int(bucket["total"]),
            active_deals=int(bucket["active_deals"]),
            pipeline_amount=round(float(bucket["pipeline_amount"]), 2),
            demos_scheduled=int(bucket.get("demos_scheduled", 0)),
            demos_done=int(bucket.get("demos_done", 0)),
            demos_converted=int(bucket.get("demos_converted", 0)),
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
    milestone_stmt = (
        select(
            CompanyStageMilestone.milestone_key,
            CompanyStageMilestone.first_reached_at,
            Deal.name.label("deal_name"),
            Deal.value.label("deal_value"),
            Deal.close_date_est.label("close_date_est"),
            Deal.geography.label("deal_geography"),
            Company.name.label("company_name"),
        )
        .outerjoin(Deal, CompanyStageMilestone.deal_id == Deal.id)
        .outerjoin(Company, CompanyStageMilestone.company_id == Company.id)
        .where(
            CompanyStageMilestone.first_reached_at >= window_start,
            CompanyStageMilestone.first_reached_at <= window_end,
            CompanyStageMilestone.milestone_key.in_(["demo_done", "poc_agreed", "poc_wip", "poc_done", "closed_won"]),
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

    ms_demo_done = sum(1 for r in milestone_summary_rows if r.milestone_key == "demo_done")
    ms_poc_agreed = sum(1 for r in milestone_summary_rows if r.milestone_key == "poc_agreed")
    ms_poc_wip = sum(1 for r in milestone_summary_rows if r.milestone_key == "poc_wip")
    ms_poc_done = sum(1 for r in milestone_summary_rows if r.milestone_key == "poc_done")
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
            CompanyStageMilestone.milestone_key.in_(["demo_done", "poc_agreed", "poc_wip", "poc_done", "closed_won"]),
        )
    )
    if filter_rep_ids:
        prev_stmt = prev_stmt.where(Deal.assigned_to_id.in_(filter_rep_ids))
    prev_rows = (await session.execute(prev_stmt)).all()
    if filter_geographies:
        prev_rows = [r for r in prev_rows if _normalize_geography_key(r.deal_geography) in filter_geographies]

    prev_demo_done = sum(1 for r in prev_rows if r.milestone_key == "demo_done")
    prev_poc_agreed = sum(1 for r in prev_rows if r.milestone_key == "poc_agreed")
    prev_poc_wip = sum(1 for r in prev_rows if r.milestone_key == "poc_wip")
    prev_poc_done = sum(1 for r in prev_rows if r.milestone_key == "poc_done")
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
            demo_done_count=ms_demo_done,
            poc_agreed_count=ms_poc_agreed,
            poc_wip_count=ms_poc_wip,
            poc_done_count=ms_poc_done,
            closed_won_count=ms_closed_won,
            closed_won_value=round(ms_closed_won_value, 2),
            milestone_deals=ms_milestone_deals,
            prev_demo_done_count=prev_demo_done,
            prev_poc_agreed_count=prev_poc_agreed,
            prev_poc_wip_count=prev_poc_wip,
            prev_poc_done_count=prev_poc_done,
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


@router.get("/outreach")
async def get_outreach_analytics(
    session: DBSession,
    _user: CurrentUser,
    window_days: int = Query(default=90, ge=7, le=365),
    rep_email: Optional[str] = Query(default=None),
):
    """Outreach funnel, per-rep, per-sequence, and subject-line performance.

    Returns real-data aggregations from outreach_sequences + contacts +
    activities. Engagement counters (opens, clicks) are kept fresh by the
    periodic Instantly sync — this endpoint just reads them.
    """
    return await build_outreach_overview(
        session, window_days=window_days, rep_email=rep_email
    )
