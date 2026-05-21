"""Drive → Qdrant indexing pipeline for Zippy.

Given a user (or admin) with a selected Drive folder:

    1. Recursively list every file under that folder via the Drive API.
    2. Skip files that haven't changed since the last index (based on
       ``modifiedTime`` tracked in ``indexed_drive_files``).
    3. Download + extract text for anything new or stale.
    4. Chunk, embed, and upsert into Qdrant.
    5. Update the Postgres tracker so the next run is cheap.

The indexer is *idempotent* — rerunning it on the same folder never produces
duplicate chunks because Qdrant point IDs are derived deterministically from
(source_id, chunk_index).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.embeddings import get_embeddings_client
from app.clients.google_drive import (
    download_file_bytes,
    list_files_in_folder_recursive,
)
from app.clients.qdrant import KnowledgeChunk, get_qdrant_client
from app.config import settings
from app.models.user_email_connection import UserEmailConnection
from app.models.zippy import IndexedDriveFile
from app.services.chunker import chunk_text
from app.services.text_extraction import extract_text, is_supported

logger = logging.getLogger(__name__)


@dataclass
class IndexReport:
    """Summary returned to the caller (and surfaced in the UI)."""

    folder_id: str
    folder_name: str
    scope: str  # "admin" | "user"
    files_scanned: int = 0
    files_indexed: int = 0
    files_skipped_unchanged: int = 0
    files_skipped_unsupported: int = 0
    files_failed: int = 0
    chunks_written: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "folder_id": self.folder_id,
            "folder_name": self.folder_name,
            "scope": self.scope,
            "files_scanned": self.files_scanned,
            "files_indexed": self.files_indexed,
            "files_skipped_unchanged": self.files_skipped_unchanged,
            "files_skipped_unsupported": self.files_skipped_unsupported,
            "files_failed": self.files_failed,
            "chunks_written": self.chunks_written,
            "errors": self.errors[:20],  # Cap so the response stays small.
        }


def _parse_drive_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


async def index_connection(
    session: AsyncSession,
    connection: UserEmailConnection,
    *,
    client_id: str,
    client_secret: str,
    force: bool = False,
) -> IndexReport:
    """Index the Drive folder currently selected on ``connection``.

    Parameters
    ----------
    force:
        When True, reindex every file regardless of ``drive_modified_at``.
        Useful after chunker / embedding-model upgrades.
    """
    folder_id = connection.selected_drive_folder_id
    folder_name = connection.selected_drive_folder_name or ""
    scope = "admin" if connection.is_admin_folder else "user"

    report = IndexReport(
        folder_id=folder_id or "",
        folder_name=folder_name,
        scope=scope,
    )

    if not folder_id:
        report.errors.append("No Drive folder selected on this connection.")
        return report

    # 1. Enumerate files under the folder (recursive, Drive API).
    try:
        files, updated_token = await list_files_in_folder_recursive(
            folder_id=folder_id,
            token_data=connection.token_data,
            client_id=client_id,
            client_secret=client_secret,
        )
    except Exception as exc:
        logger.exception("Drive listing failed for folder %s", folder_id)
        report.errors.append(f"Drive listing failed: {exc}")
        return report

    # Persist the refreshed token if it changed so the next sync doesn't
    # re-refresh unnecessarily.
    if updated_token and updated_token != connection.token_data:
        connection.token_data = updated_token
        session.add(connection)
        await session.flush()

    report.files_scanned = len(files)

    # 2. Fetch what we already have indexed for this scope so we can skip
    # unchanged files and track deletions. Use drive_folder_id to scope.
    stmt = select(IndexedDriveFile).where(
        IndexedDriveFile.owner_user_id == connection.user_id,
        IndexedDriveFile.is_admin == connection.is_admin_folder,
        IndexedDriveFile.drive_folder_id == folder_id,
    )
    result = await session.execute(stmt)
    existing_by_id: dict[str, IndexedDriveFile] = {
        row.drive_file_id: row for row in result.scalars().all()
    }

    # Lazy-init: only connect to Qdrant / embeddings when there are actually
    # files to process. An empty folder should return a clean empty report,
    # not a 500 because Qdrant happened to be unreachable at that moment.
    embeddings_client = get_embeddings_client()
    qdrant = get_qdrant_client()

    if files:
        try:
            await qdrant.ensure_collection()
        except Exception as exc:
            logger.exception("Qdrant collection bootstrap failed for folder %s", folder_id)
            report.errors.append(
                f"Qdrant is unreachable or misconfigured: {exc}. "
                "Check QDRANT_URL and QDRANT_API_KEY in your environment."
            )
            return report

    live_file_ids: set[str] = set()

    for file_meta in files:
        drive_file_id = file_meta.get("id")
        if not drive_file_id:
            continue
        live_file_ids.add(drive_file_id)

        name = file_meta.get("name", "")
        mime = file_meta.get("mimeType", "")
        web_view = file_meta.get("webViewLink", "")
        size = int(file_meta["size"]) if file_meta.get("size") else None
        modified = _parse_drive_iso(file_meta.get("modifiedTime"))

        existing = existing_by_id.get(drive_file_id)

        # Skip files we've seen if Drive's modifiedTime hasn't advanced.
        if (
            not force
            and existing
            and existing.drive_modified_at is not None
            and modified is not None
            and existing.drive_modified_at >= modified
            and not existing.last_error
        ):
            report.files_skipped_unchanged += 1
            continue

        # Skip unsupported mime types without marking as failed.
        if not is_supported(mime) and not mime.startswith("application/vnd.google-apps."):
            report.files_skipped_unsupported += 1
            _touch_record(
                session,
                existing,
                connection,
                folder_id,
                file_meta,
                modified,
                size,
                chunk_count=0,
                error="Unsupported file type",
            )
            continue

        # 3. Download + extract text.
        try:
            raw, effective_mime, updated_token = await download_file_bytes(
                file_id=drive_file_id,
                mime_type=mime,
                token_data=connection.token_data,
                client_id=client_id,
                client_secret=client_secret,
            )
            if updated_token and updated_token != connection.token_data:
                connection.token_data = updated_token
                session.add(connection)
                await session.flush()
            if not raw:
                report.files_skipped_unsupported += 1
                _touch_record(
                    session,
                    existing,
                    connection,
                    folder_id,
                    file_meta,
                    modified,
                    size,
                    chunk_count=0,
                    error="Drive returned no content",
                )
                continue

            text = await asyncio.to_thread(extract_text, raw, effective_mime, name)
        except Exception as exc:
            logger.exception("Failed to download/extract %s", drive_file_id)
            report.files_failed += 1
            report.errors.append(f"{name}: {exc}")
            _touch_record(
                session,
                existing,
                connection,
                folder_id,
                file_meta,
                modified,
                size,
                chunk_count=(existing.qdrant_chunk_count if existing else 0),
                error=str(exc),
            )
            continue

        if not text.strip():
            report.files_skipped_unsupported += 1
            _touch_record(
                session,
                existing,
                connection,
                folder_id,
                file_meta,
                modified,
                size,
                chunk_count=0,
                error="No extractable text",
            )
            continue

        # 4. Chunk + embed.
        chunks = chunk_text(text)
        if not chunks:
            continue
        try:
            vectors = await embeddings_client.embed([c.text for c in chunks])
        except Exception as exc:
            logger.exception("Embedding failed for %s", drive_file_id)
            report.files_failed += 1
            report.errors.append(f"{name}: embedding failed: {exc}")
            _touch_record(
                session,
                existing,
                connection,
                folder_id,
                file_meta,
                modified,
                size,
                chunk_count=(existing.qdrant_chunk_count if existing else 0),
                error=f"Embedding failed: {exc}",
            )
            continue

        # If we're re-indexing an existing file, drop its old chunks first so
        # the chunk count stays accurate when the doc shrinks.
        if existing and existing.qdrant_chunk_count:
            await qdrant.delete_by_source(drive_file_id)

        knowledge_chunks = [
            KnowledgeChunk(
                text=chunk.text,
                vector=vector,
                source_id=drive_file_id,
                source_type="drive_file",
                source_name=name,
                chunk_index=chunk.index,
                owner_user_id=str(connection.user_id),
                is_admin=connection.is_admin_folder,
                mime_type=mime,
                drive_url=web_view,
            )
            for chunk, vector in zip(chunks, vectors)
        ]

        try:
            written = await qdrant.upsert(knowledge_chunks)
        except Exception as exc:
            logger.exception("Qdrant upsert failed for %s", drive_file_id)
            report.files_failed += 1
            report.errors.append(f"{name}: qdrant upsert failed: {exc}")
            _touch_record(
                session,
                existing,
                connection,
                folder_id,
                file_meta,
                modified,
                size,
                chunk_count=(existing.qdrant_chunk_count if existing else 0),
                error=f"Qdrant upsert failed: {exc}",
            )
            continue

        report.files_indexed += 1
        report.chunks_written += written
        _touch_record(
            session,
            existing,
            connection,
            folder_id,
            file_meta,
            modified,
            size,
            chunk_count=written,
            error=None,
        )

    # 5. Remove tracker rows + vectors for files that disappeared from Drive.
    for drive_file_id, existing in existing_by_id.items():
        if drive_file_id in live_file_ids:
            continue
        try:
            await qdrant.delete_by_source(drive_file_id)
        except Exception as exc:
            logger.warning("Failed to delete qdrant points for %s: %s", drive_file_id, exc)
        await session.delete(existing)

    await session.commit()
    return report


def _touch_record(
    session: AsyncSession,
    existing: Optional[IndexedDriveFile],
    connection: UserEmailConnection,
    folder_id: str,
    file_meta: dict,
    modified: Optional[datetime],
    size: Optional[int],
    *,
    chunk_count: int,
    error: Optional[str],
) -> None:
    """Insert or update the per-file tracker row."""
    now = datetime.utcnow()
    if existing is None:
        row = IndexedDriveFile(
            owner_user_id=connection.user_id,
            is_admin=connection.is_admin_folder,
            drive_file_id=file_meta.get("id", ""),
            drive_folder_id=folder_id,
            name=file_meta.get("name", ""),
            mime_type=file_meta.get("mimeType", ""),
            web_view_link=file_meta.get("webViewLink", "") or "",
            size_bytes=size,
            drive_modified_at=modified,
            qdrant_chunk_count=chunk_count,
            last_indexed_at=now if error is None else None,
            last_error=error,
            updated_at=now,
        )
        session.add(row)
    else:
        existing.name = file_meta.get("name", existing.name)
        existing.mime_type = file_meta.get("mimeType", existing.mime_type)
        existing.web_view_link = file_meta.get("webViewLink", existing.web_view_link) or ""
        existing.size_bytes = size
        existing.drive_modified_at = modified
        existing.qdrant_chunk_count = chunk_count
        existing.last_indexed_at = now if error is None else existing.last_indexed_at
        existing.last_error = error
        existing.updated_at = now
        session.add(existing)


async def reset_scope(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    is_admin: bool,
) -> None:
    """Drop every indexed file + Qdrant chunk for a scope — e.g. folder changed."""
    qdrant = get_qdrant_client()
    await qdrant.delete_by_owner(str(owner_user_id), is_admin=is_admin)

    stmt = select(IndexedDriveFile).where(
        IndexedDriveFile.owner_user_id == owner_user_id,
        IndexedDriveFile.is_admin == is_admin,
    )
    result = await session.execute(stmt)
    for row in result.scalars().all():
        await session.delete(row)
    await session.commit()
