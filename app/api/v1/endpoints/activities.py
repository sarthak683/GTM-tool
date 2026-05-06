from typing import Optional
from uuid import UUID

import logging

from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy import or_, select

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
    _user: CurrentUser,
    deal_id: Optional[UUID] = Query(default=None),
    contact_id: Optional[UUID] = Query(default=None),
    company_id: Optional[UUID] = Query(default=None),
    type: Optional[str] = Query(default=None),
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
    items, total = await repo.list_paginated(
        *filters,
        skip=pagination.skip,
        limit=pagination.limit,
        order_by=Activity.created_at.desc(),
    )
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


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
