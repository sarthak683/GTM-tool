"""
Workspace endpoints — single fast aggregate calls for the Sales Workspace UI.

Design decisions:
- All counts come from server-side SQL aggregates (no full-table fetches)
- Alerts are computed on the fly from existing model state (no extra table)
- Stage status infers readiness from data quality, not a stored flag
"""
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from app.core.dependencies import DBSession
from app.models.battlecard import Battlecard
from app.models.company import Company
from app.models.contact import Contact, ContactRead
from app.models.activity import Activity
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting
from app.services.contact_tracking import apply_contact_tracking

router = APIRouter(prefix="/workspace", tags=["workspace"])

# ── Response schemas ─────────────────────────────────────────────────────────

CLOSED_STAGES = {"closed_won", "closed_lost", "not_a_fit", "churned"}
LATE_STAGES = {"poc_done", "commercial_negotiation"}
HOT_PROSPECT_STAGES = {"Meeting Booked", "Interested", "Engaged", "Live Conversation"}
MOTION_PROSPECT_STAGES = HOT_PROSPECT_STAGES | {"In Sequence", "Engaging", "Deal Active"}
PROSPECT_BLOCKER_STAGES = {"Research Needed", "Blocked"}
DEAL_STAGE_GROUPS = [
    {
        "key": "open",
        "label": "Open",
        "stages": {"open"},
        "tone": "blue",
    },
    {
        "key": "qualified",
        "label": "Qualified",
        "stages": {"qualified_lead"},
        "tone": "blue",
    },
    {
        "key": "demo",
        "label": "Demo",
        "stages": {"demo_scheduled", "demo_done"},
        "tone": "green",
    },
    {
        "key": "poc",
        "label": "PoC",
        "stages": {"poc_agreed", "poc_wip", "poc_done"},
        "tone": "green",
    },
    {
        "key": "commercial",
        "label": "Negotiation",
        "stages": {"commercial_negotiation"},
        "tone": "amber",
    },
    {
        "key": "paused",
        "label": "Paused",
        "stages": {"on_hold", "nurture"},
        "tone": "amber",
    },
    {
        "key": "won",
        "label": "Closed Won",
        "stages": {"closed_won"},
        "tone": "green",
    },
    {
        "key": "lost",
        "label": "Closed / Lost",
        "stages": {"closed_lost", "not_a_fit", "churned"},
        "tone": "red",
    },
]
PROSPECT_STAGE_ORDER = [
    "Deal Active",
    "Meeting Booked",
    "Interested",
    "Engaged",
    "Live Conversation",
    "Engaging",
    "In Sequence",
    "Ready",
    "Research Needed",
    "Sequence Complete",
    "Blocked",
    "Customer",
]


class WorkspaceSummary(BaseModel):
    open_deals: int
    total_companies: int
    total_contacts: int
    scheduled_meetings: int
    alerts_count: int


class StageStatus(BaseModel):
    stage: str
    status: str          # ready | needs_action | blocked
    count: int
    blockers: list[str]
    actions: list[str]


class Alert(BaseModel):
    id: str              # deterministic string — no migration needed
    type: str            # stale_deal | no_contacts | no_pre_brief | at_risk | missing_close_date | no_next_steps
    severity: str        # high | medium | low
    title: str
    description: str
    entity_id: Optional[UUID] = None
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None   # deal | company | meeting
    link: Optional[str] = None
    created_at: datetime


class InsightMetric(BaseModel):
    key: str
    label: str
    value: str
    hint: str
    tone: str
    link: Optional[str] = None


class InsightBucket(BaseModel):
    key: str
    label: str
    count: int
    amount: Optional[float] = None
    tone: str


class InsightQueue(BaseModel):
    key: str
    label: str
    count: int
    hint: str
    tone: str
    link: str


class WorkspaceInsights(BaseModel):
    generated_at: datetime
    metrics: list[InsightMetric]
    deal_stage_mix: list[InsightBucket]
    deal_health_mix: list[InsightBucket]
    prospect_stage_mix: list[InsightBucket]
    meeting_readiness_mix: list[InsightBucket]
    focus_queues: list[InsightQueue]
    alerts: list[Alert]


def _format_currency_short(value: float) -> str:
    amount = float(value or 0)
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount:,.0f}"


def _tone_for_prospect_stage(stage: str) -> str:
    if stage in {"Meeting Booked", "Interested", "Engaged", "Live Conversation", "Deal Active", "Customer"}:
        return "green"
    if stage in {"Blocked"}:
        return "red"
    if stage in {"Research Needed", "Sequence Complete"}:
        return "amber"
    return "blue"


async def _load_tracked_contacts(session) -> list[ContactRead]:
    contacts = (await session.execute(select(Contact))).scalars().all()
    tracked = [ContactRead.model_validate(contact) for contact in contacts]
    await apply_contact_tracking(session, tracked)
    return tracked


def _days_since(value: Optional[datetime]) -> Optional[int]:
    if not value:
        return None
    return max((datetime.utcnow() - value).days, 0)


def _non_empty_text(column):
    return and_(column.is_not(None), column != "")


def _empty_text(column):
    return or_(column.is_(None), column == "")


def _deal_stage_group(stage: str) -> dict[str, object]:
    for group in DEAL_STAGE_GROUPS:
        if stage in group["stages"]:
            return group
    return {
        "key": "other",
        "label": "Other",
        "stages": {stage},
        "tone": "blue",
    }


# ── Helper: compute all alerts ───────────────────────────────────────────────

async def _compute_alerts(
    session,
    tracked_contacts: Optional[list[ContactRead]] = None,
) -> list[Alert]:
    alerts: list[Alert] = []
    now = datetime.utcnow()
    alert_ids: set[str] = set()

    def push(alert: Alert) -> None:
        if alert.id in alert_ids:
            return
        alert_ids.add(alert.id)
        alerts.append(alert)

    # 1. Stale deals — stuck in the same stage for > 14 days
    stale_rows = (await session.execute(
        select(Deal.id, Deal.name, Deal.stage, Deal.days_in_stage, Deal.health)
        .where(
            ~Deal.stage.in_(CLOSED_STAGES),
            Deal.days_in_stage > 14,
        )
        .order_by(Deal.days_in_stage.desc())
        .limit(10)
    )).all()

    for row in stale_rows:
        push(Alert(
            id=f"stale_deal_{row.id}",
            type="stale_deal",
            severity="high" if row.days_in_stage > 30 else "medium",
            title=f"Deal stalled: {row.name}",
            description=f"Stuck in '{row.stage}' for {row.days_in_stage} days with no stage movement.",
            entity_id=row.id,
            entity_name=row.name,
            entity_type="deal",
            link=f"/pipeline?deal={row.id}",
            created_at=now,
        ))

    # 2. At-risk deals — health = red
    atrisk_rows = (await session.execute(
        select(Deal.id, Deal.name, Deal.stage)
        .where(Deal.health == "red", ~Deal.stage.in_(CLOSED_STAGES))
        .limit(10)
    )).all()

    for row in atrisk_rows:
        push(Alert(
            id=f"at_risk_{row.id}",
            type="at_risk",
            severity="high",
            title=f"At-risk deal: {row.name}",
            description=f"Deal is flagged red in '{row.stage}' stage. Review and update health score.",
            entity_id=row.id,
            entity_name=row.name,
            entity_type="deal",
            link=f"/pipeline?deal={row.id}",
            created_at=now,
        ))

    # 3. Late-stage deals missing close date
    no_close_rows = (await session.execute(
        select(Deal.id, Deal.name, Deal.stage)
        .where(
            Deal.stage.in_(LATE_STAGES),
            Deal.close_date_est.is_(None),
        )
        .limit(10)
    )).all()

    for row in no_close_rows:
        push(Alert(
            id=f"missing_close_{row.id}",
            type="missing_close_date",
            severity="medium",
            title=f"No close date: {row.name}",
            description=f"Deal is in '{row.stage}' but has no estimated close date. Forecast is blind.",
            entity_id=row.id,
            entity_name=row.name,
            entity_type="deal",
            link=f"/pipeline?deal={row.id}",
            created_at=now,
        ))

    # 4. Open deals with no next step
    no_next_step_rows = (await session.execute(
        select(Deal.id, Deal.name, Deal.stage)
        .where(
            ~Deal.stage.in_(CLOSED_STAGES),
            _empty_text(Deal.next_step),
        )
        .order_by(Deal.updated_at.asc())
        .limit(10)
    )).all()

    for row in no_next_step_rows:
        push(Alert(
            id=f"deal_no_next_step_{row.id}",
            type="deal_no_next_step",
            severity="medium",
            title=f"No next step: {row.name}",
            description=f"Deal is active in '{row.stage}' but no next step is logged.",
            entity_id=row.id,
            entity_name=row.name,
            entity_type="deal",
            link=f"/pipeline?deal={row.id}",
            created_at=now,
        ))

    # 5. Companies with active deals but zero contacts
    contacted_subq = select(Contact.company_id).where(
        Contact.company_id.is_not(None)
    ).distinct()

    active_company_subq = select(Deal.company_id).where(
        ~Deal.stage.in_(CLOSED_STAGES),
        Deal.company_id.is_not(None),
    ).distinct()

    no_contact_rows = (await session.execute(
        select(Company.id, Company.name)
        .where(
            Company.id.in_(active_company_subq),
            Company.id.notin_(contacted_subq),
        )
        .limit(10)
    )).all()

    for row in no_contact_rows:
        push(Alert(
            id=f"no_contacts_{row.id}",
            type="no_contacts",
            severity="medium",
            title=f"No contacts: {row.name}",
            description="This account has an active deal but no contacts found. Run Hunter contact discovery.",
            entity_id=row.id,
            entity_name=row.name,
            entity_type="company",
            link=f"/companies/{row.id}",
            created_at=now,
        ))

    # 6. Scheduled meetings within 48h with no pre-brief
    in_48h = now + timedelta(hours=48)
    no_brief_rows = (await session.execute(
        select(Meeting.id, Meeting.title, Meeting.scheduled_at)
        .where(
            Meeting.status == "scheduled",
            Meeting.scheduled_at.is_not(None),
            Meeting.scheduled_at >= now,
            Meeting.scheduled_at <= in_48h,
            _empty_text(Meeting.pre_brief),
        )
        .limit(10)
    )).all()

    for row in no_brief_rows:
        push(Alert(
            id=f"no_brief_{row.id}",
            type="no_pre_brief",
            severity="high",
            title=f"No pre-brief: {row.title}",
            description="Meeting is within 48 hours but no pre-meeting brief has been generated.",
            entity_id=row.id,
            entity_name=row.title,
            entity_type="meeting",
            link=f"/meetings/{row.id}",
            created_at=now,
        ))

    # 7. Completed meetings with no next steps logged
    no_nextsteps_rows = (await session.execute(
        select(Meeting.id, Meeting.title)
        .where(
            Meeting.status == "completed",
            _empty_text(Meeting.next_steps),
        )
        .limit(10)
    )).all()

    for row in no_nextsteps_rows:
        push(Alert(
            id=f"no_next_steps_{row.id}",
            type="no_next_steps",
            severity="low",
            title=f"No next steps: {row.title}",
            description="Meeting is marked completed but no next steps were logged. Update the debrief.",
            entity_id=row.id,
            entity_name=row.title,
            entity_type="meeting",
            link=f"/meetings/{row.id}",
            created_at=now,
        ))

    if tracked_contacts is None:
        tracked_contacts = await _load_tracked_contacts(session)

    hot_contacts = sorted(
        [contact for contact in tracked_contacts if (contact.tracking_stage or "") in HOT_PROSPECT_STAGES],
        key=lambda contact: (
            contact.tracking_score or 0,
            contact.tracking_last_activity_at or contact.updated_at,
        ),
        reverse=True,
    )[:6]

    for contact in hot_contacts:
        full_name = f"{contact.first_name} {contact.last_name}".strip()
        push(Alert(
            id=f"hot_prospect_{contact.id}",
            type="hot_prospect",
            severity="high" if contact.tracking_stage == "Meeting Booked" else "medium",
            title=f"Follow up now: {full_name}",
            description=contact.tracking_summary or "Prospect momentum is positive and should move quickly.",
            entity_id=contact.id,
            entity_name=full_name,
            entity_type="contact",
            link=f"/contacts/{contact.id}",
            created_at=contact.tracking_last_activity_at or contact.updated_at,
        ))

    cooling_contacts = sorted(
        [
            contact
            for contact in tracked_contacts
            if (contact.tracking_stage or "") in {"In Sequence", "Engaging"}
            and (_days_since(contact.tracking_last_activity_at) or 0) >= 7
        ],
        key=lambda contact: _days_since(contact.tracking_last_activity_at) or 0,
        reverse=True,
    )[:6]

    for contact in cooling_contacts:
        stale_days = _days_since(contact.tracking_last_activity_at) or 0
        full_name = f"{contact.first_name} {contact.last_name}".strip()
        push(Alert(
            id=f"cooling_sequence_{contact.id}",
            type="cooling_sequence",
            severity="medium",
            title=f"Sequence cooling: {full_name}",
            description=f"No fresh engagement signal in {stale_days} days while outreach is still running.",
            entity_id=contact.id,
            entity_name=full_name,
            entity_type="contact",
            link=f"/contacts/{contact.id}",
            created_at=contact.tracking_last_activity_at or contact.updated_at,
        ))

    blocked_contacts = sorted(
        [contact for contact in tracked_contacts if (contact.tracking_stage or "") in PROSPECT_BLOCKER_STAGES],
        key=lambda contact: (
            contact.tracking_score or 0,
            contact.updated_at,
        ),
    )[:6]

    for contact in blocked_contacts:
        full_name = f"{contact.first_name} {contact.last_name}".strip()
        push(Alert(
            id=f"research_blocker_{contact.id}",
            type="research_blocker",
            severity="high" if contact.tracking_stage == "Blocked" else "medium",
            title=f"Outreach blocked: {full_name}",
            description=contact.tracking_summary or "Contact data or buyer signal is blocking progress.",
            entity_id=contact.id,
            entity_name=full_name,
            entity_type="contact",
            link=f"/contacts/{contact.id}",
            created_at=contact.tracking_last_activity_at or contact.updated_at,
        ))

    # Sort: high → medium → low
    severity_order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda alert: (severity_order[alert.severity], -(alert.created_at.timestamp() if alert.created_at else now.timestamp())))
    return alerts[:18]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=WorkspaceSummary)
async def workspace_summary(session: DBSession):
    """
    Single fast aggregate response for the Sales Workspace landing page.
    Replaces 4 separate list() API calls in the frontend.
    """
    open_deals = (await session.execute(
        select(func.count(Deal.id)).where(~Deal.stage.in_(CLOSED_STAGES))
    )).scalar_one()

    total_companies = (await session.execute(
        select(func.count(Company.id))
    )).scalar_one()

    total_contacts = (await session.execute(
        select(func.count(Contact.id))
    )).scalar_one()

    scheduled_meetings = (await session.execute(
        select(func.count(Meeting.id)).where(Meeting.status == "scheduled")
    )).scalar_one()

    alerts = await _compute_alerts(session)

    return WorkspaceSummary(
        open_deals=open_deals,
        total_companies=total_companies,
        total_contacts=total_contacts,
        scheduled_meetings=scheduled_meetings,
        alerts_count=len(alerts),
    )


@router.get("/alerts", response_model=list[Alert])
async def workspace_alerts(session: DBSession):
    """
    CRM alerts feed — computed from existing model state, no stored alerts table.
    Covers: stale deals, at-risk deals, missing close dates, contactless accounts,
            upcoming meetings with no brief, completed meetings with no next steps.
    """
    return await _compute_alerts(session)


@router.get("/insights", response_model=WorkspaceInsights)
async def workspace_insights(session: DBSession):
    now = datetime.utcnow()
    tracked_contacts = await _load_tracked_contacts(session)
    alerts = await _compute_alerts(session, tracked_contacts=tracked_contacts)

    deal_rows = (await session.execute(
        select(
            Deal.id,
            Deal.name,
            Deal.stage,
            Deal.health,
            Deal.value,
            Deal.days_in_stage,
            Deal.next_step,
        )
    )).all()

    open_deal_rows = [row for row in deal_rows if row.stage not in CLOSED_STAGES]
    open_pipeline_value = sum(float(row.value or 0) for row in open_deal_rows)
    deal_rescue_count = sum(
        1
        for row in open_deal_rows
        if row.health == "red" or row.days_in_stage > 14 or not row.next_step
    )

    deal_stage_counts: Counter[str] = Counter()
    deal_stage_amounts: Counter[str] = Counter()
    for row in deal_rows:
        group = _deal_stage_group(row.stage)
        deal_stage_counts[group["key"]] += 1
        deal_stage_amounts[group["key"]] += float(row.value or 0)

    deal_stage_mix = [
        InsightBucket(
            key=group["key"],
            label=group["label"],
            count=deal_stage_counts.get(group["key"], 0),
            amount=deal_stage_amounts.get(group["key"], 0.0),
            tone=group["tone"],
        )
        for group in DEAL_STAGE_GROUPS
        if deal_stage_counts.get(group["key"], 0) > 0
    ]

    health_rows = (await session.execute(
        select(
            Deal.health,
            func.count(Deal.id),
            func.coalesce(func.sum(Deal.value), 0),
        )
        .where(~Deal.stage.in_(CLOSED_STAGES))
        .group_by(Deal.health)
    )).all()

    health_meta = {
        "green": ("Healthy", "green"),
        "yellow": ("Watch", "amber"),
        "red": ("Rescue", "red"),
    }
    health_map = {
        str(health): (count, float(amount or 0))
        for health, count, amount in health_rows
    }
    deal_health_mix = [
        InsightBucket(
            key=key,
            label=label,
            count=health_map.get(key, (0, 0.0))[0],
            amount=health_map.get(key, (0, 0.0))[1],
            tone=tone,
        )
        for key, (label, tone) in health_meta.items()
        if health_map.get(key, (0, 0.0))[0] > 0
    ]

    prospect_counts = Counter(contact.tracking_stage or "Ready" for contact in tracked_contacts)
    prospect_stage_mix = [
        InsightBucket(
            key=stage.lower().replace(" ", "_"),
            label=stage,
            count=prospect_counts.get(stage, 0),
            amount=None,
            tone=_tone_for_prospect_stage(stage),
        )
        for stage in PROSPECT_STAGE_ORDER
        if prospect_counts.get(stage, 0) > 0
    ]

    prospects_in_motion = sum(
        1 for contact in tracked_contacts if (contact.tracking_stage or "") in MOTION_PROSPECT_STAGES
    )
    hot_follow_up_count = sum(
        1 for contact in tracked_contacts if (contact.tracking_stage or "") in HOT_PROSPECT_STAGES
    )
    research_blocker_count = sum(
        1 for contact in tracked_contacts if (contact.tracking_stage or "") in PROSPECT_BLOCKER_STAGES
    )
    cooling_sequence_count = sum(
        1
        for contact in tracked_contacts
        if (contact.tracking_stage or "") in {"In Sequence", "Engaging"}
        and (_days_since(contact.tracking_last_activity_at) or 0) >= 7
    )

    ready_meetings = (await session.execute(
        select(func.count(Meeting.id))
        .where(Meeting.status == "scheduled", _non_empty_text(Meeting.pre_brief))
    )).scalar_one()
    prep_missing_meetings = (await session.execute(
        select(func.count(Meeting.id))
        .where(Meeting.status == "scheduled", _empty_text(Meeting.pre_brief))
    )).scalar_one()
    followup_logged_meetings = (await session.execute(
        select(func.count(Meeting.id))
        .where(Meeting.status == "completed", _non_empty_text(Meeting.next_steps))
    )).scalar_one()
    followup_missing_meetings = (await session.execute(
        select(func.count(Meeting.id))
        .where(Meeting.status == "completed", _empty_text(Meeting.next_steps))
    )).scalar_one()

    meeting_readiness_mix = [
        InsightBucket(
            key="meeting_ready",
            label="Ready",
            count=ready_meetings,
            amount=None,
            tone="green",
        ),
        InsightBucket(
            key="meeting_prep_gap",
            label="Prep Missing",
            count=prep_missing_meetings,
            amount=None,
            tone="amber",
        ),
        InsightBucket(
            key="meeting_followup_logged",
            label="Follow-up Logged",
            count=followup_logged_meetings,
            amount=None,
            tone="blue",
        ),
        InsightBucket(
            key="meeting_followup_gap",
            label="Follow-up Missing",
            count=followup_missing_meetings,
            amount=None,
            tone="red",
        ),
    ]
    meeting_readiness_mix = [bucket for bucket in meeting_readiness_mix if bucket.count > 0]

    no_contact_accounts = (await session.execute(
        select(func.count(Company.id))
        .where(
            Company.id.in_(
                select(Deal.company_id)
                .where(~Deal.stage.in_(CLOSED_STAGES), Deal.company_id.is_not(None))
                .distinct()
            ),
            Company.id.notin_(
                select(Contact.company_id)
                .where(Contact.company_id.is_not(None))
                .distinct()
            ),
        )
    )).scalar_one()

    meeting_prep_gap_count = (await session.execute(
        select(func.count(Meeting.id))
        .where(
            Meeting.status == "scheduled",
            Meeting.scheduled_at.is_not(None),
            Meeting.scheduled_at >= now,
            Meeting.scheduled_at <= now + timedelta(hours=48),
            _empty_text(Meeting.pre_brief),
        )
    )).scalar_one()

    metrics = [
        InsightMetric(
            key="open_pipeline",
            label="Open Pipeline",
            value=_format_currency_short(open_pipeline_value),
            hint=f"{len(open_deal_rows)} open deals are currently in play.",
            tone="blue",
            link="/pipeline",
        ),
        InsightMetric(
            key="deal_rescue",
            label="Deal Rescue Queue",
            value=str(deal_rescue_count),
            hint="Deals with red health, staleness, or no next step.",
            tone="red" if deal_rescue_count else "green",
            link="/pipeline",
        ),
        InsightMetric(
            key="prospects_in_motion",
            label="Prospects In Motion",
            value=str(prospects_in_motion),
            hint="Live sequence, reply, live call, or deal momentum is active.",
            tone="green" if prospects_in_motion else "blue",
            link="/prospecting",
        ),
        InsightMetric(
            key="hot_follow_up",
            label="Hot Follow-up",
            value=str(hot_follow_up_count),
            hint="Positive buyer signals that deserve fast rep action.",
            tone="amber" if hot_follow_up_count else "blue",
            link="/prospecting",
        ),
        InsightMetric(
            key="coverage_gaps",
            label="Coverage Gaps",
            value=str(no_contact_accounts + research_blocker_count),
            hint="Accounts or prospects still blocked by missing coverage.",
            tone="red" if (no_contact_accounts + research_blocker_count) else "green",
            link="/account-sourcing",
        ),
        InsightMetric(
            key="meeting_gaps",
            label="Meeting Gaps",
            value=str(meeting_prep_gap_count + followup_missing_meetings),
            hint="Upcoming prep gaps plus completed calls with no next steps.",
            tone="red" if (meeting_prep_gap_count + followup_missing_meetings) else "green",
            link="/meetings",
        ),
    ]

    focus_queues = [
        InsightQueue(
            key="deal_rescue",
            label="Deal Rescue",
            count=deal_rescue_count,
            hint="Red, stale, or next-step-light deals need attention.",
            tone="red" if deal_rescue_count else "green",
            link="/pipeline",
        ),
        InsightQueue(
            key="follow_up_now",
            label="Follow Up Now",
            count=hot_follow_up_count,
            hint="Replies, interest, calls, or meetings are already showing traction.",
            tone="amber" if hot_follow_up_count else "blue",
            link="/prospecting",
        ),
        InsightQueue(
            key="cooling_sequences",
            label="Cooling Sequences",
            count=cooling_sequence_count,
            hint="Live outreach has gone quiet for at least a week.",
            tone="amber" if cooling_sequence_count else "green",
            link="/prospecting",
        ),
        InsightQueue(
            key="research_blockers",
            label="Research Blockers",
            count=research_blocker_count,
            hint="Prospects still need usable data or are blocked by a hard signal.",
            tone="red" if research_blocker_count else "green",
            link="/prospecting",
        ),
        InsightQueue(
            key="coverage_gaps",
            label="Stakeholder Coverage",
            count=no_contact_accounts,
            hint="Active-deal accounts still have no stakeholder coverage.",
            tone="red" if no_contact_accounts else "green",
            link="/account-sourcing",
        ),
        InsightQueue(
            key="meeting_prep",
            label="Meeting Prep Gaps",
            count=meeting_prep_gap_count,
            hint="Upcoming meetings inside 48 hours still need a brief.",
            tone="amber" if meeting_prep_gap_count else "green",
            link="/pre-meeting-assistance",
        ),
        InsightQueue(
            key="post_meeting_followup",
            label="Post-Meeting Follow-up",
            count=followup_missing_meetings,
            hint="Completed meetings need next steps before momentum fades.",
            tone="red" if followup_missing_meetings else "green",
            link="/meetings",
        ),
    ]

    return WorkspaceInsights(
        generated_at=now,
        metrics=metrics,
        deal_stage_mix=deal_stage_mix,
        deal_health_mix=deal_health_mix,
        prospect_stage_mix=prospect_stage_mix,
        meeting_readiness_mix=meeting_readiness_mix,
        focus_queues=focus_queues,
        alerts=alerts,
    )


@router.get("/stages/{stage}", response_model=StageStatus)
async def stage_status(stage: str, session: DBSession):
    """
    Per-stage health check — returns ready | needs_action | blocked
    plus specific blockers and suggested actions.
    """
    now = datetime.utcnow()

    if stage == "account-sourcing":
        total = (await session.execute(select(func.count(Company.id)))).scalar_one()
        unenriched = (await session.execute(
            select(func.count(Company.id)).where(Company.enriched_at.is_(None))
        )).scalar_one()

        blockers, actions = [], []
        if total == 0:
            blockers.append("No accounts imported yet")
            actions.append("Upload a CSV in Account Sourcing")
            status = "blocked"
        elif unenriched > 0:
            blockers.append(f"{unenriched} account(s) not yet enriched")
            actions.append("Run bulk enrichment from the Accounts page")
            status = "needs_action"
        else:
            status = "ready"

        return StageStatus(stage=stage, status=status, count=total,
                           blockers=blockers, actions=actions)

    if stage == "prospecting":
        total_contacts = (await session.execute(select(func.count(Contact.id)))).scalar_one()

        contacted_ids = select(Contact.company_id).where(
            Contact.company_id.is_not(None)
        ).distinct().scalar_subquery()

        companies_with_deals = (await session.execute(
            select(func.count(Deal.company_id.distinct()))
            .where(~Deal.stage.in_(CLOSED_STAGES), Deal.company_id.is_not(None))
        )).scalar_one()

        companies_no_contacts = (await session.execute(
            select(func.count(Company.id))
            .where(Company.id.notin_(contacted_ids))
        )).scalar_one()

        blockers, actions = [], []
        if total_contacts == 0:
            blockers.append("No contacts discovered yet")
            actions.append("Use Hunter contact discovery on accounts")
            status = "blocked"
        elif companies_no_contacts > 0:
            blockers.append(f"{companies_no_contacts} account(s) have no contacts")
            actions.append("Run 'Find Contacts' on those accounts")
            status = "needs_action"
        else:
            status = "ready"

        return StageStatus(stage=stage, status=status, count=total_contacts,
                           blockers=blockers, actions=actions)

    if stage == "pre-meeting":
        total_meetings = (await session.execute(
            select(func.count(Meeting.id)).where(Meeting.status == "scheduled")
        )).scalar_one()

        no_brief = (await session.execute(
            select(func.count(Meeting.id))
            .where(Meeting.status == "scheduled", Meeting.pre_brief.is_(None))
        )).scalar_one()

        blockers, actions = [], []
        if total_meetings == 0:
            blockers.append("No meetings scheduled yet")
            actions.append("Schedule a meeting from a deal in demo stage")
            status = "blocked"
        elif no_brief > 0:
            blockers.append(f"{no_brief} scheduled meeting(s) missing a pre-brief")
            actions.append("Open each meeting and run 'Run Web Intel'")
            status = "needs_action"
        else:
            status = "ready"

        return StageStatus(stage=stage, status=status, count=total_meetings,
                           blockers=blockers, actions=actions)

    if stage == "custom-demo":
        demo_meetings = (await session.execute(
            select(func.count(Meeting.id))
            .where(Meeting.meeting_type.in_(["demo", "poc"]))
        )).scalar_one()

        no_strategy = (await session.execute(
            select(func.count(Meeting.id))
            .where(
                Meeting.meeting_type.in_(["demo", "poc"]),
                Meeting.demo_strategy.is_(None),
            )
        )).scalar_one()

        blockers, actions = [], []
        if demo_meetings == 0:
            blockers.append("No demo or PoC meetings scheduled")
            actions.append("Move a deal to Demo stage and schedule a meeting")
            status = "blocked"
        elif no_strategy > 0:
            blockers.append(f"{no_strategy} demo meeting(s) have no AI demo strategy")
            actions.append("Open each meeting and run the intel pipeline")
            status = "needs_action"
        else:
            status = "ready"

        return StageStatus(stage=stage, status=status, count=demo_meetings,
                           blockers=blockers, actions=actions)

    if stage == "live-meeting":
        bc_count = (await session.execute(
            select(func.count(Battlecard.id)).where(Battlecard.is_active == True)
        )).scalar_one()

        blockers, actions = [], []
        if bc_count == 0:
            blockers.append("No battlecards loaded")
            actions.append("Seed default Beacon battlecards or create custom ones")
            status = "blocked"
        elif bc_count < 5:
            blockers.append(f"Only {bc_count} battlecard(s) — low coverage")
            actions.append("Add more objection and competitor battlecards")
            status = "needs_action"
        else:
            status = "ready"

        return StageStatus(stage=stage, status=status, count=bc_count,
                           blockers=blockers, actions=actions)

    # crm-insights-alerts / sales-workspace / unknown
    return StageStatus(
        stage=stage,
        status="ready",
        count=1,
        blockers=[],
        actions=[],
    )
