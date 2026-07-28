from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.deal import Deal

# How long to skip the milestone backfill after a successful in-process run.
# analytics.py calls backfill_company_stage_milestones() on every dashboard /
# funnel request, but the backfill is an idempotent catch-up that scans every
# stage_change activity + every milestone-stage deal — far too heavy to run per
# request. Throttle here (we can't edit analytics.py) so it runs at most once
# every few hours per process. Per-process granularity is acceptable: with a
# couple of replicas the worst case is one backfill per replica per window, and
# the work is idempotent so nothing is double-created.
_BACKFILL_MIN_INTERVAL = timedelta(hours=6)
# Wall-clock time of the last completed backfill in this process. Module-level
# so it survives across requests within a worker/replica.
_last_backfill_at: datetime | None = None

MILESTONE_STAGE_MAP: dict[str, str] = {
    "demo_scheduled": "demo_scheduled",
    "qualified_lead": "qualified_lead",
    "demo_done": "demo_done",
    "poc_agreed": "poc_agreed",
    "poc_wip": "poc_wip",
    "poc_done": "poc_done",
    "commercial_negotiation": "commercial_negotiation",
    "workshop": "workshop_msa",
    "msa_review": "workshop_msa",
    "closed_won": "closed_won",
}

MILESTONE_LABELS: dict[str, str] = {
    "demo_scheduled": "Demo Scheduled",
    "qualified_lead": "Converted",
    "demo_done": "Demo Done",
    "poc_agreed": "POC Agreed",
    "poc_wip": "POC WIP",
    "poc_done": "POC Done",
    "commercial_negotiation": "Commercial Negotiation",
    "workshop_msa": "Workshop / MSA",
    "closed_won": "Closed Won",
}

_STAGE_CHANGE_RE = re.compile(r"Stage moved from (?P<old>[a-z_]+) to (?P<new>[a-z_]+)")


def stage_to_milestone_key(stage: str | None) -> str | None:
    if not stage:
        return None
    return MILESTONE_STAGE_MAP.get(str(stage).strip().lower())


async def record_deal_stage_milestone(
    session: AsyncSession,
    *,
    deal: Deal,
    stage: str | None = None,
    reached_at: datetime | None = None,
    source: str | None = None,
    source_activity_id: UUID | None = None,
) -> CompanyStageMilestone | None:
    milestone_key = stage_to_milestone_key(stage or deal.stage)
    if not milestone_key or not deal.company_id:
        return None

    existing = (
        await session.execute(
            select(CompanyStageMilestone).where(
                CompanyStageMilestone.company_id == deal.company_id,
                CompanyStageMilestone.milestone_key == milestone_key,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    milestone = CompanyStageMilestone(
        company_id=deal.company_id,
        deal_id=deal.id,
        source_activity_id=source_activity_id,
        milestone_key=milestone_key,
        first_reached_at=reached_at or deal.stage_entered_at or deal.updated_at or deal.created_at or datetime.utcnow(),
        source=source,
        updated_at=datetime.utcnow(),
    )
    session.add(milestone)
    return milestone


def _parse_new_stage_from_activity(activity: Activity) -> str | None:
    if activity.type == "stage_change":
        match = _STAGE_CHANGE_RE.search(activity.content or "")
        if match:
            return match.group("new")
    return None


async def backfill_company_stage_milestones(session: AsyncSession) -> int:
    # Throttle: if this process ran the backfill less than _BACKFILL_MIN_INTERVAL
    # ago, skip the heavy scan and report no new rows. The backfill is idempotent
    # catch-up, so skipping a request-triggered run only defers work the next
    # uncached call (or any milestone write path) will pick up.
    global _last_backfill_at
    now = datetime.utcnow()
    if _last_backfill_at is not None and (now - _last_backfill_at) < _BACKFILL_MIN_INTERVAL:
        return 0

    existing_pairs = {
        (row.company_id, row.milestone_key)
        for row in (
            await session.execute(
                select(CompanyStageMilestone.company_id, CompanyStageMilestone.milestone_key)
            )
        ).all()
    }
    created = 0

    activity_rows = (
        await session.execute(
            select(Activity, Deal)
            .join(Deal, Activity.deal_id == Deal.id)
            .where(
                Activity.type == "stage_change",
                Deal.company_id.is_not(None),
            )
            .order_by(Activity.created_at.asc())
        )
    ).all()

    deals_by_id: dict[UUID, Deal] = {}
    for activity, deal in activity_rows:
        if not deal.id:
            continue
        deals_by_id[deal.id] = deal
        stage = _parse_new_stage_from_activity(activity)
        milestone_key = stage_to_milestone_key(stage)
        pair = (deal.company_id, milestone_key)
        if not milestone_key or pair in existing_pairs:
            continue
        session.add(
            CompanyStageMilestone(
                company_id=deal.company_id,
                deal_id=deal.id,
                source_activity_id=activity.id,
                milestone_key=milestone_key,
                first_reached_at=activity.created_at,
                source="activity_backfill",
                updated_at=datetime.utcnow(),
            )
        )
        existing_pairs.add(pair)
        created += 1

    deal_rows = (
        await session.execute(
            select(Deal).where(
                Deal.company_id.is_not(None),
                Deal.stage.in_(list(MILESTONE_STAGE_MAP.keys())),
            )
        )
    ).scalars().all()

    for deal in deal_rows:
        if not deal.id or not deal.company_id:
            continue
        milestone_key = stage_to_milestone_key(deal.stage)
        pair = (deal.company_id, milestone_key)
        if not milestone_key or pair in existing_pairs:
            continue
        session.add(
            CompanyStageMilestone(
                company_id=deal.company_id,
                deal_id=deal.id,
                milestone_key=milestone_key,
                first_reached_at=deal.stage_entered_at or deal.updated_at or deal.created_at or datetime.utcnow(),
                source="current_state_backfill",
                updated_at=datetime.utcnow(),
            )
        )
        existing_pairs.add(pair)
        created += 1

    if created:
        await session.commit()
    # Mark a successful completion so the next few hours of requests skip the
    # scan. Recorded only after the work runs, so a failed scan (which raises
    # before here) doesn't suppress the retry.
    _last_backfill_at = now
    return created
