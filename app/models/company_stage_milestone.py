from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


MILESTONE_KEYS = (
    "demo_done",
    "poc_agreed",
    "poc_wip",
    "poc_done",
    "closed_won",
)


class CompanyStageMilestone(SQLModel, table=True):
    __tablename__ = "company_stage_milestones"
    __table_args__ = (
        UniqueConstraint("company_id", "milestone_key", name="uq_company_stage_milestone_company_key"),
    )

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    company_id: UUID = Field(foreign_key="companies.id", index=True)
    deal_id: Optional[UUID] = Field(default=None, foreign_key="deals.id", index=True)
    source_activity_id: Optional[UUID] = Field(default=None, foreign_key="activities.id", index=True)
    milestone_key: str = Field(index=True)
    first_reached_at: datetime = Field(index=True)
    source: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
