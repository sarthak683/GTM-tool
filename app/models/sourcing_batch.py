"""
Sourcing batch — tracks each CSV upload session for account sourcing.

Each batch contains metadata about the import: filename, row counts,
processing status. Companies are linked back via sourcing_batch_id FK.
"""
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SourcingBatch(SQLModel, table=True):
    __tablename__ = "sourcing_batches"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    filename: str
    status: str = "pending"  # pending | processing | completed | failed
    total_rows: int = 0
    processed_rows: int = 0
    created_companies: int = 0
    skipped_rows: int = 0
    failed_rows: int = 0
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    created_by_name: Optional[str] = None
    created_by_email: Optional[str] = None
    meta: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    error_log: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SourcingBatchRead(SQLModel):
    id: UUID
    filename: str
    status: str
    total_rows: int
    processed_rows: int
    created_companies: int
    skipped_rows: int
    failed_rows: int
    created_by_id: Optional[UUID] = None
    created_by_name: Optional[str] = None
    created_by_email: Optional[str] = None
    meta: Optional[Any] = None
    error_log: Optional[Any] = None
    current_stage: Optional[str] = None
    progress_message: Optional[str] = None
    eta_seconds: Optional[int] = None
    contacts_found: Optional[int] = None
    verdict_summary: Optional[Any] = None
    requires_confirmation: bool = False
    auto_started: bool = False
    created_at: datetime
    updated_at: datetime
