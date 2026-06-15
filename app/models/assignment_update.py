from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import field_validator, model_validator
from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

TRACKER_ENTITY_TYPES = frozenset({"company", "contact", "deal"})
TRACKER_ASSIGNMENT_ROLES = frozenset({"owner", "ae", "sdr"})
TRACKER_PROGRESS_STATES = frozenset({
    "new",
    "working",
    "waiting_on_buyer",
    "meeting_booked",
    "qualified",
    "deal_created",
    "blocked",
    "closed",
})
TRACKER_CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
TRACKER_BUYER_SIGNALS = frozenset({
    "none",
    "replied",
    "interested",
    "champion_identified",
    "meeting_requested",
    "commercial_discussion",
    "verbal_yes",
})
TRACKER_BLOCKER_TYPES = frozenset({
    "none",
    "no_response",
    "wrong_person",
    "timing",
    "budget",
    "competition",
    "internal_dependency",
    "legal_security",
    "other",
})
TRACKER_TOUCH_TYPES = frozenset({
    "none",
    "email",
    "call",
    "linkedin",
    "meeting",
    "research",
    "internal",
})
TRACKER_STALE_DAYS = 3


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class AssignmentUpdateBase(SQLModel):
    entity_type: str
    entity_id: UUID
    assignment_role: str = "owner"
    progress_state: str = "working"
    confidence: str = "medium"
    buyer_signal: str = "none"
    blocker_type: str = "none"
    last_touch_type: str = "none"
    summary: str = Field(sa_column=Column(Text))
    next_step: str = Field(sa_column=Column(Text))
    next_step_due_date: Optional[date] = Field(default=None, index=True)
    blocker_detail: Optional[str] = Field(default=None, sa_column=Column(Text))

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of {sorted(TRACKER_ENTITY_TYPES)}")
        return normalized

    @field_validator("assignment_role")
    @classmethod
    def validate_assignment_role(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_ASSIGNMENT_ROLES:
            raise ValueError(f"assignment_role must be one of {sorted(TRACKER_ASSIGNMENT_ROLES)}")
        return normalized

    @field_validator("progress_state")
    @classmethod
    def validate_progress_state(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_PROGRESS_STATES:
            raise ValueError(f"progress_state must be one of {sorted(TRACKER_PROGRESS_STATES)}")
        return normalized

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_CONFIDENCE_LEVELS:
            raise ValueError(f"confidence must be one of {sorted(TRACKER_CONFIDENCE_LEVELS)}")
        return normalized

    @field_validator("buyer_signal")
    @classmethod
    def validate_buyer_signal(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_BUYER_SIGNALS:
            raise ValueError(f"buyer_signal must be one of {sorted(TRACKER_BUYER_SIGNALS)}")
        return normalized

    @field_validator("blocker_type")
    @classmethod
    def validate_blocker_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_BLOCKER_TYPES:
            raise ValueError(f"blocker_type must be one of {sorted(TRACKER_BLOCKER_TYPES)}")
        return normalized

    @field_validator("last_touch_type")
    @classmethod
    def validate_last_touch_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in TRACKER_TOUCH_TYPES:
            raise ValueError(f"last_touch_type must be one of {sorted(TRACKER_TOUCH_TYPES)}")
        return normalized

    @field_validator("summary", "next_step", "blocker_detail")
    @classmethod
    def trim_text_fields(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_text(value)

    @model_validator(mode="after")
    def validate_required_text(self) -> "AssignmentUpdateBase":
        if not self.summary:
            raise ValueError("summary is required")
        if not self.next_step:
            raise ValueError("next_step is required")
        return self


class AssignmentUpdate(AssignmentUpdateBase, table=True):
    __tablename__ = "assignment_updates"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    assignee_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    entity_name_snapshot: Optional[str] = None
    company_name_snapshot: Optional[str] = None
    assignee_name_snapshot: Optional[str] = None
    assignee_email_snapshot: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AssignmentUpdateCreate(AssignmentUpdateBase):
    pass


class AssignmentUpdateRead(AssignmentUpdateBase):
    id: UUID
    assignee_id: Optional[UUID] = None
    created_by_id: Optional[UUID] = None
    entity_name_snapshot: Optional[str] = None
    company_name_snapshot: Optional[str] = None
    assignee_name_snapshot: Optional[str] = None
    assignee_email_snapshot: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: datetime


class ExecutionTrackerItemRead(SQLModel):
    entity_type: str
    entity_id: UUID
    entity_name: str
    entity_subtitle: Optional[str] = None
    entity_link: str
    company_name: Optional[str] = None
    assignee_id: UUID
    assignee_name: Optional[str] = None
    assignment_role: str
    system_status: Optional[str] = None
    entity_updated_at: datetime
    needs_update: bool
    next_step_overdue: bool
    latest_update: Optional[AssignmentUpdateRead] = None


class ExecutionTrackerSummary(SQLModel):
    total_items: int
    no_update_items: int
    needs_update_items: int
    blocked_items: int
    overdue_next_steps: int
    positive_momentum_items: int
