"""
ContactDispositionHistory — immutable audit log of every
Contact.call_disposition transition (interested, not_interested, dnd, etc.).

Mirrors DealStageHistory. call_disposition previously had NO audit trail —
`apply_call_disposition_effects` computes a before/after diff but discards it
after the API response. Writes happen through
`app.services.contact_disposition_history.record_disposition_change` so every
call site looks the same. Powers the weekly digest email's "prospects marked
DND" section (filtered to to_disposition == 'dnd'), though this logs every
transition, not just DND, for future reuse.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class ContactDispositionHistory(SQLModel, table=True):
    __tablename__ = "contact_disposition_history"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    contact_id: UUID = Field(foreign_key="contacts.id", index=True)
    from_disposition: Optional[str] = Field(default=None, index=True)
    to_disposition: Optional[str] = Field(default=None, index=True)
    changed_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow, index=True)
