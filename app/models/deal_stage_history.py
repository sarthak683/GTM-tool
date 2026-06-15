"""
DealStageHistory — immutable audit log of every deal stage transition.

One row per transition. Powers the funnel conversion grid, stuck-deal rules,
and forecast accuracy. Writes happen through
`app.services.deal_stage_history.record_stage_transition` so every call site
looks the same.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class DealStageHistory(SQLModel, table=True):
    __tablename__ = "deal_stage_history"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    deal_id: UUID = Field(foreign_key="deals.id", index=True)
    from_stage: Optional[str] = Field(default=None, index=True)
    to_stage: str = Field(index=True)
    changed_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    reason: Optional[str] = None
    source: Optional[str] = Field(default=None, index=True)


class DealStageHistoryRead(SQLModel):
    id: UUID
    deal_id: UUID
    from_stage: Optional[str]
    to_stage: str
    changed_by_id: Optional[UUID]
    changed_at: datetime
    reason: Optional[str]
    source: Optional[str]
