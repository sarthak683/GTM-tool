from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import field_validator
from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.meeting import to_naive_utc


TASK_ENTITY_TYPES = {"company", "contact", "deal"}
TASK_TYPES = {"manual", "system"}
TASK_STATUSES = {"open", "completed", "dismissed"}
TASK_PRIORITIES = {"low", "medium", "high"}
TASK_ASSIGNED_ROLES = {"admin", "ae", "sdr"}
TASK_TRACKS = {"sales_ai", "critical", "hygiene", "manual"}


class TaskBase(SQLModel):
    entity_type: str
    entity_id: UUID = Field(index=True)
    task_type: str = "manual"
    title: str
    description: Optional[str] = None
    status: str = "open"
    priority: str = "medium"
    source: Optional[str] = None
    recommended_action: Optional[str] = None
    due_at: Optional[datetime] = None


class Task(TaskBase, table=True):
    __tablename__ = "tasks"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    action_payload: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    system_key: Optional[str] = Field(default=None, index=True)
    task_track: Optional[str] = Field(default=None, index=True)
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    assigned_role: Optional[str] = Field(default=None, index=True)
    assigned_to_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    accepted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TaskCommentBase(SQLModel):
    body: str


class TaskComment(TaskCommentBase, table=True):
    __tablename__ = "task_comments"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    task_id: UUID = Field(foreign_key="tasks.id", index=True)
    body: str = Field(sa_column=Column(Text))
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class TaskCommentCreate(TaskCommentBase):
    pass


class TaskCommentRead(TaskCommentBase):
    id: UUID
    task_id: UUID
    created_by_id: Optional[UUID] = None
    created_by_name: Optional[str] = None
    created_at: datetime


class TaskCreate(SQLModel):
    entity_type: str
    entity_id: UUID
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    due_at: Optional[datetime] = None
    assigned_role: Optional[str] = None
    assigned_to_id: Optional[UUID] = None

    @field_validator("due_at", mode="before")
    @classmethod
    def strip_timezone(cls, v: Any) -> Optional[datetime]:
        return to_naive_utc(v)


class TaskUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    due_at: Optional[datetime] = None
    status: Optional[str] = None
    assigned_role: Optional[str] = None
    assigned_to_id: Optional[UUID] = None

    @field_validator("due_at", mode="before")
    @classmethod
    def strip_timezone(cls, v: Any) -> Optional[datetime]:
        return to_naive_utc(v)


class TaskRead(TaskBase):
    id: UUID
    action_payload: Optional[Any] = None
    system_key: Optional[str] = None
    task_track: Optional[str] = None
    created_by_id: Optional[UUID] = None
    created_by_name: Optional[str] = None
    assigned_role: Optional[str] = None
    assigned_to_id: Optional[UUID] = None
    assigned_to_name: Optional[str] = None
    accepted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    comments: list[TaskCommentRead] = []


class TaskWorkspaceRead(TaskRead):
    entity_name: str
    entity_subtitle: Optional[str] = None
    entity_link: str
