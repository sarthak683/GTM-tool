from datetime import datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import logging

from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy import func, or_, select

from app.core.dependencies import CurrentUser, DBSession, Pagination
from app.models.activity import Activity, ActivityCreate, ActivityRead, ActivityUpdate
from app.models.deal import Deal
from app.models.meeting import Meeting
from app.repositories.activity import ActivityRepository
from app.schemas.common import PaginatedResponse
from app.database import AsyncSessionLocal
from app.services.tasks import backfill_open_task_assignments, refresh_system_tasks_for_entity

router = APIRouter(prefix="/activities", tags=["activities"])
logger = logging.getLogger(__name__)
REPORT_CUTOFF_TIMEZONE = ZoneInfo("Asia/Kolkata")
REPORT_CUTOFF_HOUR = 6


async def _refresh_tasks_after_activity_background(deal_id: UUID | None, contact_id: UUID | None) -> None:
    async with AsyncSessionLocal() as session:
        try:
            if contact_id:
                await refresh_system_tasks_for_entity(session, "contact", contact_id)
            if deal_id:
                await refresh_system_tasks_for_entity(session, "deal", deal_id)
            await backfill_open_task_assignments(session)
            await session.commit()
        except Exception as exc:  # pragma: no cover - background safety net
            logger.warning("activity-triggered task refresh failed for deal=%s contact=%s: %s", deal_id, contact_id, exc)
            await session.rollback()


@router.get("/", response_model=PaginatedResponse[ActivityRead])
async def list_activities(
    session: DBSession,
    pagination: Pagination,
    current_user: CurrentUser,
    deal_id: Optional[UUID] = Query(default=None),
    contact_id: Optional[UUID] = Query(default=None),
    company_id: Optional[UUID] = Query(default=None),
    type: Optional[str] = Query(default=None),
    created_by_id: Optional[UUID] = Query(default=None),
    created_by_me: bool = Query(default=False, description="Shortcut for created_by_id=current_user.id"),
    since: Optional[str] = Query(default=None, description="ISO datetime — only activities at or after this moment"),
):
    repo = ActivityRepository(session)
    filters = []
    if deal_id:
        filters.append(Activity.deal_id == deal_id)
    if contact_id:
        filters.append(Activity.contact_id == contact_id)
    if company_id:
        # Activities linked via deals belonging to this company,
        # or via meetings mapped to this company
        company_deal_ids = select(Deal.id).where(Deal.company_id == company_id)
        meeting_ext_ids = select(
            ("tldv:meeting:" + Meeting.external_source_id)
        ).where(Meeting.company_id == company_id, Meeting.external_source_id.isnot(None))
        filters.append(
            or_(
                Activity.deal_id.in_(company_deal_ids),
                Activity.external_source_id.in_(meeting_ext_ids),
            )
        )
    if type:
        filters.append(Activity.type == type)
    # Per-rep filter — supports the "Calls Logged" tile on the Contacts page
    # showing TODAY'S real call count for the logged-in rep, instead of the
    # page-bounded contact-derived count which silently drops to 0 when the
    # paginated view rotates (the 2026-05-07 "6 -> 0" Mahesh saw).
    effective_creator = created_by_id or (current_user.id if created_by_me else None)
    if effective_creator:
        filters.append(Activity.created_by_id == effective_creator)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            # Compare against naive-UTC created_at, so explicitly convert to
            # UTC. astimezone() without an arg uses the pod's local timezone
            # which we cannot guarantee is UTC.
            if since_dt.tzinfo is not None:
                since_dt = since_dt.astimezone(timezone.utc).replace(tzinfo=None)
            filters.append(Activity.created_at >= since_dt)
        except ValueError:
            # Bad input — ignore the filter rather than 500-ing the dashboard.
            logger.warning("activities.list: invalid since=%r — ignoring", since)
    items, total = await repo.list_paginated(
        *filters,
        skip=pagination.skip,
        limit=pagination.limit,
        order_by=Activity.created_at.desc(),
    )
    # Resolve the logger's display name so the UI can show "Manually logged by
    # <person>" instead of a faceless "Manually logged". One batched lookup.
    # SQLModel table rows reject undeclared attrs, so hydrate ActivityRead
    # objects (which declare user_name) and set the name there.
    creator_ids = {it.created_by_id for it in items if getattr(it, "created_by_id", None)}
    names: dict = {}
    if creator_ids:
        from app.models.user import User
        name_rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(creator_ids)))
        ).all()
        names = {uid: nm for uid, nm in name_rows}
    reads = []
    for it in items:
        read = ActivityRead.model_validate(it, from_attributes=True)
        nm = names.get(getattr(it, "created_by_id", None))
        if nm:
            read.user_name = nm
        reads.append(read)
    return PaginatedResponse.build(reads, total, pagination.skip, pagination.limit)


@router.get("/me/calls-today")
async def my_calls_today(session: DBSession, current_user: CurrentUser):
    reference = datetime.now(timezone.utc)
    local_reference = reference.astimezone(REPORT_CUTOFF_TIMEZONE)
    period_start_date = local_reference.date()
    if local_reference.time() < time(REPORT_CUTOFF_HOUR):
        period_start_date -= timedelta(days=1)
    period_start = datetime.combine(
        period_start_date,
        time(REPORT_CUTOFF_HOUR),
        tzinfo=REPORT_CUTOFF_TIMEZONE,
    ).astimezone(timezone.utc).replace(tzinfo=None)
    period_end = reference.replace(tzinfo=None)

    total = (
        await session.execute(
            select(func.count(Activity.id)).where(
                Activity.created_by_id == current_user.id,
                or_(
                    func.lower(Activity.type) == "call",
                    func.lower(Activity.medium) == "call",
                ),
                Activity.created_at >= period_start,
                Activity.created_at < period_end,
            )
        )
    ).scalar_one()
    return {
        "total": int(total or 0),
        "timezone": str(REPORT_CUTOFF_TIMEZONE),
        "cutoff_hour": REPORT_CUTOFF_HOUR,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }


@router.post("/", response_model=ActivityRead, status_code=201)
async def create_activity(payload: ActivityCreate, session: DBSession, current_user: CurrentUser, background_tasks: BackgroundTasks):
    # Stamp the creator so manual call / LinkedIn / note activities have rep
    # attribution, without requiring the frontend to send it.
    data = payload.model_dump()
    if not data.get("created_by_id"):
        data["created_by_id"] = current_user.id
    if not data.get("source"):
        data["source"] = "manual"
    activity = await ActivityRepository(session).create(data)
    if activity.deal_id or activity.contact_id:
        background_tasks.add_task(_refresh_tasks_after_activity_background, activity.deal_id, activity.contact_id)
    return activity


@router.get("/{activity_id}", response_model=ActivityRead)
async def get_activity(activity_id: UUID, session: DBSession, _user: CurrentUser):
    return await ActivityRepository(session).get_or_raise(activity_id)


@router.put("/{activity_id}", response_model=ActivityRead)
async def update_activity(activity_id: UUID, payload: ActivityUpdate, session: DBSession, _user: CurrentUser):
    repo = ActivityRepository(session)
    activity = await repo.get_or_raise(activity_id)
    return await repo.update(activity, payload.model_dump(exclude_unset=True))


@router.delete("/{activity_id}", status_code=204)
async def delete_activity(activity_id: UUID, session: DBSession, _user: CurrentUser):
    repo = ActivityRepository(session)
    activity = await repo.get_or_raise(activity_id)
    await repo.delete(activity)
