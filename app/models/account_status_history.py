"""
AccountStatusHistory — immutable audit log of every Company.account_status
transition (DND, Not a Fit, Reach Out Later, and any other value).

Mirrors DealStageHistory. account_status previously had NO audit trail — it
was a plain column overwrite, so nothing could answer "who disabled this
account and when." Writes happen through
`app.services.account_status_history.record_account_status_change` so every
call site looks the same. Powers the weekly digest email's "accounts marked
DND / Not a Fit / Reach Out Later" section.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class AccountStatusHistory(SQLModel, table=True):
    __tablename__ = "account_status_history"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    company_id: UUID = Field(foreign_key="companies.id", index=True)
    from_status: Optional[str] = Field(default=None, index=True)
    to_status: Optional[str] = Field(default=None, index=True)
    changed_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow, index=True)
