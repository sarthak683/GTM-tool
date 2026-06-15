from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ActivityBase(SQLModel):
    type: str = Field(index=True)  # email, call, meeting, note, transcript, visit
    source: Optional[str] = None  # instantly, fireflies, rb2b, manual
    medium: Optional[str] = None  # email, call, linkedin, whatsapp, in_person, sms, other
    # Plain str here — sa_column only goes on the table class below
    content: Optional[str] = None
    ai_summary: Optional[str] = None


class Activity(ActivityBase, table=True):
    __tablename__ = "activities"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    deal_id: Optional[UUID] = Field(default=None, foreign_key="deals.id", index=True)
    contact_id: Optional[UUID] = Field(default=None, foreign_key="contacts.id", index=True)
    external_source: Optional[str] = Field(default=None, index=True)
    external_source_id: Optional[str] = Field(default=None, index=True)
    # Override inherited str fields with proper Text columns
    content: Optional[str] = Field(default=None, sa_column=Column(Text))
    ai_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    event_metadata: Optional[Any] = Field(default=None, sa_column=Column("metadata", JSONB))
    medium: Optional[str] = None  # email, call, linkedin, whatsapp, in_person, sms, other
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # Call-specific fields (populated by Aircall webhook handler)
    call_id: Optional[str] = Field(default=None, index=True)
    call_duration: Optional[int] = None        # seconds
    call_outcome: Optional[str] = None         # answered | missed | voicemail | failed
    recording_url: Optional[str] = Field(default=None, sa_column=Column("recording_url", Text))
    aircall_user_name: Optional[str] = None    # agent name from Aircall
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id")

    # Email-specific fields (populated by Gmail inbox sync)
    email_message_id: Optional[str] = Field(default=None, index=True)  # RFC Message-ID (dedup key)
    email_subject: Optional[str] = None
    email_from: Optional[str] = None           # sender address
    email_to: Optional[str] = Field(default=None, sa_column=Column("email_to", Text))  # comma-separated
    email_cc: Optional[str] = Field(default=None, sa_column=Column("email_cc", Text))  # comma-separated


class ActivityCreate(ActivityBase):
    deal_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    event_metadata: Optional[Any] = None
    created_by_id: Optional[UUID] = None
    call_id: Optional[str] = None
    call_duration: Optional[int] = None
    call_outcome: Optional[str] = None
    recording_url: Optional[str] = None
    aircall_user_name: Optional[str] = None
    email_message_id: Optional[str] = None
    email_subject: Optional[str] = None
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    email_cc: Optional[str] = None


class ActivityRead(ActivityBase):
    id: UUID
    deal_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    event_metadata: Optional[Any] = None
    created_at: datetime
    call_id: Optional[str] = None
    call_duration: Optional[int] = None
    call_outcome: Optional[str] = None
    recording_url: Optional[str] = None
    aircall_user_name: Optional[str] = None
    created_by_id: Optional[UUID] = None
    user_name: Optional[str] = None  # joined from users table
    email_message_id: Optional[str] = None
    email_subject: Optional[str] = None
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    email_cc: Optional[str] = None


class ActivityUpdate(SQLModel):
    type: Optional[str] = None
    source: Optional[str] = None
    medium: Optional[str] = None
    content: Optional[str] = None
    ai_summary: Optional[str] = None
    deal_id: Optional[UUID] = None
    contact_id: Optional[UUID] = None
    event_metadata: Optional[Any] = None
    call_id: Optional[str] = None
    call_duration: Optional[int] = None
    call_outcome: Optional[str] = None
    recording_url: Optional[str] = None
    aircall_user_name: Optional[str] = None
    email_message_id: Optional[str] = None
    email_subject: Optional[str] = None
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    email_cc: Optional[str] = None
