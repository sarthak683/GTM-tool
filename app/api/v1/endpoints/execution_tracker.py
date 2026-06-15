from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentUser, DBSession, Pagination
from app.core.exceptions import ValidationError
from app.models.assignment_update import (
    AssignmentUpdateCreate,
    AssignmentUpdateRead,
    ExecutionTrackerItemRead,
    ExecutionTrackerSummary,
    TRACKER_ENTITY_TYPES,
    TRACKER_PROGRESS_STATES,
)
from app.schemas.common import PaginatedResponse
from app.services.execution_tracker import (
    build_execution_items,
    build_execution_summary,
    create_item_update,
    list_item_updates,
    load_assignment_contexts,
    load_latest_updates,
)

router = APIRouter(prefix="/execution-tracker", tags=["execution-tracker"])


def _validate_filters(entity_type: Optional[str], progress_state: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    normalized_entity_type = (entity_type or "").strip().lower() or None
    normalized_progress_state = (progress_state or "").strip().lower() or None

    if normalized_entity_type and normalized_entity_type not in TRACKER_ENTITY_TYPES:
        raise ValidationError(f"entity_type must be one of {sorted(TRACKER_ENTITY_TYPES)}")
    if normalized_progress_state and normalized_progress_state not in TRACKER_PROGRESS_STATES:
        raise ValidationError(f"progress_state must be one of {sorted(TRACKER_PROGRESS_STATES)}")
    return normalized_entity_type, normalized_progress_state


@router.get("/items", response_model=PaginatedResponse[ExecutionTrackerItemRead])
async def list_execution_items(
    session: DBSession,
    current_user: CurrentUser,
    pagination: Pagination,
    assignee_id: Optional[UUID] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    progress_state: Optional[str] = Query(default=None),
    needs_update_only: bool = Query(default=False),
    q: Optional[str] = Query(default=None, description="Search by entity, company, assignee, or latest update text"),
):
    normalized_entity_type, normalized_progress_state = _validate_filters(entity_type, progress_state)
    contexts = await load_assignment_contexts(
        session,
        current_user,
        assignee_id=assignee_id,
        entity_type=normalized_entity_type,
    )
    latest_updates = await load_latest_updates(session, contexts)
    items = build_execution_items(
        contexts,
        latest_updates,
        q=q,
        progress_state=normalized_progress_state,
        needs_update_only=needs_update_only,
    )
    paged = items[pagination.skip:pagination.skip + pagination.limit]
    return PaginatedResponse.build(paged, len(items), pagination.skip, pagination.limit)


@router.get("/summary", response_model=ExecutionTrackerSummary)
async def execution_tracker_summary(
    session: DBSession,
    current_user: CurrentUser,
    assignee_id: Optional[UUID] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    progress_state: Optional[str] = Query(default=None),
    needs_update_only: bool = Query(default=False),
    q: Optional[str] = Query(default=None),
):
    normalized_entity_type, normalized_progress_state = _validate_filters(entity_type, progress_state)
    contexts = await load_assignment_contexts(
        session,
        current_user,
        assignee_id=assignee_id,
        entity_type=normalized_entity_type,
    )
    latest_updates = await load_latest_updates(session, contexts)
    items = build_execution_items(
        contexts,
        latest_updates,
        q=q,
        progress_state=normalized_progress_state,
        needs_update_only=needs_update_only,
    )
    return build_execution_summary(items)


@router.get("/items/{entity_type}/{entity_id}/updates", response_model=list[AssignmentUpdateRead])
async def get_execution_item_updates(
    entity_type: str,
    entity_id: UUID,
    session: DBSession,
    current_user: CurrentUser,
    assignment_role: str = Query(...),
):
    return await list_item_updates(
        session,
        current_user,
        entity_type=entity_type,
        entity_id=entity_id,
        assignment_role=assignment_role,
    )


@router.post("/updates", response_model=AssignmentUpdateRead, status_code=201)
async def create_execution_update(
    payload: AssignmentUpdateCreate,
    session: DBSession,
    current_user: CurrentUser,
):
    return await create_item_update(session, current_user, payload)
