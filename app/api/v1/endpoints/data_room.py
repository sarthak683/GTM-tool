"""
Data Room endpoints — curated sales assets (Google Docs, Slides, demo videos,
client call recordings) shown in the multi-tab Data Room page.

  GET    /data-room/items            — list items, optionally filtered by category
  POST   /data-room/items            — add an item (title + embed URL + optional thumbnail)
  DELETE /data-room/items/{id}       — remove an item

The Data Room is a shared sales asset library, so the list is readable by any
authenticated user; anyone can add or remove an item (the creator is stamped
server-side for attribution).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import field_validator
from sqlalchemy.exc import IntegrityError

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import ValidationError
from app.models.data_room_item import (
    DataRoomItem,
    DataRoomItemCreate,
    DataRoomItemRead,
)
from app.repositories.base import BaseRepository

router = APIRouter(prefix="/data-room", tags=["data-room"])

DATA_ROOM_CATEGORIES = {
    "documentation",   # Google Docs
    "decks",           # Google Slides / PDFs
    "videos",          # Demo videos (Google Drive)
    "demo_recordings",  # Client call recordings (Google Drive)
}


class DataRoomItemCreatePayload(DataRoomItemCreate):
    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in DATA_ROOM_CATEGORIES:
            raise ValueError(
                f"category must be one of: {', '.join(sorted(DATA_ROOM_CATEGORIES))}"
            )
        return value

    @field_validator("embed_url")
    @classmethod
    def validate_embed_url(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("embed_url must be an absolute http(s) URL")
        return stripped


@router.get("/items", response_model=list[DataRoomItemRead])
async def list_data_room_items(
    category: Optional[str] = Query(default=None),
    session: DBSession = None,
    _user: CurrentUser = None,
):
    filters = []
    if category:
        if category not in DATA_ROOM_CATEGORIES:
            raise ValidationError(
                f"category must be one of: {', '.join(sorted(DATA_ROOM_CATEGORIES))}"
            )
        filters.append(DataRoomItem.category == category)
    return await BaseRepository(DataRoomItem, session).list(
        *filters, order_by=DataRoomItem.created_at
    )


@router.post("/items", response_model=DataRoomItemRead, status_code=201)
async def create_data_room_item(
    payload: DataRoomItemCreatePayload,
    session: DBSession = None,
    current_user: CurrentUser = None,
):
    try:
        return await BaseRepository(DataRoomItem, session).create(
            {**payload.model_dump(), "created_by_id": current_user.id}
        )
    except IntegrityError as exc:
        raise ValidationError("Could not save data room item.") from exc


@router.delete("/items/{item_id}", status_code=204)
async def delete_data_room_item(
    item_id: UUID,
    session: DBSession = None,
    _user: CurrentUser = None,
):
    repo = BaseRepository(DataRoomItem, session)
    item = await repo.get_or_raise(item_id)
    await repo.delete(item)
