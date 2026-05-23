"""
PushSubscription — Web Push (VAPID) subscriptions per user.

One row per (user, browser, device) combination. The browser's PushSubscription
object is opaque from the server's perspective: we just store its three pieces
and replay them to the push service when we want to ring that device.

The `endpoint` value is globally unique to that subscription — same user on
the same phone unsubscribing and re-subscribing produces a new endpoint, so we
upsert on (endpoint) rather than on (user_id, user_agent) to handle that
cleanly.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class PushSubscription(SQLModel, table=True):
    __tablename__ = "push_subscriptions"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    # The push service URL the browser obtained from its push provider
    # (e.g. https://fcm.googleapis.com/fcm/send/... or https://updates.push.services.mozilla.com/...).
    endpoint: str = Field(unique=True, index=True)
    # The two key pieces the browser exposes — needed by pywebpush to encrypt
    # the payload so only this subscription can decrypt it.
    p256dh: str
    auth: str
    # Optional metadata so the user can recognize their devices in settings.
    user_agent: Optional[str] = None
    label: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    last_used_at: Optional[datetime] = None


class PushSubscriptionRead(SQLModel):
    id: UUID
    endpoint: str
    user_agent: Optional[str] = None
    label: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
