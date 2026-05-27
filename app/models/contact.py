from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ContactBase(SQLModel):
    first_name: str
    last_name: str
    email: Optional[str] = Field(default=None, index=True)
    email_verified: bool = False
    phone: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = None
    seniority: Optional[str] = None
    linkedin_url: Optional[str] = None
    persona: Optional[str] = Field(default=None, index=True)


class Contact(ContactBase, table=True):
    __tablename__ = "contacts"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    company_id: Optional[UUID] = Field(default=None, foreign_key="companies.id", index=True)
    # Account sourcing enrichment fields
    enriched_at: Optional[datetime] = None
    enrichment_data: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    persona_type: Optional[str] = None  # champion | buyer | evaluator | blocker
    assigned_to_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)  # AE
    assigned_rep_email: Optional[str] = None
    sdr_id: Optional[UUID] = Field(default=None, index=True)  # SDR
    sdr_name: Optional[str] = None
    outreach_lane: Optional[str] = Field(default=None, index=True)
    sequence_status: Optional[str] = Field(default=None, index=True)
    instantly_status: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    warm_intro_strength: Optional[int] = None
    warm_intro_path: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    conversation_starter: Optional[str] = None
    personalization_notes: Optional[str] = None
    talking_points: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    # Per-channel tracking
    email_open_count: int = Field(default=0)
    email_click_count: int = Field(default=0)
    email_last_opened_at: Optional[datetime] = None
    call_status: Optional[str] = None   # none | attempted | connected | voicemail | callback
    call_disposition: Optional[str] = None  # interested | not_interested | callback | wrong_number | no_answer
    call_notes: Optional[str] = None
    call_last_at: Optional[datetime] = None
    linkedin_status: Optional[str] = None  # none | sent | accepted | replied
    linkedin_last_at: Optional[datetime] = None
    timezone: Optional[str] = None
    # Rep-selected follow-up timestamp for "interested_follow_up_required" /
    # "call_back_later_rescheduled" dispositions. Auto-cleared by
    # apply_call_disposition_effects when disposition leaves that bucket.
    next_followup_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ContactCreate(ContactBase):
    company_id: Optional[UUID] = None
    persona_type: Optional[str] = None


class ContactRead(ContactBase):
    id: UUID
    company_id: Optional[UUID] = None
    company_name: Optional[str] = None  # populated via SQL JOIN in ContactRepository
    enriched_at: Optional[datetime] = None
    enrichment_data: Optional[Any] = None
    persona_type: Optional[str] = None
    assigned_to_id: Optional[UUID] = None    # AE
    assigned_to_name: Optional[str] = None   # populated via JOIN
    assigned_rep_email: Optional[str] = None
    sdr_id: Optional[UUID] = None            # SDR
    sdr_name: Optional[str] = None           # populated via JOIN
    outreach_lane: Optional[str] = None
    sequence_status: Optional[str] = None
    instantly_status: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    warm_intro_strength: Optional[int] = None
    warm_intro_path: Optional[Any] = None
    conversation_starter: Optional[str] = None
    personalization_notes: Optional[str] = None
    talking_points: Optional[Any] = None
    tracking_stage: Optional[str] = None
    tracking_summary: Optional[str] = None
    tracking_score: Optional[int] = None
    tracking_label: Optional[str] = None
    tracking_last_activity_at: Optional[datetime] = None
    # Per-channel tracking
    email_open_count: int = 0
    email_click_count: int = 0
    email_last_opened_at: Optional[datetime] = None
    call_status: Optional[str] = None
    call_disposition: Optional[str] = None
    call_notes: Optional[str] = None
    call_last_at: Optional[datetime] = None
    linkedin_status: Optional[str] = None
    linkedin_last_at: Optional[datetime] = None
    timezone: Optional[str] = None
    next_followup_at: Optional[datetime] = None
    # Computed aggregate: count of activities of type='call' for this contact.
    # Populated by ContactRepository.list_with_company_name; never written by clients.
    call_attempt_count: int = 0
    created_at: datetime
    updated_at: datetime


class ContactUpdate(SQLModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    email_verified: Optional[bool] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    seniority: Optional[str] = None
    linkedin_url: Optional[str] = None
    persona: Optional[str] = None
    company_id: Optional[UUID] = None
    enriched_at: Optional[datetime] = None
    enrichment_data: Optional[Any] = None
    persona_type: Optional[str] = None
    assigned_to_id: Optional[UUID] = None
    assigned_rep_email: Optional[str] = None
    sdr_id: Optional[UUID] = None
    sdr_name: Optional[str] = None
    outreach_lane: Optional[str] = None
    sequence_status: Optional[str] = None
    instantly_status: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    warm_intro_strength: Optional[int] = None
    warm_intro_path: Optional[Any] = None
    conversation_starter: Optional[str] = None
    personalization_notes: Optional[str] = None
    talking_points: Optional[Any] = None
    email_open_count: Optional[int] = None
    email_click_count: Optional[int] = None
    email_last_opened_at: Optional[datetime] = None
    call_status: Optional[str] = None
    call_disposition: Optional[str] = None
    call_notes: Optional[str] = None
    call_last_at: Optional[datetime] = None
    linkedin_status: Optional[str] = None
    linkedin_last_at: Optional[datetime] = None
    timezone: Optional[str] = None
    next_followup_at: Optional[datetime] = None
