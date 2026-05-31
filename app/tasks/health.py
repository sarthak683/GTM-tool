"""
Celery Beat task: recalculate deal health daily for all active deals.

Runs every 24 hours via the beat_schedule in celery_app.py.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_
from sqlmodel import select

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# Closed/terminal stages that should NOT be health-checked.
_CLOSED_STAGES = frozenset([
    "closed_won", "closed_lost", "not_a_fit", "churned",
])
# Batch size × cron cadence caps reconcile throughput. With 12/hour, ceiling
# was 288 deals/day — well below active deal count in prod, which is why p95
# task-refresh lag measured ~39h. Bumped to 40 per run, paired with a 15-min
# cron cadence (see celery_app.py beat_schedule) for ~3,840 deals/day. The
# per-deal `should_queue_deal_task_refresh` gate (TTL + input-hash) prevents
# unchanged deals from re-running the AI pipeline.
DEAL_TASK_RECONCILE_BATCH_SIZE = 40
DEAL_TASK_RECONCILE_LOOKBACK_DAYS = 30


@celery_app.task(name="app.tasks.health.recalculate_all_deal_health")
def recalculate_all_deal_health() -> dict:
    """Recalculate health score for every active deal."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        count = loop.run_until_complete(_async_recalculate())
    finally:
        loop.close()
    return {"status": "completed", "deals_updated": count}


async def _async_recalculate() -> int:
    from app.database import task_session
    from app.models.activity import Activity
    from app.models.deal import Deal
    from app.services.deal_health import compute_health
    from app.services.deal_linker import reconcile_deal_stakeholders

    updated = 0
    contacts_linked = 0
    contacts_created = 0
    async with task_session() as session:
        result = await session.execute(
            select(Deal).where(Deal.stage.notin_(_CLOSED_STAGES))
        )
        deals = result.scalars().all()

        for deal in deals:
            # Recompute days_in_stage from stage_entered_at
            if deal.stage_entered_at:
                deal.days_in_stage = (datetime.utcnow() - deal.stage_entered_at).days
            elif deal.created_at:
                deal.days_in_stage = (datetime.utcnow() - deal.created_at).days

            acts_result = await session.execute(
                select(Activity).where(Activity.deal_id == deal.id)
            )
            activities = acts_result.scalars().all()

            score, health = compute_health(deal, activities)
            deal.health_score = score
            deal.health = health
            session.add(deal)
            updated += 1

            # Reconcile stakeholders from the deal's own account + meetings +
            # emails so the AE workflow + MEDDPICC have people to work with.
            try:
                res = await reconcile_deal_stakeholders(session, deal)
                contacts_linked += res["linked"]
                contacts_created += res["created"]
            except Exception:
                logger.exception("health: stakeholder reconcile failed for deal %s", deal.id)

        await session.commit()

    logger.info(
        "Health recalculated for %d deals; linked %d stakeholders, created %d new contacts",
        updated, contacts_linked, contacts_created,
    )
    return updated


@celery_app.task(name="app.tasks.health.reconcile_recent_deal_tasks")
def reconcile_recent_deal_tasks() -> dict:
    """Refresh a bounded batch of recently active deal tasks so stale system tasks self-heal."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        refreshed = loop.run_until_complete(_async_reconcile_recent_deal_tasks())
    finally:
        loop.close()
    return {"status": "completed", "deals_refreshed": refreshed}


async def _async_reconcile_recent_deal_tasks() -> int:
    from app.database import task_session
    from app.models.deal import Deal
    from app.models.task import Task
    from app.services.tasks import backfill_open_task_assignments, refresh_system_tasks_for_entity

    refreshed = 0
    now = datetime.utcnow()
    lookback_start = now - timedelta(days=DEAL_TASK_RECONCILE_LOOKBACK_DAYS)

    async with task_session() as session:
        candidate_ids = (
            await session.execute(
                select(Deal.id)
                .join(
                    Task,
                    and_(
                        Task.entity_type == "deal",
                        Task.entity_id == Deal.id,
                    ),
                )
                .where(
                    Deal.stage.notin_(_CLOSED_STAGES),
                    Task.task_type == "system",
                    Task.status == "open",
                    Task.system_key.like("deal_%"),
                    or_(
                        Deal.ai_tasks_refreshed_at.is_(None),
                        Deal.ai_tasks_refreshed_at <= now - timedelta(hours=1),
                    ),
                    or_(
                        Deal.last_activity_at.is_(None),
                        Deal.last_activity_at >= lookback_start,
                        Deal.updated_at >= lookback_start,
                    ),
                )
                .group_by(Deal.id)
                .order_by(
                    func.max(func.coalesce(Deal.last_activity_at, Deal.updated_at)).desc(),
                    func.max(func.coalesce(Deal.ai_tasks_refreshed_at, Deal.created_at)).asc(),
                )
                .limit(DEAL_TASK_RECONCILE_BATCH_SIZE)
            )
        ).scalars().all()

        for deal_id in candidate_ids:
            try:
                await refresh_system_tasks_for_entity(session, "deal", deal_id)
                await session.commit()
                refreshed += 1
            except Exception as exc:
                logger.warning("Deal task reconciliation failed for deal %s: %s", deal_id, exc)
                await session.rollback()
                continue

        if refreshed:
            await backfill_open_task_assignments(session)
            await session.commit()

    logger.info("Reconciled deal tasks for %d deals", refreshed)
    return refreshed
