"""Zippy persistence: conversations, messages, indexed file tracking.

We keep chat history in Postgres so users can resume a session and so we have
an audit trail of what the agent answered with which sources. Vector data
itself lives in Qdrant — Postgres only stores pointers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, LargeBinary, Text
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


class ZippyGeneratedDoc(SQLModel, table=True):
    """A Zippy-generated file (.docx/.xlsx/.pptx) stored as bytes in Postgres.

    Why the database and not the filesystem
    ---------------------------------------
    These files used to be written to ``/app/storage/zippy_outputs`` inside the
    container and served through a ``StaticFiles`` mount. Production runs two
    backend replicas with no shared volume, so the file only ever existed on the
    pod that generated it: a download link produced by pod A returned a bare 404
    roughly half the time (whenever the request was balanced onto pod B), and
    *every* generated file was destroyed by each restart, redeploy or reschedule
    because the container's writable layer is ephemeral.

    Postgres is already shared by every replica and already backed up, so a row
    here is reachable from any pod and survives restarts. The alternative — a
    ReadWriteMany PVC — would have coupled this fix to provisioning storage
    infrastructure for a feature that produces a handful of few-hundred-KB files.

    ``token`` is a capability, not an id
    ------------------------------------
    The download route is reached by a plain ``<a href target="_blank">`` in the
    chat transcript, which cannot carry the app's ``Authorization`` header, so
    the route cannot require a session. The token is therefore the credential:
    256 bits from ``secrets.token_urlsafe``, unguessable, and never derived from
    the filename. That is the same posture as the static mount it replaces, but
    strictly stronger — the old URLs were guessable slugs with 24 bits of
    randomness.

    ``user_id`` is intentionally NOT a foreign key: it is provenance for audit
    and cleanup only, and a nullable FK would make deleting a user fail on a
    stale document row.
    """

    __tablename__ = "zippy_generated_docs"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    # Unguessable capability token that appears in the download URL.
    # UNIQUE, not separately indexed: the unique constraint's own btree is what
    # the download lookup uses, so a second index would be pure overhead.
    token: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    user_id: Optional[UUID] = Field(default=None, index=True)
    # "mom" | "nda_in" | "roi" | "poc_ppt" | "generic_docx" | ...
    kind: str = Field(default="")
    # User-facing download name, e.g. "MOM-Acme-2026-08-17-a1b2c3.docx".
    filename: str
    content_type: str = Field(
        default="application/octet-stream",
    )
    size_bytes: int = Field(default=0)
    data: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    # Past this instant the row is dead: the route reports it as expired and the
    # nightly purge task deletes it. Indexed because the purge scans on it.
    expires_at: datetime = Field(default_factory=datetime.utcnow, index=True)


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
