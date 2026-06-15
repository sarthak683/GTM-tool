"""Follow-up reminder model — linked to contacts for stakeholder engagement tracking."""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Reminder(SQLModel, table=True):
    __tablename__ = "reminders"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    contact_id: UUID = Field(foreign_key="contacts.id", index=True)
    company_id: Optional[UUID] = Field(default=None, foreign_key="companies.id", index=True)
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id")
    assigned_to_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    note: str
    due_at: datetime = Field(index=True)
    status: str = Field(default="pending", index=True)  # pending | completed | dismissed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class ReminderCreate(SQLModel):
    contact_id: UUID
    company_id: Optional[UUID] = None
    note: str
    due_at: datetime
    assigned_to_id: Optional[UUID] = None


class ReminderRead(SQLModel):
    id: UUID
    contact_id: UUID
    company_id: Optional[UUID] = None
    created_by_id: Optional[UUID] = None
    assigned_to_id: Optional[UUID] = None
    note: str
    due_at: datetime
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    # Joined fields
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    assigned_to_name: Optional[str] = None


class ReminderUpdate(SQLModel):
    note: Optional[str] = None
    due_at: Optional[datetime] = None
    status: Optional[str] = None
    completed_at: Optional[datetime] = None
