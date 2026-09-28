"""
Outreach Sequence — the new, reusable multi-contact cadence system.

Replaces the old per-contact "Outreach Sequence" (app.models.outreach —
OutreachSequence/OutreachStep, AI-generated, Instantly-driven). That system
is retired: its tables are left in place with their historical data intact,
but nothing in the app writes to or reads from them anymore.

Three entities:
- Sequence: the reusable template a rep builds once ("Enterprise Cold
  Outreach"), Team-shared or private.
- SequenceStep: one ordered step in a Sequence — a channel (email/call/
  linkedin) and a delay_minutes (minutes after the PREVIOUS step's
  completion; for step 1, minutes after enrollment). The builder shows this
  as an amount + unit (Days/Hours/Min) picker; delay_minutes is always the
  normalized minutes value on the wire and in storage.
- Enrollment: one contact running one Sequence. A contact can only have one
  active Enrollment at a time (enforced at the API layer, not the DB).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

SEQUENCE_STEP_TYPES = {"email", "call", "linkedin"}
SEQUENCE_EMAIL_SEND_VIA = {"personal", "instantly"}
# LinkedIn steps only — a build-time label for which kind of touch this step
# is. Purely descriptive: it doesn't change completion behavior (still the
# same "Log LinkedIn touch" dialog either way), it just pre-selects that
# dialog's outcome dropdown to save the rep a click.
SEQUENCE_LINKEDIN_CATEGORIES = {"dm", "follow_up", "inmail"}
ENROLLMENT_STATUSES = {"active", "completed", "removed"}


class SequenceBase(SQLModel):
    name: str
    shared: bool = False


class Sequence(SequenceBase, table=True):
    __tablename__ = "sequences"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SequenceCreate(SQLModel):
    name: str
    shared: bool = False


class SequenceUpdate(SQLModel):
    name: Optional[str] = None
    shared: Optional[bool] = None


class SequenceRead(SequenceBase):
    id: UUID
    owner_id: UUID
    owner_name: Optional[str] = None
    step_count: int = 0
    active_enrollment_count: int = 0
    created_at: datetime
    updated_at: datetime


class SequenceStepBase(SQLModel):
    type: str  # email | call | linkedin
    delay_minutes: int = 0
    # Email steps only. Personal = the rep's own connected Gmail (existing
    # "Log Email" dialog). Instantly = add the contact as a lead to
    # instantly_campaign_id; that step completes later, on the existing
    # Instantly webhook confirming the send — not on a click.
    send_via: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    # LinkedIn steps only — see SEQUENCE_LINKEDIN_CATEGORIES.
    linkedin_category: Optional[str] = None


class SequenceStep(SequenceStepBase, table=True):
    __tablename__ = "sequence_steps"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    # Position within the sequence — always assigned server-side from the
    # submitted list's order, never client-supplied (see SequenceStepCreate).
    order: int = Field(default=0, index=True)
    sequence_id: UUID = Field(foreign_key="sequences.id", index=True)


class SequenceStepCreate(SequenceStepBase):
    pass


class SequenceStepRead(SequenceStepBase):
    id: UUID
    sequence_id: UUID
    order: int


class EnrollmentBase(SQLModel):
    contact_id: UUID
    sequence_id: UUID
    current_step: int = 1
    status: str = "active"


class Enrollment(EnrollmentBase, table=True):
    __tablename__ = "enrollments"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    contact_id: UUID = Field(foreign_key="contacts.id", index=True)
    sequence_id: UUID = Field(foreign_key="sequences.id", index=True)
    enrolled_by: UUID = Field(foreign_key="users.id")
    enrolled_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class EnrollmentRead(EnrollmentBase):
    id: UUID
    enrolled_by: UUID
    enrolled_at: datetime
    completed_at: Optional[datetime] = None
    sequence_name: Optional[str] = None
    total_steps: int = 0
