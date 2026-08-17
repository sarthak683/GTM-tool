"""
Core Metric Dictionary — the single source of truth for every performance
number shown on any dashboard.

Rules:
- Every metric is an `async` function that takes (session, rep_id?, period).
- Every metric reads raw data; no metric calls another metric (to keep
  traces obvious and make caching simple in Phase 5).
- Outcomes read from `deal_stage_history`. Activities read from `activities`.
  Deduplication exactly as the spec requires.
- `rep_id=None` means workspace-wide (admin view, leaderboards, funnel).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, timezone as dt_timezone
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, case, cast, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.deal import Deal
from app.models.deal_stage_history import DealStageHistory
from app.models.meeting import Meeting
from app.services.business_days import business_days_between
from app.services.metric_definitions import (
    call_unit as _call_unit,
    dedupe_meetings_across_sources,
    meeting_happened,
    meeting_rep_ids,
    outbound_email_filter,
    reply_email_filter,
)


# ── Period resolver ──────────────────────────────────────────────────────────

PeriodGranularity = Literal["week", "month", "quarter", "custom"]


@dataclass(frozen=True)
class Period:
    start: datetime  # inclusive, UTC
    end: datetime    # exclusive, UTC
    granularity: PeriodGranularity
    label: str


def resolve_period(
    granularity: PeriodGranularity,
    anchor: Optional[date] = None,
    tz_offset_hours: int = 0,
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None,
    tz_name: Optional[str] = None,
) -> Period:
    """
    Turn a granularity + anchor date into an explicit [start, end) window in
    UTC.

    ``tz_name`` (IANA, e.g. "Asia/Kolkata" — normally the workspace_timezone
    analytics setting) makes boundaries fall on LOCAL midnights, DST-correct
    per boundary. It wins over ``tz_offset_hours``, which is kept for callers
    that still pass a plain offset. The anchor "what week/month is it right
    now" question is also answered in that zone — at 2am IST on the 1st, UTC
    is still last month, and the scorecard used to show the wrong period.
    """
    zone: Optional[ZoneInfo] = None
    if tz_name:
        try:
            zone = ZoneInfo(tz_name)
        except Exception:
            zone = None
    if anchor is None:
        anchor = (
            datetime.now(dt_timezone.utc).astimezone(zone).date()
            if zone
            else datetime.utcnow().date()
        )

    if granularity == "custom":
        if not (custom_start and custom_end):
            raise ValueError("custom period requires custom_start and custom_end")
        start_local = datetime.combine(custom_start, time.min)
        end_local = datetime.combine(custom_end + timedelta(days=1), time.min)
        label = f"{custom_start.isoformat()} → {custom_end.isoformat()}"
    elif granularity == "week":
        # ISO week: Monday start, Sunday end.
        monday = anchor - timedelta(days=anchor.weekday())
        start_local = datetime.combine(monday, time.min)
        end_local = start_local + timedelta(days=7)
        iso_year, iso_week, _ = monday.isocalendar()
        label = f"{iso_year}-W{iso_week:02d}"
    elif granularity == "month":
        first = anchor.replace(day=1)
        if first.month == 12:
            next_first = first.replace(year=first.year + 1, month=1)
        else:
            next_first = first.replace(month=first.month + 1)
        start_local = datetime.combine(first, time.min)
        end_local = datetime.combine(next_first, time.min)
        label = first.strftime("%B %Y")
    elif granularity == "quarter":
        q_start_month = ((anchor.month - 1) // 3) * 3 + 1
        first = date(anchor.year, q_start_month, 1)
        if q_start_month + 3 > 12:
            next_first = date(first.year + 1, 1, 1)
        else:
            next_first = date(first.year, q_start_month + 3, 1)
        start_local = datetime.combine(first, time.min)
        end_local = datetime.combine(next_first, time.min)
        label = f"Q{(q_start_month - 1)//3 + 1} {first.year}"
    else:
        raise ValueError(f"unknown granularity: {granularity}")

    if zone is not None:
        start_utc = start_local.replace(tzinfo=zone).astimezone(dt_timezone.utc).replace(tzinfo=None)
        end_utc = end_local.replace(tzinfo=zone).astimezone(dt_timezone.utc).replace(tzinfo=None)
        return Period(start=start_utc, end=end_utc, granularity=granularity, label=label)

    offset = timedelta(hours=tz_offset_hours)
    return Period(
        start=start_local - offset,
        end=end_local - offset,
        granularity=granularity,
        label=label,
    )


def previous_period(period: Period) -> Period:
    """Return the immediately preceding period of the same granularity."""
    span = period.end - period.start
    return Period(
        start=period.start - span,
        end=period.start,
        granularity=period.granularity,
        label=f"prev-{period.label}",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _activity_rep_filter(rep_id: Optional[UUID]):
    if rep_id is None:
        return Activity.id == Activity.id  # always true
    return Activity.created_by_id == rep_id


def _deal_rep_filter(rep_id: Optional[UUID]):
    if rep_id is None:
        return Deal.id == Deal.id
    return Deal.assigned_to_id == rep_id


def _distinct_call_day_key():
    """Group key for call metrics: one rep dialling one contact on one day.

    Repeat dials to the SAME contact on the same day collapse into one unit —
    that is the intended definition, so a rep can't inflate the metric by
    redialling one number.

    Calls with no contact linked (``contact_id IS NULL`` — Aircall couldn't
    match the number to a CRM contact, or the rep logged a manual dial) fall
    back to the activity's own id, so each counts on its own. Coalescing them
    to a constant instead made every unlinked call a rep made in a day collapse
    into a single unit, which silently under-reported dial counts.
    """
    return func.concat(
        func.coalesce(cast(Activity.created_by_id, String), ""),
        ":",
        func.coalesce(
            cast(Activity.contact_id, String),
            cast(Activity.id, String),
        ),
        ":",
        cast(func.date(Activity.created_at), String),
    )


def _connected_call_filter():
    outcome = func.lower(func.coalesce(Activity.call_outcome, ""))
    content = func.lower(func.trim(func.coalesce(Activity.content, "")))
    connected_prefixes = (
        "demo scheduled/booked",
        "interested/follow-up required",
        "meeting confirmed",
        "gatekeeper (connected to admin, not lead)",
        "connected - not interested",
        "do not contact/dnc",
        "contact poor fit",
        "redirected to other icp",
        "call back later/rescheduled",
    )
    return or_(
        outcome.in_(("connected", "answered", "completed", "success")),
        *[content.like(f"{prefix}%") for prefix in connected_prefixes],
    )


# ── Activity metrics ─────────────────────────────────────────────────────────


async def calls_made(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    """Every dial the rep logged in the period — one row, one call.

    This used to count DISTINCT rep+contact+day groups, so a rep who dialled
    the same contact three times in a day saw "1". Reps count dials, the label
    reads "Calls made", and the mismatch read as the CRM losing their work.
    The deduplicated view is still available as ``contacts_dialed``.
    """
    stmt = select(func.count(distinct(_call_unit()))).select_from(Activity).where(
        Activity.type == "call",
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def calls_connected(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    """Connected dials, in the same one-call units as ``calls_made``.

    Must stay in the same units as ``calls_made`` — ``connect_rate`` divides
    one by the other, so mixing raw counts with deduplicated groups would
    silently distort the rate.
    """
    stmt = select(func.count(distinct(_call_unit()))).select_from(Activity).where(
        Activity.type == "call",
        _connected_call_filter(),
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def contacts_dialed(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    """Distinct rep+contact+day dial groups — the previous ``calls_made``.

    Kept as its own metric so the anti-gaming view (redials to one number
    don't inflate the number) is still available alongside the true dial count.
    """
    stmt = select(func.count(distinct(_distinct_call_day_key()))).where(
        Activity.type == "call",
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def emails_sent(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    """Outbound emails a rep actually SENT.

    Definition lives in app.services.metric_definitions.outbound_email_filter —
    SHARED with SalesAnalytics so the two surfaces cannot drift apart again.
    """
    stmt = select(func.count(Activity.id)).where(
        outbound_email_filter(),
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def emails_replied_to(
    session: AsyncSession, rep_id: Optional[UUID], period: Period, lookback_days: int = 1
) -> int:
    """Prospect replies received in the period (shared definition —
    metric_definitions.reply_email_filter)."""
    stmt = select(func.count(Activity.id)).where(
        reply_email_filter(),
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def linkedin_whatsapp_touches(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    stmt = select(func.count(Activity.id)).where(
        Activity.medium.in_(["linkedin", "whatsapp"]),
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def meetings_done(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    """Meetings that actually HAPPENED in the period.

    Uses the SHARED definitions (metric_definitions): cross-source dedupe (a
    tl;dv row and its Google Calendar twin count once), happened-inference
    (explicit held status OR past + not cancelled — reps rarely flip the
    status by hand), and owner/deal-owner/attendee attribution. The old
    version required status in (held, completed, done) on raw rows, so it
    undercounted vs the dashboard for the same rep and label.
    """
    rows = (
        await session.execute(
            select(
                Meeting.id,
                Meeting.scheduled_at,
                Meeting.status,
                Meeting.company_id,
                Meeting.deal_id,
                Meeting.external_source,
                Meeting.owner_user_id,
                Meeting.attendees,
            ).where(
                Meeting.scheduled_at >= period.start,
                Meeting.scheduled_at < period.end,
            )
        )
    ).all()
    happened = [row for row in rows if meeting_happened(row)]
    deduped = dedupe_meetings_across_sources(happened)
    if rep_id is None:
        return len(deduped)

    deal_ids = {row.deal_id for row in deduped if row.deal_id}
    deal_owner: dict = {}
    if deal_ids:
        deal_owner = dict(
            (
                await session.execute(
                    select(Deal.id, Deal.assigned_to_id).where(Deal.id.in_(deal_ids))
                )
            ).all()
        )
    from app.models.user import User

    rep_email = (
        await session.execute(select(User.email).where(User.id == rep_id))
    ).scalar_one_or_none()
    user_ids_by_email = {rep_email.strip().lower(): rep_id} if rep_email else {}
    return sum(
        1
        for row in deduped
        if rep_id in meeting_rep_ids(row, deal_owner=deal_owner, user_ids_by_email=user_ids_by_email)
    )


async def total_touchpoints(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    calls = await calls_connected(session, rep_id, period)
    emails = await emails_sent(session, rep_id, period)
    liw = await linkedin_whatsapp_touches(session, rep_id, period)
    meetings = await meetings_done(session, rep_id, period)
    return calls + emails + liw + meetings


async def crm_updates(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> int:
    """
    Count of stage / amount / close date / MEDDPICC / contact edits made
    by the rep. Reads activities of type 'field_change' or 'stage_change'
    or 'qualification_update'.
    """
    stmt = select(func.count(Activity.id)).where(
        Activity.type.in_(["field_change", "stage_change", "qualification_update"]),
        Activity.created_at >= period.start,
        Activity.created_at < period.end,
        _activity_rep_filter(rep_id),
    )
    return (await session.execute(stmt)).scalar_one() or 0


# ── Outcome metrics (read from deal_stage_history) ───────────────────────────


async def _entered_stage_count(
    session: AsyncSession,
    rep_id: Optional[UUID],
    period: Period,
    to_stage: str,
) -> int:
    stmt = (
        select(func.count(distinct(DealStageHistory.deal_id)))
        .join(Deal, Deal.id == DealStageHistory.deal_id)
        .where(
            DealStageHistory.to_stage == to_stage,
            DealStageHistory.changed_at >= period.start,
            DealStageHistory.changed_at < period.end,
            _deal_rep_filter(rep_id),
        )
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def demos_booked(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "demo_scheduled")


async def demos_done(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "demo_done")


async def qualified_leads(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "qualified_lead")


async def pocs_procured(session, rep_id, period):
    # Deals entering POC AGREED or POC WIP within the period.
    stmt = (
        select(func.count(distinct(DealStageHistory.deal_id)))
        .join(Deal, Deal.id == DealStageHistory.deal_id)
        .where(
            DealStageHistory.to_stage.in_(["poc_agreed", "poc_wip"]),
            DealStageHistory.changed_at >= period.start,
            DealStageHistory.changed_at < period.end,
            _deal_rep_filter(rep_id),
        )
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def pocs_done(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "poc_done")


async def closed_won(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "closed_won")


async def closed_lost(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "closed_lost")


async def disqualified(session, rep_id, period):
    return await _entered_stage_count(session, rep_id, period, "not_a_fit")


async def closed_won_value(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> Decimal:
    # Distinct-deal subquery, NOT a join: a deal that re-entered closed_won
    # within the window has multiple history rows, and the old join summed its
    # value once per row. Keeps the count (closed_won) and the value on the
    # same dedupe rule.
    won_deal_ids = (
        select(DealStageHistory.deal_id)
        .where(
            DealStageHistory.to_stage == "closed_won",
            DealStageHistory.changed_at >= period.start,
            DealStageHistory.changed_at < period.end,
        )
        .distinct()
    )
    stmt = select(func.coalesce(func.sum(Deal.value), 0)).where(
        Deal.id.in_(won_deal_ids),
        _deal_rep_filter(rep_id),
    )
    return Decimal((await session.execute(stmt)).scalar_one() or 0)


# ── Efficiency metrics ───────────────────────────────────────────────────────


def _safe_ratio(numer: float, denom: float) -> float:
    return round(numer / denom, 4) if denom else 0.0


async def connect_rate(session, rep_id, period) -> float:
    made = await calls_made(session, rep_id, period)
    connected = await calls_connected(session, rep_id, period)
    return _safe_ratio(connected, made)


async def reply_rate(session, rep_id, period, lookback_days: int = 30) -> float:
    sent = await emails_sent(session, rep_id, period)
    replied = await emails_replied_to(session, rep_id, period, lookback_days)
    return _safe_ratio(replied, sent)


async def demo_show_up_rate(session, rep_id, period) -> float:
    booked = await demos_booked(session, rep_id, period)
    done = await demos_done(session, rep_id, period)
    return _safe_ratio(done, booked)


async def overall_win_rate(session, rep_id, period) -> float:
    won = await closed_won(session, rep_id, period)
    lost = await closed_lost(session, rep_id, period)
    return _safe_ratio(won, won + lost)


async def avg_cycle_time_days(session, rep_id, period) -> float:
    """
    Days from first activity to Closed Won, for deals that closed-won in
    the period. Uses the earliest `deal_stage_history.changed_at` as first
    activity (that's the backfill-current row for pre-existing deals, and
    the creation row for new ones).
    """
    first_seen = (
        select(
            DealStageHistory.deal_id.label("deal_id"),
            func.min(DealStageHistory.changed_at).label("first_at"),
        )
        .group_by(DealStageHistory.deal_id)
        .subquery()
    )
    won = (
        select(
            DealStageHistory.deal_id.label("deal_id"),
            DealStageHistory.changed_at.label("won_at"),
        )
        .where(
            DealStageHistory.to_stage == "closed_won",
            DealStageHistory.changed_at >= period.start,
            DealStageHistory.changed_at < period.end,
        )
        .subquery()
    )
    stmt = (
        select(
            func.avg(
                func.extract("epoch", won.c.won_at - first_seen.c.first_at) / 86400.0
            )
        )
        .select_from(won)
        .join(first_seen, first_seen.c.deal_id == won.c.deal_id)
        .join(Deal, Deal.id == won.c.deal_id)
        .where(_deal_rep_filter(rep_id))
    )
    avg_days = (await session.execute(stmt)).scalar_one()
    return float(avg_days or 0.0)


async def touches_per_won(session, rep_id, period) -> float:
    won = await closed_won(session, rep_id, period)
    total = await total_touchpoints(session, rep_id, period)
    return _safe_ratio(total, won)


# ── Stage conversion (for the funnel grid) ───────────────────────────────────


async def stage_conversion(
    session: AsyncSession,
    period: Period,
    from_stage: str,
    to_stage: str,
    rep_id: Optional[UUID] = None,
) -> dict:
    """
    For deals that entered `from_stage` during the period, how many ever
    reached `to_stage` and the median days it took.
    """
    entered = (
        select(
            DealStageHistory.deal_id.label("deal_id"),
            DealStageHistory.changed_at.label("from_at"),
        )
        .join(Deal, Deal.id == DealStageHistory.deal_id)
        .where(
            DealStageHistory.to_stage == from_stage,
            DealStageHistory.changed_at >= period.start,
            DealStageHistory.changed_at < period.end,
            _deal_rep_filter(rep_id),
        )
        .subquery()
    )
    reached = (
        select(
            DealStageHistory.deal_id.label("deal_id"),
            func.min(DealStageHistory.changed_at).label("to_at"),
        )
        .where(DealStageHistory.to_stage == to_stage)
        .group_by(DealStageHistory.deal_id)
        .subquery()
    )
    joined = (
        select(
            entered.c.deal_id,
            entered.c.from_at,
            reached.c.to_at,
        )
        .select_from(entered)
        .outerjoin(reached, reached.c.deal_id == entered.c.deal_id)
        .subquery()
    )
    stmt = select(
        func.count(joined.c.deal_id).label("entered_count"),
        func.count(joined.c.to_at).label("reached_count"),
        func.percentile_cont(0.5).within_group(
            func.extract("epoch", joined.c.to_at - joined.c.from_at) / 86400.0
        ).label("median_days"),
    ).where(or_(joined.c.to_at.is_(None), joined.c.to_at >= joined.c.from_at))
    row = (await session.execute(stmt)).one()
    entered_count = row.entered_count or 0
    reached_count = row.reached_count or 0
    return {
        "from_stage": from_stage,
        "to_stage": to_stage,
        "deals": entered_count,
        "conv_rate": _safe_ratio(reached_count, entered_count),
        "median_days": float(row.median_days) if row.median_days is not None else None,
    }


# ── Pipeline delta (for scorecard Pipeline block) ────────────────────────────


async def pipeline_delta(
    session: AsyncSession, rep_id: Optional[UUID], period: Period
) -> dict:
    """New opportunities created this period, and deals moved to NOT FIT / CLOSED LOST."""
    created_stmt = select(func.count(Deal.id), func.coalesce(func.sum(Deal.value), 0)).where(
        Deal.created_at >= period.start,
        Deal.created_at < period.end,
        _deal_rep_filter(rep_id),
    )
    created_count, created_value = (await session.execute(created_stmt)).one()

    lost_or_nf = (
        select(func.count(distinct(DealStageHistory.deal_id)))
        .join(Deal, Deal.id == DealStageHistory.deal_id)
        .where(
            DealStageHistory.to_stage.in_(["not_a_fit", "closed_lost"]),
            DealStageHistory.changed_at >= period.start,
            DealStageHistory.changed_at < period.end,
            _deal_rep_filter(rep_id),
        )
    )
    lost_count = (await session.execute(lost_or_nf)).scalar_one() or 0
    return {
        "created_count": created_count or 0,
        "created_value": float(created_value or 0),
        "exited_count": lost_count,
    }


# ── Stuck-deal detection (for Deal Health + At-risk block) ───────────────────


async def stuck_deals(
    session: AsyncSession,
    stuck_thresholds_days: dict[str, int],
    rep_id: Optional[UUID] = None,
    now: Optional[datetime] = None,
) -> list[dict]:
    """
    Open deals whose current stage dwell exceeds the workspace threshold.

    Dwell is measured from ``Deal.stage_entered_at`` — the same field the
    board computes "days in stage" from — with the latest stage-history row
    only as a fallback for legacy deals. The old version used ONLY
    ``max(history.changed_at)``, which diverged from the board exactly on
    deals moved by paths that skipped history (now instrumented, but the
    historical rows remain), so a deal could be "stuck" on the scorecard and
    fresh on the board at the same time.

    Dwell is counted in BUSINESS days (Mon-Fri) via ``business_days_between``
    — the thresholds are documented as business days, and the board's
    ``is_stalled`` flag uses the same helper, so the two surfaces agree.
    """
    now = now or datetime.utcnow()
    latest = (
        select(
            DealStageHistory.deal_id.label("deal_id"),
            func.max(DealStageHistory.changed_at).label("entered_at"),
        )
        .group_by(DealStageHistory.deal_id)
        .subquery()
    )
    stmt = (
        select(
            Deal.id,
            Deal.name,
            Deal.stage,
            func.coalesce(Deal.stage_entered_at, latest.c.entered_at, Deal.created_at).label("entered_at"),
        )
        .select_from(Deal)
        .outerjoin(latest, latest.c.deal_id == Deal.id)
        .where(
            Deal.stage.in_(list(stuck_thresholds_days.keys())),
            Deal.deleted_at.is_(None),
            _deal_rep_filter(rep_id),
        )
    )
    rows = (await session.execute(stmt)).all()
    out: list[dict] = []
    for r in rows:
        threshold = stuck_thresholds_days.get(r.stage)
        if threshold is None:
            continue
        if r.entered_at is None:
            continue
        dwell_days = business_days_between(r.entered_at, now)
        if dwell_days > threshold:
            out.append(
                {
                    "deal_id": str(r.id),
                    "deal_name": r.name,
                    "stage": r.stage,
                    "dwell_days": dwell_days,
                    "threshold_days": threshold,
                    "over_by_days": dwell_days - threshold,
                }
            )
    out.sort(key=lambda d: d["over_by_days"], reverse=True)
    return out


# ── RAG computation ──────────────────────────────────────────────────────────


def compute_rag(attainment_pct: float, bands: dict) -> str:
    if attainment_pct >= bands.get("green_min", 1.0):
        return "green"
    if attainment_pct >= bands.get("amber_min", 0.7):
        return "amber"
    return "red"
