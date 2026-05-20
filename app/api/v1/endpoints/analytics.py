from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.core.analytics_defaults import DEFAULT_STAGE_PROBABILITIES
from app.core.dependencies import CurrentUser, DBSession
from app.models.activity import Activity
from app.models.company import Company
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.meeting import Meeting
from app.models.user import User
from app.services.company_stage_milestones import MILESTONE_LABELS, backfill_company_stage_milestones
from app.services.deal_stages import get_configured_deal_stages

router = APIRouter(prefix="/analytics", tags=["analytics"])

PROPOSAL_STAGES = {"poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop"}
HOT_MEETING_MARKERS = {"meeting_booked", "call booked", "demo booked"}
REAL_MEETING_SOURCES = {"", "google_calendar", "tldv", "manual"}


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


class RepActivityRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    calls: int
    connected_calls: int = 0
    live_calls: int = 0
    emails: int
    linkedin_reachouts: int = 0
    meetings: int
    total: int
    active_deals: int
    pipeline_amount: float


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
    forecast_buckets: list[ForecastRow] = []
    forecast_granularity: str = "month"
    conversion_funnel: list[FunnelStep]
    monthly_unique_funnel: list[MonthlyUniqueFunnelRow]
    quota: QuotaState


class SalesActivityDrilldownRow(BaseModel):
    id: UUID
    kind: Literal["activity", "meeting"]
    activity_type: str
    occurred_at: datetime
    rep_user_id: Optional[UUID] = None
    rep_name: str
    source: Optional[str] = None
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
    now = datetime.utcnow()
    if from_date:
        window_start = datetime.fromisoformat(from_date)
    else:
        window_start = now - timedelta(days=window_days)
    if to_date:
        window_end = datetime.fromisoformat(to_date) + timedelta(days=1)
    else:
        window_end = now
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


def _normalize_geography_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "Unassigned"
    if raw in {"us", "usa", "united states", "united states of america", "na", "north america", "americas", "latam", "latin america", "canada", "mexico"}:
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


@router.get("/sales-activity-drilldown", response_model=SalesActivityDrilldownRead)
async def sales_activity_drilldown(
    session: DBSession,
    _user: CurrentUser,
    metric: Annotated[
        Literal["emails", "calls", "connected_calls", "live_calls", "linkedin_reachouts", "meetings", "total"],
        Query(description="Activity metric to inspect"),
    ],
    window_days: Annotated[int, Query(ge=30, le=365)] = 90,
    rep_id: Annotated[Optional[UUID], Query()] = None,
    geography: Annotated[list[str], Query()] = [],
    from_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window start")] = None,
    to_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window end")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    window_start, window_end = _resolve_analytics_window(window_days, from_date, to_date)
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}

    user_rows = (await session.execute(select(User.id, User.name, User.email))).all()
    users = {row.id: row.name for row in user_rows}
    user_emails = {row.id: str(row.email or "").strip().lower() for row in user_rows}
    user_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in user_rows if row.email}

    deal_stmt = select(Deal.id, Deal.name, Deal.assigned_to_id, Deal.company_id)
    if filter_geographies:
        deal_stmt = deal_stmt.where(Deal.geography.in_(list(filter_geographies)))
    if rep_id:
        deal_stmt = deal_stmt.where(Deal.assigned_to_id == rep_id)
    scoped_deal_rows = (await session.execute(deal_stmt)).all()
    scoped_deal_ids = {row.id for row in scoped_deal_rows}

    contact_stmt = select(Contact.id, Contact.assigned_to_id, Contact.company_id)
    if filter_geographies:
        contact_stmt = contact_stmt.outerjoin(Company, Contact.company_id == Company.id).where(Company.region.in_(list(filter_geographies)))
    if rep_id:
        contact_stmt = contact_stmt.where(Contact.assigned_to_id == rep_id)
    scoped_contact_rows = (await session.execute(contact_stmt)).all()
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
        activities = (
            await session.execute(
                activity_stmt.order_by(Activity.created_at.desc()).offset(offset).limit(limit + 1)
            )
        ).scalars().all()

        activity_page = activities[:limit]
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
            row_rep_id = _activity_rep_id(activity, deal_owner=deal_owner, contact_owner=contact_owner)
            if rep_id and row_rep_id != rep_id:
                continue
            rep_email = user_emails.get(row_rep_id) if row_rep_id else None
            direction = None
            if str(activity.type or "").strip().lower() == "email" or str(activity.medium or "").strip().lower() == "email":
                direction = "outbound" if rep_email and str(activity.email_from or "").strip().lower() == rep_email else "inbound"
            company_id = contact_company_ids.get(activity.contact_id) or deal_company_ids.get(activity.deal_id)
            rows.append(
                SalesActivityDrilldownRow(
                    id=activity.id,
                    kind="activity",
                    activity_type=str(activity.medium or activity.type or "activity"),
                    occurred_at=activity.created_at,
                    rep_user_id=row_rep_id,
                    rep_name=_label_for_rep(row_rep_id, users)[2],
                    source=activity.source,
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

    if metric in {"meetings", "total"}:
        meeting_stmt = select(Meeting).where(
            Meeting.is_internal.is_(False),
            or_(
                (Meeting.scheduled_at >= window_start) & (Meeting.scheduled_at <= window_end),
                Meeting.scheduled_at.is_(None) & (Meeting.created_at >= window_start) & (Meeting.created_at <= window_end),
            ),
        )
        if rep_id:
            meeting_stmt = meeting_stmt.where(
                or_(
                    Meeting.owner_user_id == rep_id,
                    Meeting.synced_by_user_id == rep_id,
                    Meeting.deal_id.in_(scoped_deal_ids or {UUID(int=0)}),
                )
            )
        elif filter_geographies:
            meeting_stmt = meeting_stmt.where(Meeting.deal_id.in_(scoped_deal_ids or {UUID(int=0)}))
        meeting_rows = (
            await session.execute(
                meeting_stmt.order_by(Meeting.scheduled_at.desc(), Meeting.created_at.desc()).offset(offset).limit(limit + 1)
            )
        ).scalars().all()

        meeting_page = meeting_rows[:limit]
        meeting_deal_ids = {meeting.deal_id for meeting in meeting_page if meeting.deal_id}
        meeting_company_ids = {meeting.company_id for meeting in meeting_page if meeting.company_id}
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

        for meeting in meeting_page:
            if meeting.status == "cancelled":
                continue
            source = str(meeting.external_source or "").strip().lower()
            if source not in REAL_MEETING_SOURCES:
                continue
            meeting_time = _meeting_reporting_timestamp(meeting, window_end=window_end)
            for row_rep_id in _meeting_rep_ids(meeting, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email):
                if rep_id and row_rep_id != rep_id:
                    continue
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
    if metric != "meetings":
        has_more = has_more or len(locals().get("activities", [])) > limit
    if metric in {"meetings", "total"}:
        has_more = has_more or len(locals().get("meeting_rows", [])) > limit
    return SalesActivityDrilldownRead(
        generated_at=datetime.utcnow(),
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
    window_days: Annotated[int, Query(ge=30, le=365)] = 90,
    rep_id: Annotated[list[UUID], Query()] = [],
    geography: Annotated[list[str], Query()] = [],
    from_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window start")] = None,
    to_date: Annotated[Optional[str], Query(description="ISO date YYYY-MM-DD — override window end")] = None,
    forecast_granularity: Annotated[str, Query(pattern="^(week|month)$", description="Bucket size for forecast_buckets")] = "month",
):
    filter_rep_ids = rep_id or []
    filter_geographies = {_normalize_geography_key(g) for g in geography if g}
    now = datetime.utcnow()
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

    deal_stmt = select(
        Deal.id,
        Deal.name,
        Deal.stage,
        Deal.value,
        Deal.close_date_est,
        Deal.days_in_stage,
        Deal.stage_entered_at,
        Deal.assigned_to_id,
        Deal.created_at,
        Deal.updated_at,
        Deal.geography,
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
    week_starts = _rolling_week_starts(window_start, window_end)
    user_rows = (await session.execute(select(User.id, User.name, User.email))).all()
    users = {row.id: row.name for row in user_rows}
    user_ids_by_email = {str(row.email or "").strip().lower(): row.id for row in user_rows if row.email}

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
            ).where(
                Meeting.is_internal.is_(False),
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
    # Parallel bucket map driven by `forecast_granularity` (week or month).
    # Kept separate from forecast_by_month so existing consumers that read
    # the monthly buckets keep working unchanged.
    forecast_by_bucket: dict[str, dict[str, float | int | str]] = {}
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
        probability = _stage_probability(stage_id)
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

            # Granular bucket (week or month). ISO weeks make the key
            # unambiguous across years (2026-W01, etc.).
            if forecast_granularity == "week":
                iso_year, iso_week, _ = row.close_date_est.isocalendar()
                bucket_key = f"{iso_year}-W{iso_week:02d}"
                # Label is the Monday of the ISO week, e.g. "Week of Mar 03".
                week_start = row.close_date_est - timedelta(days=row.close_date_est.weekday())
                bucket_label = f"Week of {week_start.strftime('%b %d')}"
            else:
                bucket_key = month_key
                bucket_label = _month_label(month_key)
            granular_bucket = forecast_by_bucket.setdefault(
                bucket_key,
                {
                    "key": bucket_key,
                    "label": bucket_label,
                    "deal_count": 0,
                    "amount": 0.0,
                    "weighted_amount": 0.0,
                },
            )
            granular_bucket["deal_count"] += 1
            granular_bucket["amount"] += amount
            granular_bucket["weighted_amount"] += weighted_amount

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
            activity_bucket["emails"] += 1
            if week_counts is not None:
                week_counts["emails"] += 1
        elif medium == "linkedin" or kind == "linkedin":
            activity_bucket["linkedin_reachouts"] += 1
            if week_counts is not None:
                week_counts["linkedin_reachouts"] += 1
        activity_bucket["total"] = (
            activity_bucket["calls"]
            + activity_bucket["emails"]
            + activity_bucket["linkedin_reachouts"]
            + activity_bucket["meetings"]
        )
        if week_counts is not None:
            week_counts["total"] = (
                week_counts["calls"]
                + week_counts["emails"]
                + week_counts["linkedin_reachouts"]
                + week_counts["meetings"]
            )

    def bump_meeting(row_rep_id: UUID | None, meeting_timestamp: datetime) -> None:
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
        meeting_bucket["total"] = (
            meeting_bucket["calls"]
            + meeting_bucket["emails"]
            + meeting_bucket["linkedin_reachouts"]
            + meeting_bucket["meetings"]
        )
        if week_counts is not None:
            week_counts["meetings"] += 1
            week_counts["total"] = (
                week_counts["calls"]
                + week_counts["emails"]
                + week_counts["linkedin_reachouts"]
                + week_counts["meetings"]
            )

    # tl;dv and Google Calendar both ingest the same real-world meeting from
    # different sources, producing two Meeting rows with different
    # external_source values but the same scheduled_at + customer. Without
    # dedup, recorded calls double-count (the "157 meetings" inflation Rakesh
    # saw in prod). We collapse on (scheduled_at floored to the minute,
    # company_id, deal_id, primary owner) and prefer the tl;dv row when both
    # exist because tl;dv only fires on calls that actually happened, while
    # google_calendar entries can include rescheduled/no-show events.
    _SOURCE_PRIORITY = {"tldv": 0, "google_calendar": 1, "manual": 2, "": 3}
    candidate_rows = []
    for row in meetings_rows:
        if row.status == "cancelled":
            continue
        source = str(row.external_source or "").strip().lower()
        if source not in REAL_MEETING_SOURCES:
            continue
        candidate_rows.append((source, row))

    seen: dict[tuple, tuple[int, object]] = {}
    for source, row in candidate_rows:
        primary_owner = row.owner_user_id or deal_owner.get(row.deal_id)
        bucket_minute = row.scheduled_at.replace(second=0, microsecond=0) if row.scheduled_at else None
        dedup_key = (bucket_minute, row.company_id, row.deal_id, primary_owner)
        priority = _SOURCE_PRIORITY.get(source, 99)
        existing = seen.get(dedup_key)
        if existing is None or priority < existing[0]:
            seen[dedup_key] = (priority, row)

    for _priority, row in seen.values():
        meeting_timestamp = _meeting_reporting_timestamp(row, window_end=window_end)
        for row_rep_id in _meeting_rep_ids(row, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email):
            if filter_rep_ids and row_rep_id not in filter_rep_ids:
                continue
            bump_meeting(row_rep_id, meeting_timestamp)

    rep_activity_rows = [
        RepActivityRow(
            key=str(bucket["key"]),
            user_id=bucket["user_id"],
            rep_name=str(bucket["rep_name"]),
            calls=int(bucket["calls"]),
            connected_calls=int(bucket["connected_calls"]),
            live_calls=int(bucket["live_calls"]),
            emails=int(bucket["emails"]),
            linkedin_reachouts=int(bucket["linkedin_reachouts"]),
            meetings=int(bucket["meetings"]),
            total=int(bucket["total"]),
            active_deals=int(bucket["active_deals"]),
            pipeline_amount=round(float(bucket["pipeline_amount"]), 2),
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

    forecast_bucket_rows = [
        ForecastRow(
            key=str(bucket["key"]),
            label=str(bucket["label"]),
            deal_count=int(bucket["deal_count"]),
            amount=round(float(bucket["amount"]), 2),
            weighted_amount=round(float(bucket["weighted_amount"]), 2),
        )
        for bucket in sorted(forecast_by_bucket.values(), key=lambda value: str(value["key"]))
    ]

    leads_count = sum(1 for row in contact_rows if row.created_at >= window_start)
    meeting_stage_contacts = sum(1 for row in contact_rows if _contact_meeting_signal(row))
    # Reuse the deduped set from rep-activity counting above so the funnel's
    # Meeting count agrees with per-rep totals (no tldv+gcal double-count).
    meetings_count = len(seen)
    proposal_count = sum(
        1
        for row in deal_rows
        if row.stage in PROPOSAL_STAGES and row.updated_at >= window_start
    )
    closed_won_count = sum(
        1
        for row in deal_rows
        if row.stage == "closed_won" and row.updated_at >= window_start
    )

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

    return SalesDashboardRead(
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
        ),
        highlights=highlights[:5],
        rep_activity=rep_activity_rows,
        rep_weekly_activity=rep_weekly_activity_rows,
        pipeline_by_stage=pipeline_stage_rows,
        pipeline_by_owner=owner_rows,
        velocity_by_stage=velocity_rows,
        forecast_by_month=forecast_rows,
        forecast_buckets=forecast_bucket_rows,
        forecast_granularity=forecast_granularity,
        conversion_funnel=funnel_rows,
        monthly_unique_funnel=monthly_unique_funnel,
        quota=QuotaState(
            configured=False,
            title="Quota setup required",
            message="Add rep or team targets to unlock quota attainment and gap-to-goal charts.",
        ),
    )
