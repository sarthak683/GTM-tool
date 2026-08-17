"""
Meeting model — tracks customer meetings end-to-end.

Lifecycle:
  scheduled → in_progress → completed → scored

Pre-brief:   generated before the call (company + contact research)
Post-score:  AI-generated after the call (win/loss factors, learning path)
mom:         Minutes of Meeting email draft
"""
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel
from pydantic import field_validator


def to_naive_utc(v: Any) -> Optional[datetime]:
    """Coerce ISO strings / tz-aware datetimes to naive UTC.

    All timestamp columns are TIMESTAMP WITHOUT TIME ZONE and asyncpg rejects
    aware datetimes, so the frontend's ``...Z`` ISO strings must be normalized
    before they reach the driver. Shared by the Meeting/Task/Deal write schemas.
    """
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("must be a valid ISO datetime, not free text") from exc
    if isinstance(v, datetime) and v.tzinfo is not None:
        v = v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


class MeetingBase(SQLModel):
    title: str
    company_id: Optional[UUID] = Field(default=None, foreign_key="companies.id", index=True)
    deal_id: Optional[UUID] = Field(default=None, foreign_key="deals.id", index=True)
    owner_user_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    scheduled_at: Optional[datetime] = None

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def strip_timezone(cls, v: Any) -> Optional[datetime]:
        """Ensure scheduled_at is always a naive UTC datetime (TIMESTAMP WITHOUT TIME ZONE)."""
        return to_naive_utc(v)
    status: str = "scheduled"     # scheduled | completed | cancelled
    meeting_type: str = "discovery"  # discovery | demo | poc | qbr | other


class Meeting(MeetingBase, table=True):
    __tablename__ = "meetings"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    external_source: Optional[str] = Field(default=None, index=True)
    external_source_id: Optional[str] = Field(default=None, index=True)
    synced_by_user_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    synced_at: Optional[datetime] = None
    manually_linked: bool = Field(default=False)
    meeting_url: Optional[str] = Field(default=None, sa_column=Column(Text))
    recording_url: Optional[str] = Field(default=None, sa_column=Column(Text))
    # Pre-meeting
    pre_brief: Optional[str] = Field(default=None, sa_column=Column(Text))
    demo_strategy: Optional[str] = Field(default=None, sa_column=Column(Text))
    research_data: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    attendees: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    # Post-meeting
    raw_notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    ai_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    mom_draft: Optional[str] = Field(default=None, sa_column=Column(Text))
    meeting_score: Optional[int] = None   # 0–100
    what_went_right: Optional[str] = Field(default=None, sa_column=Column(Text))
    what_went_wrong: Optional[str] = Field(default=None, sa_column=Column(Text))
    next_steps: Optional[str] = Field(default=None, sa_column=Column(Text))
    intel_email_sent_at: Optional[datetime] = None
    # True when every attendee email belongs to a workspace internal domain.
    # Set at sync time; backfilled by migration 055.  The UI hides these from
    # the default Meetings and Pre-Meeting views.
    is_internal: bool = Field(default=False, index=True)
    # Sales Lifecycle SOP stage 04: the AE classifies each upcoming client call
    # L1/L2/L3 from the invite's attendee list, and that sets how the call is
    # run. Lives on the meeting, not the deal, because one deal runs several
    # calls with different audiences. See app.services.call_levels.
    call_level: Optional[str] = Field(default=None, index=True)   # L1 | L2 | L3
    # "auto" = classifier, "manual" = an AE decided at the prep call. Attendee
    # lists keep changing as people accept and decline, so the classifier
    # re-runs on every sync — and must never overwrite a manual value.
    call_level_source: Optional[str] = None
    call_level_set_by_id: Optional[UUID] = None
    call_level_set_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MeetingCreate(MeetingBase):
    attendees: Optional[Any] = None


class MeetingRead(MeetingBase):
    id: UUID
    external_source: Optional[str] = None
    external_source_id: Optional[str] = None
    synced_by_user_id: Optional[UUID] = None
    synced_at: Optional[datetime] = None
    manually_linked: bool = False
    meeting_url: Optional[str] = None
    recording_url: Optional[str] = None
    pre_brief: Optional[str] = None
    demo_strategy: Optional[str] = None
    research_data: Optional[Any] = None
    attendees: Optional[Any] = None
    raw_notes: Optional[str] = None
    ai_summary: Optional[str] = None
    mom_draft: Optional[str] = None
    meeting_score: Optional[int] = None
    what_went_right: Optional[str] = None
    what_went_wrong: Optional[str] = None
    next_steps: Optional[str] = None
    intel_email_sent_at: Optional[datetime] = None
    is_internal: bool = False
    call_level: Optional[str] = None
    call_level_source: Optional[str] = None
    call_level_set_by_id: Optional[UUID] = None
    call_level_set_at: Optional[datetime] = None
    # Computed per request, never stored: what the CURRENT attendee list implies
    # ({level, confidence, rationale, external_count, titles_known,
    # senior_attendees}). A stored confidence would go stale the moment someone
    # accepted the invite, and its whole job is to tell the AE whether the
    # suggestion can be trusted right now. Populated by the meetings endpoints.
    call_level_suggestion: Optional[Any] = None
    created_at: datetime
    updated_at: datetime


class MeetingUpdate(SQLModel):
    title: Optional[str] = None
    company_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    owner_user_id: Optional[UUID] = None
    scheduled_at: Optional[datetime] = None
    status: Optional[str] = None
    meeting_type: Optional[str] = None
    external_source: Optional[str] = None
    external_source_id: Optional[str] = None
    meeting_url: Optional[str] = None
    recording_url: Optional[str] = None
    attendees: Optional[Any] = None
    raw_notes: Optional[str] = None
    ai_summary: Optional[str] = None
    mom_draft: Optional[str] = None
    meeting_score: Optional[int] = None
    what_went_right: Optional[str] = None
    what_went_wrong: Optional[str] = None
    next_steps: Optional[str] = None
    intel_email_sent_at: Optional[datetime] = None
    manually_linked: Optional[bool] = None

    # MeetingUpdate is a plain SQLModel (it does NOT inherit MeetingBase), so
    # PUT /meetings/{id} needs its own tz normalization for datetime fields.
    @field_validator("scheduled_at", "intel_email_sent_at", mode="before")
    @classmethod
    def strip_timezone(cls, v: Any) -> Optional[datetime]:
        return to_naive_utc(v)
