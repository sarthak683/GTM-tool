from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


# Status values — kept narrow on purpose so the frontend can switch on them
# without a hash-map. Any new state needs a code change on both sides.
CALL_RECORDING_STATUSES = (
    "uploaded",       # POST landed, audio on disk in worker /tmp
    "transcribing",   # Whisper call in flight
    "classifying",    # Claude disposition classification in flight
    "ready",          # transcript + ai_disposition populated, audio deleted
    "failed",         # transcription or classification errored
)


class CallRecording(SQLModel, table=True):
    __tablename__ = "call_recordings"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    contact_id: UUID = Field(foreign_key="contacts.id", index=True)
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id")
    status: str = Field(default="uploaded", index=True)
    consent_acknowledged_at: Optional[datetime] = None
    audio_duration_seconds: Optional[int] = None
    audio_size_bytes: Optional[int] = None
    transcript: Optional[str] = None
    # AI outputs — all nullable so a partial run is still queryable.
    ai_disposition: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_summary: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CallRecordingRead(SQLModel):
    id: UUID
    contact_id: UUID
    created_by_id: Optional[UUID] = None
    status: str
    consent_acknowledged_at: Optional[datetime] = None
    audio_duration_seconds: Optional[int] = None
    audio_size_bytes: Optional[int] = None
    transcript: Optional[str] = None
    ai_disposition: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_summary: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
