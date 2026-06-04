"""
Performance Analytics router.

Surfaces:
- /performance/scorecard      — weekly/monthly per-rep scorecard
- /performance/funnel         — pipeline & funnel dashboard
- /performance/deal-health    — stuck deals
- /performance/forecast       — commit/best/worst + gap to quota
- /performance/leaderboards   — cross-rep cuts

All numbers flow through app.services.performance_metrics so every surface
agrees on definitions. This sits *alongside* the existing /analytics router
(old Sales Analytics page) — nothing there changes.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, select

from app.core.dependencies import CurrentUser, DBSession
from app.core.pods import get_pod, pod_ae_emails, pod_keys
from app.models.user import User
from app.core.dependencies import AdminUser
from app.services import performance_metrics as pm
from app.services.analytics_settings import get_analytics_settings, update_analytics_settings

router = APIRouter(prefix="/performance", tags=["performance"])


# ── Response models ──────────────────────────────────────────────────────────


class MetricValue(BaseModel):
    key: str
    label: str
    value: float
    target: Optional[float] = None
    attainment: Optional[float] = None  # value / target
    rag: Optional[str] = None


class ScorecardHeader(BaseModel):
    rep_id: Optional[str]
    rep_name: Optional[str]
    role: Optional[str]
    period_label: str
    period_start: datetime
    period_end: datetime
    overall_attainment: float
    overall_rag: str


class ScorecardBlock(BaseModel):
    title: str
    metrics: list[MetricValue]


class AtRiskDeal(BaseModel):
    deal_id: str
    deal_name: str
    stage: str
    dwell_days: int
    threshold_days: int
    over_by_days: int


class ScorecardResponse(BaseModel):
    header: ScorecardHeader
    activity: ScorecardBlock
    outcomes: ScorecardBlock
    efficiency: ScorecardBlock
    pipeline_delta: dict
    at_risk_deals: list[AtRiskDeal]


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _resolve_rep(
    session, current_user: User, rep_id: Optional[UUID]
) -> Optional[User]:
    """
    Non-admins always see their own scorecard. Admins may pass rep_id=null to
    get a workspace-wide view or a specific rep.
    """
    if current_user.role != "admin":
        return current_user
    if rep_id is None:
        return None
    row = (await session.execute(select(User).where(User.id == rep_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="rep not found")
    return row


def _targets_for(settings: dict, role: Optional[str], granularity: str) -> dict:
    key = "weekly_targets" if granularity == "week" else "monthly_targets"
    all_targets = settings.get(key, {})
    if role and role in all_targets:
        return all_targets[role]
    # Fall back to AE targets if role unknown; admins / workspace view get empty.
    return all_targets.get("ae", {})


def _metric(
    key: str,
    label: str,
    value: float,
    targets: dict,
    rag_bands: dict,
) -> MetricValue:
    target = targets.get(key)
    attainment = None
    rag = None
    if target:
        attainment = round(value / target, 4) if target else 0.0
        rag = pm.compute_rag(attainment, rag_bands)
    return MetricValue(
        key=key, label=label, value=float(value),
        target=float(target) if target else None,
        attainment=attainment, rag=rag,
    )


# ── Scorecard ────────────────────────────────────────────────────────────────


@router.get("/scorecard", response_model=ScorecardResponse)
async def get_scorecard(
    session: DBSession,
    current_user: CurrentUser,
    rep_id: Annotated[Optional[UUID], Query()] = None,
    period: Annotated[Literal["week", "month"], Query()] = "week",
    anchor: Annotated[Optional[date], Query()] = None,
):
    rep = await _resolve_rep(session, current_user, rep_id)
    rep_uuid = rep.id if rep else None
    settings = await get_analytics_settings(session)
    tz_offset = 0
    p = pm.resolve_period(period, anchor=anchor, tz_offset_hours=tz_offset)
    targets = _targets_for(settings, rep.role if rep else None, period)
    bands = settings["rag_bands"]

    # Activity block
    calls_c = await pm.calls_connected(session, rep_uuid, p)
    calls_m = await pm.calls_made(session, rep_uuid, p)
    emails_s = await pm.emails_sent(session, rep_uuid, p)
    emails_r = await pm.emails_replied_to(
        session, rep_uuid, p, lookback_days=settings.get("email_reply_lookback_days", 30)
    )
    liw = await pm.linkedin_whatsapp_touches(session, rep_uuid, p)
    meetings = await pm.meetings_done(session, rep_uuid, p)
    touch_total = calls_c + emails_s + liw + meetings
    updates = await pm.crm_updates(session, rep_uuid, p)

    activity = ScorecardBlock(
        title="Activity",
        metrics=[
            _metric("calls_made", "Calls made", calls_m, targets, bands),
            _metric("calls_connected", "Calls connected", calls_c, targets, bands),
            _metric("emails_sent", "Emails sent", emails_s, targets, bands),
            _metric("emails_replied_to", "Emails replied-to", emails_r, targets, bands),
            _metric("linkedin_whatsapp_touches", "LinkedIn / WhatsApp", liw, targets, bands),
            _metric("meetings_done", "Meetings done", meetings, targets, bands),
            _metric("total_touchpoints", "Total touchpoints", touch_total, targets, bands),
            _metric("crm_updates", "CRM updates", updates, targets, bands),
        ],
    )

    # Outcomes block
    d_booked = await pm.demos_booked(session, rep_uuid, p)
    d_done = await pm.demos_done(session, rep_uuid, p)
    q_leads = await pm.qualified_leads(session, rep_uuid, p)
    pocs_pr = await pm.pocs_procured(session, rep_uuid, p)
    pocs_dn = await pm.pocs_done(session, rep_uuid, p)
    won = await pm.closed_won(session, rep_uuid, p)
    lost = await pm.closed_lost(session, rep_uuid, p)
    nf = await pm.disqualified(session, rep_uuid, p)

    outcomes = ScorecardBlock(
        title="Outcomes",
        metrics=[
            _metric("demos_booked", "Demos booked", d_booked, targets, bands),
            _metric("demos_done", "Demos done", d_done, targets, bands),
            _metric("qualified_leads", "Qualified leads", q_leads, targets, bands),
            _metric("pocs_procured", "POCs procured (WIP)", pocs_pr, targets, bands),
            _metric("pocs_done", "POCs done", pocs_dn, targets, bands),
            _metric("closed_won", "Closed Won", won, targets, bands),
            _metric("closed_lost", "Closed Lost", lost, targets, bands),
            _metric("disqualified", "Disqualified (Not Fit)", nf, targets, bands),
        ],
    )

    # Efficiency block
    conn_rate = await pm.connect_rate(session, rep_uuid, p)
    rep_rate = await pm.reply_rate(session, rep_uuid, p, lookback_days=settings.get("email_reply_lookback_days", 30))
    show_up = await pm.demo_show_up_rate(session, rep_uuid, p)
    win_rt = await pm.overall_win_rate(session, rep_uuid, p)
    cycle = await pm.avg_cycle_time_days(session, rep_uuid, p)
    tpw = await pm.touches_per_won(session, rep_uuid, p)

    efficiency = ScorecardBlock(
        title="Efficiency",
        metrics=[
            MetricValue(key="connect_rate", label="Connect rate", value=conn_rate),
            MetricValue(key="reply_rate", label="Reply rate", value=rep_rate),
            MetricValue(key="demo_show_up_rate", label="Demo show-up", value=show_up),
            MetricValue(key="win_rate", label="Win rate", value=win_rt),
            MetricValue(key="avg_cycle_time_days", label="Avg cycle (days)", value=cycle),
            MetricValue(key="touches_per_won", label="Touches per Won", value=tpw),
        ],
    )

    pipe_delta = await pm.pipeline_delta(session, rep_uuid, p)
    at_risk_raw = await pm.stuck_deals(session, settings["stuck_thresholds_days"], rep_uuid)
    at_risk = [AtRiskDeal(**d) for d in at_risk_raw[:10]]

    # Overall attainment: mean of per-metric attainment across activity + outcomes
    atts = [
        m.attainment for m in (*activity.metrics, *outcomes.metrics)
        if m.attainment is not None
    ]
    overall_att = round(sum(atts) / len(atts), 4) if atts else 0.0
    overall_rag = pm.compute_rag(overall_att, bands) if atts else "red"

    header = ScorecardHeader(
        rep_id=str(rep.id) if rep else None,
        rep_name=rep.name if rep else "Workspace",
        role=rep.role if rep else None,
        period_label=p.label,
        period_start=p.start,
        period_end=p.end,
        overall_attainment=overall_att,
        overall_rag=overall_rag,
    )

    return ScorecardResponse(
        header=header,
        activity=activity,
        outcomes=outcomes,
        efficiency=efficiency,
        pipeline_delta=pipe_delta,
        at_risk_deals=at_risk,
    )


# ── Funnel & Conversion ──────────────────────────────────────────────────────


class StageCount(BaseModel):
    stage: str
    deal_count: int
    total_value: float


class ConversionRow(BaseModel):
    from_stage: str
    to_stage: str
    deals: int
    conv_rate: float
    median_days: Optional[float]


class FunnelResponse(BaseModel):
    period_label: str
    period_start: datetime
    period_end: datetime
    funnel: list[StageCount]
    conversion: list[ConversionRow]
    movement: dict  # {"advanced": int, "regressed": int, "exited": int, "entered": int}


@router.get("/funnel", response_model=FunnelResponse)
async def get_funnel(
    session: DBSession,
    current_user: CurrentUser,
    period: Annotated[Literal["week", "month", "quarter"], Query()] = "month",
    anchor: Annotated[Optional[date], Query()] = None,
    rep_id: Annotated[Optional[UUID], Query()] = None,
):
    from app.models.deal import Deal, DEAL_STAGES
    from app.models.deal_stage_history import DealStageHistory
    from sqlalchemy import func, select, or_

    settings = await get_analytics_settings(session)
    p = pm.resolve_period(period, anchor=anchor)

    rep = await _resolve_rep(session, current_user, rep_id)
    rep_uuid = rep.id if rep else None

    # Funnel snapshot — current counts/values per stage.
    stmt = (
        select(Deal.stage, func.count(Deal.id), func.coalesce(func.sum(Deal.value), 0))
        .where(Deal.pipeline_type == "deal")
        .group_by(Deal.stage)
    )
    if rep_uuid:
        stmt = stmt.where(Deal.assigned_to_id == rep_uuid)
    rows = (await session.execute(stmt)).all()
    by_stage = {r[0]: (r[1], float(r[2] or 0)) for r in rows}
    funnel = [
        StageCount(stage=s, deal_count=by_stage.get(s, (0, 0.0))[0], total_value=by_stage.get(s, (0, 0.0))[1])
        for s in DEAL_STAGES
    ]

    # Conversion grid
    transitions = settings.get("conversion_transitions", [])
    conversion: list[ConversionRow] = []
    for t in transitions:
        row = await pm.stage_conversion(session, p, t["from"], t["to"], rep_uuid)
        conversion.append(ConversionRow(**row))

    # Movement counts within the period
    ordered = {s: i for i, s in enumerate(DEAL_STAGES)}
    hist_stmt = (
        select(DealStageHistory.from_stage, DealStageHistory.to_stage, func.count(DealStageHistory.id))
        .where(
            DealStageHistory.changed_at >= p.start,
            DealStageHistory.changed_at < p.end,
        )
        .group_by(DealStageHistory.from_stage, DealStageHistory.to_stage)
    )
    if rep_uuid:
        hist_stmt = hist_stmt.join(Deal, Deal.id == DealStageHistory.deal_id).where(Deal.assigned_to_id == rep_uuid)
    hist_rows = (await session.execute(hist_stmt)).all()
    advanced = regressed = exited = entered = 0
    exit_stages = {"closed_lost", "not_a_fit", "churned"}
    for fs, ts, cnt in hist_rows:
        if ts in exit_stages:
            exited += cnt
            continue
        if fs is None:
            entered += cnt
            continue
        f_idx = ordered.get(fs, -1)
        t_idx = ordered.get(ts, -1)
        if t_idx > f_idx:
            advanced += cnt
        elif t_idx < f_idx:
            regressed += cnt

    return FunnelResponse(
        period_label=p.label,
        period_start=p.start,
        period_end=p.end,
        funnel=funnel,
        conversion=conversion,
        movement={"advanced": advanced, "regressed": regressed, "exited": exited, "entered": entered},
    )


# ── Deal Health ──────────────────────────────────────────────────────────────


class DealHealthResponse(BaseModel):
    total_stuck: int
    by_stage: dict
    deals: list[AtRiskDeal]


@router.get("/deal-health", response_model=DealHealthResponse)
async def get_deal_health(
    session: DBSession,
    current_user: CurrentUser,
    rep_id: Annotated[Optional[UUID], Query()] = None,
):
    rep = await _resolve_rep(session, current_user, rep_id)
    rep_uuid = rep.id if rep else None
    settings = await get_analytics_settings(session)
    rows = await pm.stuck_deals(session, settings["stuck_thresholds_days"], rep_uuid)
    by_stage: dict[str, int] = {}
    for d in rows:
        by_stage[d["stage"]] = by_stage.get(d["stage"], 0) + 1
    return DealHealthResponse(
        total_stuck=len(rows),
        by_stage=by_stage,
        deals=[AtRiskDeal(**d) for d in rows],
    )


# ── Forecast ─────────────────────────────────────────────────────────────────


class ForecastCategoryBucket(BaseModel):
    category: str  # booked | commit | best | pipeline
    deal_count: int
    acv: float
    weighted_acv: float


class ForecastResponse(BaseModel):
    period_label: str
    quota: Optional[float]
    commit_number: float
    best_case_number: float
    weighted_pipeline: float
    gap_to_quota: Optional[float]
    buckets: list[ForecastCategoryBucket]


@router.get("/forecast", response_model=ForecastResponse)
async def get_forecast(
    session: DBSession,
    current_user: CurrentUser,
    period: Annotated[Literal["month", "quarter"], Query()] = "quarter",
    anchor: Annotated[Optional[date], Query()] = None,
    rep_id: Annotated[Optional[UUID], Query()] = None,
    quota: Annotated[Optional[float], Query()] = None,
):
    from app.models.deal import Deal
    from app.models.deal_stage_history import DealStageHistory
    from sqlalchemy import func, select

    rep = await _resolve_rep(session, current_user, rep_id)
    rep_uuid = rep.id if rep else None
    settings = await get_analytics_settings(session)
    probs = settings.get("stage_probabilities", {})
    p = pm.resolve_period(period, anchor=anchor)

    # Booked (closed_won in period)
    booked_stmt = (
        select(func.count(distinct(DealStageHistory.deal_id)), func.coalesce(func.sum(Deal.value), 0))
        .select_from(DealStageHistory)
        .join(Deal, Deal.id == DealStageHistory.deal_id)
        .where(
            DealStageHistory.to_stage == "closed_won",
            DealStageHistory.changed_at >= p.start,
            DealStageHistory.changed_at < p.end,
        )
    )
    if rep_uuid:
        booked_stmt = booked_stmt.where(Deal.assigned_to_id == rep_uuid)
    booked_count, booked_acv = (await session.execute(booked_stmt)).one()
    booked_acv = float(booked_acv or 0)

    # Open deals expected to close in period.
    open_stmt = select(
        Deal.id, Deal.stage, Deal.value, Deal.commit_to_deal, Deal.close_date_est
    ).where(
        Deal.pipeline_type == "deal",
        Deal.stage.not_in(["closed_won", "closed_lost", "not_a_fit", "churned", "closed"]),
        Deal.close_date_est >= p.start.date(),
        Deal.close_date_est < p.end.date(),
    )
    if rep_uuid:
        open_stmt = open_stmt.where(Deal.assigned_to_id == rep_uuid)
    open_rows = (await session.execute(open_stmt)).all()

    commit_count = commit_acv = 0.0
    best_count = best_acv = 0.0
    pipe_count = pipe_acv = 0.0
    weighted_total = 0.0
    for r in open_rows:
        v = float(r.value or 0)
        prob = probs.get(r.stage, 0.0)
        weighted_total += v * prob
        if r.commit_to_deal:
            commit_count += 1
            commit_acv += v
        elif prob >= 0.5:  # default "best" if prob ≥ 50%
            best_count += 1
            best_acv += v
        else:
            pipe_count += 1
            pipe_acv += v

    commit_number = commit_acv + booked_acv
    best_case_number = commit_number + best_acv

    buckets = [
        ForecastCategoryBucket(category="booked", deal_count=booked_count or 0, acv=booked_acv, weighted_acv=booked_acv),
        ForecastCategoryBucket(category="commit", deal_count=int(commit_count), acv=commit_acv, weighted_acv=commit_acv * 1.0),
        ForecastCategoryBucket(category="best", deal_count=int(best_count), acv=best_acv, weighted_acv=best_acv * 0.5),
        ForecastCategoryBucket(category="pipeline", deal_count=int(pipe_count), acv=pipe_acv, weighted_acv=pipe_acv * 0.15),
    ]

    gap = None
    if quota is not None:
        gap = quota - commit_number

    return ForecastResponse(
        period_label=p.label,
        quota=quota,
        commit_number=commit_number,
        best_case_number=best_case_number,
        weighted_pipeline=booked_acv + weighted_total,
        gap_to_quota=gap,
        buckets=buckets,
    )


# ── Leaderboards ─────────────────────────────────────────────────────────────


class LeaderboardEntry(BaseModel):
    rep_id: str
    rep_name: str
    role: str
    value: float


class LeaderboardResponse(BaseModel):
    metric: str
    period_label: str
    entries: list[LeaderboardEntry]


LEADERBOARD_METRICS = {
    "calls_connected": pm.calls_connected,
    "demos_done": pm.demos_done,
    "pocs_procured": pm.pocs_procured,
    "closed_won": pm.closed_won,
    "win_rate": pm.overall_win_rate,
    "avg_cycle_time_days": pm.avg_cycle_time_days,
}


@router.get("/leaderboards", response_model=LeaderboardResponse)
async def get_leaderboard(
    session: DBSession,
    current_user: CurrentUser,
    metric: Annotated[Literal["calls_connected", "demos_done", "pocs_procured", "closed_won", "win_rate", "avg_cycle_time_days"], Query()] = "calls_connected",
    period: Annotated[Literal["week", "month", "quarter"], Query()] = "month",
    anchor: Annotated[Optional[date], Query()] = None,
):
    p = pm.resolve_period(period, anchor=anchor)
    fn = LEADERBOARD_METRICS[metric]
    stmt = select(User).where(User.is_active == True, User.role.in_(["ae", "sdr"]))  # noqa: E712
    users = (await session.execute(stmt)).scalars().all()

    entries: list[LeaderboardEntry] = []
    for u in users:
        v = await fn(session, u.id, p)
        entries.append(LeaderboardEntry(rep_id=str(u.id), rep_name=u.name, role=u.role, value=float(v)))

    reverse = metric != "avg_cycle_time_days"
    entries.sort(key=lambda e: e.value, reverse=reverse)
    return LeaderboardResponse(metric=metric, period_label=p.label, entries=entries)


# ── Reps list (for admin rep-picker) ─────────────────────────────────────────


class RepSummary(BaseModel):
    id: str
    name: str
    email: str
    role: str


# ── Analytics settings (admin-only) ──────────────────────────────────────────


@router.get("/settings", response_model=dict)
async def read_settings(session: DBSession, current_user: CurrentUser):
    return await get_analytics_settings(session)


@router.put("/settings", response_model=dict)
async def write_settings(
    patch: dict,
    session: DBSession,
    _admin: AdminUser,
):
    return await update_analytics_settings(session, patch)


@router.get("/reps", response_model=list[RepSummary])
async def list_reps(session: DBSession, current_user: CurrentUser):
    stmt = select(User).where(User.is_active == True).order_by(User.name)  # noqa: E712
    rows = (await session.execute(stmt)).scalars().all()
    return [
        RepSummary(id=str(r.id), name=r.name, email=r.email, role=r.role)
        for r in rows
    ]


# ── Pod rosters (for the pod-scoped analytics view) ──────────────────────────


class PodRep(BaseModel):
    id: str
    name: str
    email: str
    role: str
    is_ae: bool


class PodSummary(BaseModel):
    key: str
    label: str
    rep_ids: list[str]      # all members — drives the dashboard's rep filter
    ae_rep_ids: list[str]   # deal-owning AEs — the pipeline/forecast subset
    reps: list[PodRep]


@router.get("/pods", response_model=list[PodSummary])
async def list_pods(session: DBSession, current_user: CurrentUser):
    """Pod rosters resolved to active user ids. Membership and the AE subset are
    declared in app/core/pods.py so the report and this dashboard agree."""
    users = (await session.execute(select(User).where(User.is_active == True))).scalars().all()  # noqa: E712
    by_email = {u.email.lower(): u for u in users if u.email}
    out: list[PodSummary] = []
    for key in pod_keys():
        pod = get_pod(key)
        if not pod:
            continue
        ae_set = set(pod_ae_emails(key))
        reps: list[PodRep] = []
        for r in pod["reps"]:
            u = by_email.get(r["email"].lower())
            if u:
                reps.append(PodRep(id=str(u.id), name=u.name, email=u.email, role=u.role, is_ae=u.email.lower() in ae_set))
        out.append(PodSummary(
            key=pod["key"],
            label=pod["label"],
            rep_ids=[r.id for r in reps],
            ae_rep_ids=[r.id for r in reps if r.is_ae],
            reps=reps,
        ))
    return out
