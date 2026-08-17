"""Durable, replica-shared storage for Zippy's generated documents.

Background
----------
Every generator in this package renders its file to a real path on disk —
``python-docx`` / ``openpyxl`` / ``python-pptx`` all want a filesystem target,
and the Google Drive upload reads the bytes straight back off it. That part is
fine. What was broken is what happened *next*: the file stayed on disk and was
published through a ``StaticFiles`` mount at ``/zippy_outputs``.

Production runs two backend replicas with no shared volume. So the file existed
only on the pod that generated it, which made the download link a coin flip, and
the container's writable layer is ephemeral, so every restart or redeploy wiped
the lot. (Verified: both prod pods' output directories were empty minutes after
a routine deploy.)

Why the bytes must be kept at all
---------------------------------
The obvious stateless fix — stream the document in the response, or regenerate
it on download — does not fit this flow:

  * The document is produced inside the agent's tool-use loop. What the user
    receives is a chat message; the link is an artifact chip persisted in
    ``zippy_messages.artifacts`` and clicked whenever they scroll back to it.
    There is no response to attach bytes to.
  * Regeneration is not reproducible. Every generator calls Claude to rewrite a
    Drive template, and the inputs that drove it (transcript, attendees, raw
    context) are tool arguments that are never persisted. Re-running would cost
    another model call and produce a *different* document.

So the bytes have to outlive the request, and they have to be reachable from
either replica. They go in Postgres: already shared, already backed up, no new
infrastructure, and the volume is trivial (a handful of few-hundred-KB files).

Retention
---------
Rows carry an ``expires_at`` and are swept by
``app.tasks.zippy_documents.purge_expired_zippy_documents``. An expired token is
reported honestly as "expired" rather than as a bare 404, so the user is told to
ask Zippy to regenerate instead of staring at a dead link.

Import weight
-------------
This module deliberately imports nothing from the document generators — only the
model and the session factory. That keeps it safe for the Celery worker (whose
``include`` list does not contain ``zippy_docs``) to import for the purge task,
without dragging in ``python-docx`` and friends.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import delete as sa_delete
from sqlmodel import select as sm_select

from app.database import AsyncSessionLocal as async_session
from app.models.zippy import ZippyGeneratedDoc

logger = logging.getLogger(__name__)


# Public URL prefix for a stored document. Relative on purpose: the frontend
# chip prepends its API base to it (see ZippyMessageBubble.tsx), so an absolute
# URL here would produce a mangled "http://apihttps://..." href.
DOCUMENT_URL_PREFIX = "/api/v1/zippy/documents"

# A generated MOM/NDA/proposal is tens to a few hundred KB; an ROI workbook or a
# PoC deck with images is larger but still small. This cap exists so a runaway
# generator can never try to push a multi-gigabyte blob through asyncpg — it is
# a guard rail, not a working limit.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

# How long a generated document stays downloadable. The link lives in chat
# history forever, but the file is only ever the *fallback* for a failed Google
# Drive upload — once a rep has had a month to click it, keeping the bytes is
# just storage with no reader. Overridable for tests and for ops.
DEFAULT_RETENTION_DAYS = 30

_CONTENT_TYPES = {
    ".docx": (
        "application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document"
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument"
        ".spreadsheetml.sheet"
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument"
        ".presentationml.presentation"
    ),
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
}


def content_type_for(filename: str) -> str:
    """Best-guess MIME type from the extension, defaulting to a binary blob."""
    suffix = Path(filename or "").suffix.lower()
    return _CONTENT_TYPES.get(suffix, "application/octet-stream")


def retention_days() -> int:
    """Retention window, overridable via ``ZIPPY_DOC_RETENTION_DAYS``.

    Read from the environment at call time rather than import time so a test (or
    an ops override) can change it without reimporting the module.
    """
    raw = os.environ.get("ZIPPY_DOC_RETENTION_DAYS")
    if raw is None:
        try:
            from app.config import settings

            return max(1, int(getattr(settings, "ZIPPY_DOC_RETENTION_DAYS", 0)) or DEFAULT_RETENTION_DAYS)
        except Exception:  # pragma: no cover - settings should always import
            return DEFAULT_RETENTION_DAYS
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        logger.warning(
            "ZIPPY_DOC_RETENTION_DAYS=%r is not an integer — using %d days",
            raw,
            DEFAULT_RETENTION_DAYS,
        )
        return DEFAULT_RETENTION_DAYS


def document_url(token: str) -> str:
    """Relative download URL for a stored document token."""
    return f"{DOCUMENT_URL_PREFIX}/{token}"


def new_token() -> str:
    """Mint a capability token for a download URL.

    ``token_urlsafe(32)`` is 256 bits. The download route is unauthenticated by
    necessity (a plain anchor in the transcript cannot send the JWT), so this
    string is the only thing standing between a URL and the document — it must
    not be derived from the filename, the user, or the timestamp.
    """
    return secrets.token_urlsafe(32)


async def store_document(
    *,
    filename: str,
    data: bytes,
    kind: str = "",
    user_id: Optional[str] = None,
    retention: Optional[int] = None,
    now: Optional[datetime] = None,
) -> str:
    """Persist ``data`` and return the capability token for its download URL.

    Raises ``ValueError`` if the payload is empty or over ``MAX_DOCUMENT_BYTES``;
    callers treat that as "no durable link" rather than failing the whole turn.
    """
    if not data:
        raise ValueError("refusing to store an empty document")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"document is {len(data)} bytes, over the "
            f"{MAX_DOCUMENT_BYTES}-byte storage cap"
        )

    created = now or datetime.utcnow()
    days = retention if retention is not None else retention_days()
    token = new_token()

    row = ZippyGeneratedDoc(
        token=token,
        user_id=_coerce_uuid(user_id),
        kind=kind or "",
        filename=filename or "document",
        content_type=content_type_for(filename),
        size_bytes=len(data),
        data=data,
        created_at=created,
        expires_at=created + timedelta(days=days),
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()

    logger.info(
        "Stored Zippy document %s (%s, %d bytes, expires %s)",
        filename,
        kind or "unknown kind",
        len(data),
        row.expires_at.isoformat(),
    )
    return token


async def load_document(token: str) -> Optional[ZippyGeneratedDoc]:
    """Fetch a stored document by token, or ``None`` if no such token exists.

    Expiry is deliberately NOT checked here: the caller needs to tell "expired"
    apart from "never existed" so it can give the user the right message.
    """
    if not token:
        return None
    async with async_session() as session:
        result = await session.execute(
            sm_select(ZippyGeneratedDoc).where(ZippyGeneratedDoc.token == token)
        )
        return result.scalars().first()


async def purge_expired(now: Optional[datetime] = None) -> int:
    """Delete every document whose ``expires_at`` has passed. Returns the count."""
    cutoff = now or datetime.utcnow()
    async with async_session() as session:
        result = await session.execute(
            sa_delete(ZippyGeneratedDoc).where(
                ZippyGeneratedDoc.expires_at < cutoff
            )
        )
        await session.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("Purged %d expired Zippy document(s)", deleted)
    return deleted


async def persist_generated_document(doc, *, user_id=None) -> Optional[str]:
    """Move a freshly generated document off local disk and into Postgres.

    Called at the single point where a ``GeneratedDocument`` becomes a link the
    user can click later (``zippy_tools._doc_to_artifact``). It reads the bytes
    the generator just wrote, stores them, rewrites ``doc.url`` to the durable
    download URL, and removes the scratch file.

    Best-effort by design. A storage failure must not fail the chat turn — the
    document has almost always been uploaded to Google Drive already, and that
    link (``doc.drive_url``) is the one the UI prefers anyway. On failure we
    clear ``doc.url`` so the artifact chip falls back to Drive instead of
    advertising a link that would 404.

    Returns the token, or ``None`` if nothing was stored.
    """
    path_str = getattr(doc, "path", "") or ""
    if not path_str:
        doc.url = ""
        return None

    path = Path(path_str)
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning(
            "Could not read generated document %s for storage: %s", path, exc
        )
        doc.url = ""
        return None

    token: Optional[str] = None
    try:
        token = await store_document(
            filename=getattr(doc, "filename", "") or path.name,
            data=data,
            kind=getattr(doc, "kind", "") or "",
            user_id=user_id,
        )
        doc.url = document_url(token)
    except Exception as exc:
        # Non-fatal: the Drive link still works, and an empty url is more honest
        # than a dead one (the frontend only renders a chip when url is truthy).
        logger.warning("Could not persist generated document %s: %s", path, exc)
        doc.url = ""

    # The scratch file has served its purpose (render target + Drive upload
    # buffer). Dropping it keeps the pod's ephemeral disk from filling up and
    # makes it obvious that nothing downstream may depend on local state.
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - unlink rarely fails
        logger.debug("Could not remove scratch file %s: %s", path, exc)

    return token


def _coerce_uuid(value):
    """Accept a UUID, a UUID string, or None — anything else becomes None."""
    if value is None:
        return None
    from uuid import UUID

    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
