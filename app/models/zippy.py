"""Zippy persistence: conversations, messages, indexed file tracking.

We keep chat history in Postgres so users can resume a session and so we have
an audit trail of what the agent answered with which sources. Vector data
itself lives in Qdrant — Postgres only stores pointers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ZippyConversation(SQLModel, table=True):
    __tablename__ = "zippy_conversations"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    title: str = Field(default="New conversation")
    # Optional short summary kept fresh by the agent every few turns — shown
    # in the sidebar next to the title.
    summary: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    is_archived: bool = Field(default=False)
    is_pinned: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ZippyMessage(SQLModel, table=True):
    __tablename__ = "zippy_messages"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    conversation_id: UUID = Field(foreign_key="zippy_conversations.id", index=True)
    # "user" | "assistant" | "system"
    role: str = Field(index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    # Citations attached to an assistant message: list of source dicts
    # ({source_id, source_name, drive_url, snippet, score}).
    citations: Optional[list] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    # Any generated artifacts (e.g. {type: "mom_docx", path: "...", filename: "..."})
    artifacts: Optional[list] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    # Tool-use trail for debugging: list of {tool, args, result_summary}
    tool_trace: Optional[list] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class IndexedDriveFile(SQLModel, table=True):
    """
    One row per Drive file we've indexed into Qdrant.

    Enables cheap delta sync: compare ``drive_modified_at`` on each run and
    skip unchanged files. ``qdrant_chunk_count`` lets us clean up stale
    chunks when a file's length shrinks.
    """
    __tablename__ = "indexed_drive_files"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    # Scope: the user who owns this indexed copy. For admin-folder rows,
    # owner_user_id is the admin and is_admin=True.
    owner_user_id: UUID = Field(foreign_key="users.id", index=True)
    is_admin: bool = Field(default=False, index=True)

    drive_file_id: str = Field(index=True)
    drive_folder_id: str = Field(index=True)
    name: str
    mime_type: str
    web_view_link: str = Field(default="")
    # BIGINT: some Drive files (videos, datasets) exceed INT32 (~2.1 GB cap),
    # and asyncpg refuses to bind them as int4. BIGINT gives us ~9 exabytes.
    size_bytes: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    drive_modified_at: Optional[datetime] = Field(default=None)

    qdrant_chunk_count: int = Field(default=0)
    last_indexed_at: Optional[datetime] = Field(default=None, index=True)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    extra: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ZippyConversationRead(SQLModel):
    id: UUID
    title: str
    summary: Optional[str] = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class ZippyMessageRead(SQLModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    citations: Optional[list] = None
    artifacts: Optional[list] = None
    created_at: datetime


class ZippyChatRequest(SQLModel):
    conversation_id: Optional[UUID] = None
    message: str
    # Limit retrieval to these Drive file IDs (used by "@file" references in UI)
    source_ids: Optional[list[str]] = None


class IndexedDriveFileRead(SQLModel):
    id: UUID
    drive_file_id: str
    name: str
    mime_type: str
    web_view_link: str
    size_bytes: Optional[int] = None
    qdrant_chunk_count: int
    last_indexed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    is_admin: bool
