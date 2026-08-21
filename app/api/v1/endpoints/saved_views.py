"""CRUD for per-user saved views.

Reads filter the list to the caller (`user_id == _user.id`); no cross-user
view sharing in v1 — keeps auth simple and matches the URL-filter-snapshot
shape so it can be promoted to "shared workspace view" later without an
API change.

Defaulting: setting `is_default = true` unsets the previous default in the
same `(object_type, view_type)` slot atomically, so a user can never end up
with two simultaneous defaults.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import NotFoundError, ValidationError
from app.models.saved_view import (
    SavedView,
    SavedViewCreate,
    SavedViewRead,
    SavedViewUpdate,
    SUPPORTED_SAVED_VIEW_OBJECTS,
    SUPPORTED_SAVED_VIEW_TYPES,
)
from app.services.realtime import broadcaster

router = APIRouter(prefix="/saved-views", tags=["saved-views"])


def _to_read(v: SavedView) -> SavedViewRead:
    return SavedViewRead(
        id=v.id,
        user_id=v.user_id,
        object_type=v.object_type,
        name=v.name,
        view_type=v.view_type,
        filters=v.filters,
        sort=v.sort,
        columns=v.columns,
        is_default=v.is_default,
        last_used_at=v.last_used_at,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


def _validate_slot(object_type: str, view_type: str) -> None:
    if object_type not in SUPPORTED_SAVED_VIEW_OBJECTS:
        raise ValidationError(
            f"object_type must be one of {sorted(SUPPORTED_SAVED_VIEW_OBJECTS)}"
        )
    if view_type not in SUPPORTED_SAVED_VIEW_TYPES:
        raise ValidationError(
            f"view_type must be one of {sorted(SUPPORTED_SAVED_VIEW_TYPES)}"
        )


async def _unset_other_defaults(
    session, user_id: UUID, object_type: str, view_type: str, keep_id: Optional[UUID]
) -> None:
    stmt = select(SavedView).where(
        SavedView.user_id == user_id,
        SavedView.object_type == object_type,
        SavedView.view_type == view_type,
        SavedView.is_default == True,  # noqa: E712 — explicit SQL boolean
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        if row.id != keep_id:
            row.is_default = False
            row.updated_at = datetime.utcnow()
            session.add(row)


@router.get("", response_model=list[SavedViewRead])
async def list_saved_views(
    session: DBSession,
    _user: CurrentUser,
    object_type: Optional[str] = Query(default=None),
    view_type: Optional[str] = Query(default=None),
):
    stmt = select(SavedView).where(SavedView.user_id == _user.id)
    if object_type:
        stmt = stmt.where(SavedView.object_type == object_type)
    if view_type:
        stmt = stmt.where(SavedView.view_type == view_type)
    # Newest first; "last used" bump is done by PATCH /applied below.
    stmt = stmt.order_by(SavedView.is_default.desc(), SavedView.updated_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_read(r) for r in rows]


@router.post("", response_model=SavedViewRead, status_code=201)
async def create_saved_view(
    payload: SavedViewCreate,
    session: DBSession,
    _user: CurrentUser,
):
    name = (payload.name or "").strip()
    if not name:
        raise ValidationError("name is required")
    if len(name) > 120:
        raise ValidationError("name must be 120 characters or fewer")
    _validate_slot(payload.object_type, payload.view_type)

    if payload.is_default:
        await _unset_other_defaults(session, _user.id, payload.object_type, payload.view_type, keep_id=None)

    view = SavedView(
        user_id=_user.id,
        object_type=payload.object_type,
        name=name,
        view_type=payload.view_type,
        filters=payload.filters,
        sort=payload.sort,
        columns=payload.columns,
        is_default=payload.is_default,
    )
    session.add(view)
    await session.commit()
    await session.refresh(view)
    return _to_read(view)


@router.patch("/{view_id}", response_model=SavedViewRead)
async def update_saved_view(
    view_id: UUID,
    payload: SavedViewUpdate,
    session: DBSession,
    _user: CurrentUser,
):
    stmt = select(SavedView).where(SavedView.id == view_id, SavedView.user_id == _user.id)
    view = (await session.execute(stmt)).scalars().first()
    if not view:
        raise NotFoundError(f"Saved view {view_id} not found")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise ValidationError("name cannot be empty")
        if len(name) > 120:
            raise ValidationError("name must be 120 characters or fewer")
        view.name = name
    if "view_type" in data:
        if data["view_type"] not in SUPPORTED_SAVED_VIEW_TYPES:
            raise ValidationError(f"view_type must be one of {sorted(SUPPORTED_SAVED_VIEW_TYPES)}")
        view.view_type = data["view_type"]
    if "filters" in data:
        view.filters = data["filters"]
    if "sort" in data:
        view.sort = data["sort"]
    if "columns" in data:
        view.columns = data["columns"]
    if data.get("is_default"):
        await _unset_other_defaults(session, _user.id, view.object_type, view.view_type, keep_id=view.id)
        view.is_default = True
    elif "is_default" in data and data["is_default"] is False:
        view.is_default = False
    view.updated_at = datetime.utcnow()
    session.add(view)
    await session.commit()
    await session.refresh(view)
    return _to_read(view)


@router.post("/{view_id}/default", response_model=SavedViewRead)
async def set_default_saved_view(
    view_id: UUID,
    session: DBSession,
    _user: CurrentUser,
):
    stmt = select(SavedView).where(SavedView.id == view_id, SavedView.user_id == _user.id)
    view = (await session.execute(stmt)).scalars().first()
    if not view:
        raise NotFoundError(f"Saved view {view_id} not found")
    await _unset_other_defaults(session, _user.id, view.object_type, view.view_type, keep_id=view.id)
    view.is_default = True
    view.updated_at = datetime.utcnow()
    session.add(view)
    await session.commit()
    await session.refresh(view)
    return _to_read(view)


@router.post("/{view_id}/applied", response_model=SavedViewRead)
async def mark_view_applied(
    view_id: UUID,
    session: DBSession,
    _user: CurrentUser,
):
    """Bump `last_used_at` when the picker applies a view.

    Called from the page on every "switch view" click; cheap (one row, one
    timestamp), drives the "recently used" sort in the picker.
    """
    stmt = select(SavedView).where(SavedView.id == view_id, SavedView.user_id == _user.id)
    view = (await session.execute(stmt)).scalars().first()
    if not view:
        raise NotFoundError(f"Saved view {view_id} not found")
    view.last_used_at = datetime.utcnow()
    session.add(view)
    await session.commit()
    await session.refresh(view)
    return _to_read(view)


@router.delete("/{view_id}", status_code=204)
async def delete_saved_view(
    view_id: UUID,
    session: DBSession,
    _user: CurrentUser,
):
    stmt = select(SavedView).where(SavedView.id == view_id, SavedView.user_id == _user.id)
    view = (await session.execute(stmt)).scalars().first()
    if not view:
        raise NotFoundError(f"Saved view {view_id} not found")
    await session.delete(view)
    await session.commit()
