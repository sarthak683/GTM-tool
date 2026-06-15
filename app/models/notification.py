from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


# Notification `type` enum. Keep narrow so the frontend can switch on it
# without a fallback bucket. Adding a new type requires:
#   (1) appending here,
#   (2) adding a renderer in frontend NotificationBell,
#   (3) adding an accept handler in app/api/v1/endpoints/notifications.py
#       (if the notification has an Accept action).
NOTIFICATION_TYPES = (
    # Reply-sentiment classifier detected a meeting was booked / the
    # prospect explicitly wants to meet. Accept = create a Deal in the
    # pipeline; payload carries the suggested fields.
    "meeting_booked_suggest_deal",
    # Informational: prospects and/or accounts were added (manual add or
    # import). Fanned out to admins + the assigned owner. No Accept action.
    "records_added",
)


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    type: str = Field(index=True)
    title: str
    body: Optional[str] = None
    action_payload: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    # Per-(user, dedup_key) uniqueness guards against webhook re-delivery
    # and re-classification spawning duplicate bell rows.
    dedup_key: Optional[str] = Field(default=None, index=True)
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationRead(SQLModel):
    id: UUID
    user_id: UUID
    type: str
    title: str
    body: Optional[str] = None
    action_payload: Optional[Any] = None
    dedup_key: Optional[str] = None
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
