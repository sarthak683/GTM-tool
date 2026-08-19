"""Data Room item model — curated sales assets linked from Google Workspace.

Categories:
  documentation          — Google Docs
  decks                  — Google Slides / PDFs
  videos                 — Demo videos (Google Drive)
  demo_recordings        — Client call recordings (Google Drive)
  post_poc_collaterals   — Post-POC handoff material (decks, videos, docs)

Each item is a title + an embeddable URL (Google Docs/Slides/Drive preview
links, or a direct PDF). Thumbnails are optional and surfaced on the card.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class DataRoomItemBase(SQLModel):
    category: str  # documentation | decks | videos | demo_recordings | post_poc_collaterals
    title: str
    embed_url: str = Field(sa_column=Column(Text))
    thumbnail_url: Optional[str] = Field(default=None, sa_column=Column(Text))


class DataRoomItem(DataRoomItemBase, table=True):
    __tablename__ = "data_room_items"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DataRoomItemCreate(DataRoomItemBase):
    pass


class DataRoomItemRead(DataRoomItemBase):
    id: UUID
    created_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
