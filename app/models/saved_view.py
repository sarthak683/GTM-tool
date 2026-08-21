"""Per-user saved views for the pipeline, contacts, etc.

A "view" captures the filter state of a page (search text, stage set, owner
set, assignee set, etc.) under a name the user picks. Switching views swaps
the entire filter set in one click, instead of clicking chips one at a time.

This is intentionally narrow — just filters + sort + view-type + name + default
flag. Column visibility/order and per-field overrides belong in a future
follow-up if the team asks for them.

Storage shape: `filters` and `sort` are JSONB blobs holding the same shape
as the URL search params the page already serializes to. That means
"save view" = "snapshot the current URL params" and "apply view" = "push
the params back into the URL", with no client-side schema translation.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Object types we currently support. Adding a new one means:
#   1. Append the value here.
#   2. Wire a picker on the page that wants to use it.
# No migration needed — `object_type` is a free-form string.
SUPPORTED_SAVED_VIEW_OBJECTS = ("deal", "contact", "company", "prospect")

# View-type values. The page that loads the view checks this and shows the
# matching layout (table / kanban / list). "kanban" and "table" are the two
# we render today; the others are reserved for future layouts.
SUPPORTED_SAVED_VIEW_TYPES = ("table", "kanban", "list", "calendar")


class SavedView(SQLModel, table=True):
    __tablename__ = "saved_views"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    user_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    object_type: str = Field(index=True)  # deal / contact / company / prospect
    name: str
    view_type: str = "kanban"  # table / kanban / list / calendar
    filters: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    sort: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    columns: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    is_default: bool = False
    # Last applied at — bumped by the page on every apply. Surfaces "recently
    # used" in the picker without adding a separate audit table.
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SavedViewCreate(SQLModel):
    object_type: str
    name: str
    view_type: str = "kanban"
    filters: Optional[Any] = None
    sort: Optional[Any] = None
    columns: Optional[Any] = None
    is_default: bool = False


class SavedViewUpdate(SQLModel):
    name: Optional[str] = None
    view_type: Optional[str] = None
    filters: Optional[Any] = None
    sort: Optional[Any] = None
    columns: Optional[Any] = None
    is_default: Optional[bool] = None


class SavedViewRead(SQLModel):
    id: UUID
    user_id: Optional[UUID] = None
    object_type: str
    name: str
    view_type: str
    filters: Optional[Any] = None
    sort: Optional[Any] = None
    columns: Optional[Any] = None
    is_default: bool
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
