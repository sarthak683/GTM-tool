from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class OutreachSequenceBase(SQLModel):
    contact_id: UUID = Field(foreign_key="contacts.id", index=True)
    company_id: UUID = Field(foreign_key="companies.id", index=True)
    persona: Optional[str] = None
    status: str = "draft"  # draft | approved | launched | paused | completed


class OutreachSequence(OutreachSequenceBase, table=True):
    __tablename__ = "outreach_sequences"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)

    # GPT-4o generated messages — kept for backwards compatibility + quick preview
    email_1: Optional[str] = None
    email_2: Optional[str] = None
    email_3: Optional[str] = None
    linkedin_message: Optional[str] = None

    subject_1: Optional[str] = None
    subject_2: Optional[str] = None
    subject_3: Optional[str] = None

    # Instantly campaign tracking
    instantly_campaign_id: Optional[str] = None
    instantly_campaign_status: Optional[str] = None  # draft | active | paused | completed

    # Context used to generate this sequence (for audit/regen)
    generation_context: Optional[dict] = Field(
        default=None, sa_column=Column(JSONB)
    )

    generated_at: Optional[datetime] = None
    launched_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OutreachSequenceRead(OutreachSequenceBase):
    id: UUID
    email_1: Optional[str] = None
    email_2: Optional[str] = None
    email_3: Optional[str] = None
    linkedin_message: Optional[str] = None
    subject_1: Optional[str] = None
    subject_2: Optional[str] = None
    subject_3: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    instantly_campaign_status: Optional[str] = None
    generation_context: Optional[Any] = None
    generated_at: Optional[datetime] = None
    launched_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# ── OutreachStep — flexible N-step sequence, synced to Instantly ──────────────

class OutreachStepBase(SQLModel):
    sequence_id: UUID = Field(foreign_key="outreach_sequences.id", index=True)
    step_number: int                        # 1-based ordering
    subject: Optional[str] = None          # Step 1 has its own subject; steps 2+ default to "Re: ..."
    body: str                              # Email body (plain text or HTML)
    delay_value: int = 0                   # Delay before sending this step (0 = send immediately for step 1)
    delay_unit: str = "Days"               # Days | Hours | Minutes


class OutreachStep(OutreachStepBase, table=True):
    __tablename__ = "outreach_steps"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)

    # A/B variants for this step — list of {"subject": ..., "body": ...} dicts
    variants: Optional[Any] = Field(default=None, sa_column=Column(JSONB))

    status: str = "draft"  # draft | active | skipped

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def channel(self) -> str:
        if isinstance(self.variants, dict):
            channel = str(self.variants.get("channel") or "").strip().lower()
            if channel in {"email", "call", "linkedin"}:
                return channel
        return "email"

    @channel.setter
    def channel(self, value: str) -> None:
        channel = str(value or "email").strip().lower()
        if channel not in {"email", "call", "linkedin"}:
            channel = "email"
        if isinstance(self.variants, dict):
            payload = dict(self.variants)
        elif isinstance(self.variants, list):
            payload = {"variants": self.variants}
        else:
            payload = {}
        payload["channel"] = channel
        self.variants = payload


class OutreachStepRead(OutreachStepBase):
    id: UUID
    channel: str = "email"
    variants: Optional[Any] = None
    status: str
    created_at: datetime
    updated_at: datetime


class OutreachStepCreate(SQLModel):
    step_number: int
    channel: str = "email"
    subject: Optional[str] = None
    body: str
    delay_value: int = 0
    delay_unit: str = "Days"
    variants: Optional[Any] = None


class OutreachStepUpdate(SQLModel):
    channel: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    delay_value: Optional[int] = None
    delay_unit: Optional[str] = None
    variants: Optional[Any] = None
    status: Optional[str] = None
