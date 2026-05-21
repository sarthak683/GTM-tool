"""Knowledge base endpoints — index Drive folders + inspect what's indexed."""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select as sm_select

from app.config import settings
from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.models.user_email_connection import UserEmailConnection
from app.models.zippy import IndexedDriveFile
from app.services.knowledge_indexer import (
    IndexReport,
    index_connection,
    reset_scope,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class ReindexResponse(BaseModel):
    ok: bool
    report: dict


class IndexedFileResponse(BaseModel):
    id: UUID
    drive_file_id: str
    name: str
    mime_type: str
    web_view_link: str
    size_bytes: Optional[int] = None
    qdrant_chunk_count: int
    last_indexed_at: Optional[str] = None
    last_error: Optional[str] = None
    is_admin: bool


class IndexStatusResponse(BaseModel):
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    is_admin_folder: bool = False
    total_files: int
    successful: int
    failed: int
    skipped: int = 0
    total_chunks: int
    files: list[IndexedFileResponse]


# last_error strings the indexer emits for intentional skips (not real failures).
# These files have no extractable text (videos, images, etc.) — we record them so
# we don't retry forever, but they shouldn't be counted against the "Failed" stat.
_SKIP_ERROR_MESSAGES = {
    "Unsupported file type",
    "Drive returned no content",
    "No extractable text",
}


def _is_skip_error(err: Optional[str]) -> bool:
    return bool(err) and err in _SKIP_ERROR_MESSAGES


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_connection_for_scope(
    session,
    *,
    user_id: UUID,
    admin_scope: bool,
) -> UserEmailConnection:
    """Return the UserEmailConnection that represents the requested scope."""
    if admin_scope:
        stmt = sm_select(UserEmailConnection).where(
            UserEmailConnection.is_admin_folder.is_(True),
            UserEmailConnection.is_active.is_(True),
        )
    else:
        stmt = sm_select(UserEmailConnection).where(
            UserEmailConnection.user_id == user_id,
            UserEmailConnection.is_active.is_(True),
        )
    result = await session.execute(stmt)
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(
            status_code=404,
            detail="No connected Drive/Gmail account for this scope.",
        )
    if not connection.selected_drive_folder_id:
        raise HTTPException(
            status_code=400,
            detail="No Drive folder selected. Choose a folder in Settings first.",
        )
    return connection


def _serialise_indexed_file(row: IndexedDriveFile) -> IndexedFileResponse:
    return IndexedFileResponse(
        id=row.id,
        drive_file_id=row.drive_file_id,
        name=row.name,
        mime_type=row.mime_type,
        web_view_link=row.web_view_link,
        size_bytes=row.size_bytes,
        qdrant_chunk_count=row.qdrant_chunk_count,
        last_indexed_at=row.last_indexed_at.isoformat() if row.last_indexed_at else None,
        last_error=row.last_error,
        is_admin=row.is_admin,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/reindex", response_model=ReindexResponse)
async def reindex_user_folder(
    session: DBSession,
    current_user: CurrentUser,
    force: bool = Query(default=False, description="Reindex even unchanged files."),
) -> ReindexResponse:
    """Index (or reindex) the current user's selected Drive folder."""
    try:
        connection = await _get_connection_for_scope(
            session, user_id=current_user.id, admin_scope=False
        )
        report: IndexReport = await index_connection(
            session,
            connection,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            force=force,
        )
        return ReindexResponse(
            ok=not bool(report.errors) or report.files_indexed > 0,
            report=report.as_dict(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during user folder reindex")
        raise HTTPException(
            status_code=500,
            detail=f"Reindex failed: {exc}. Check server logs for the full traceback.",
        ) from exc


@router.post("/reindex-admin", response_model=ReindexResponse)
async def reindex_admin_folder(
    session: DBSession,
    admin_user: AdminUser,
    force: bool = Query(default=False),
) -> ReindexResponse:
    """Admin-only: reindex the shared Beacon Drive folder used by everyone."""
    try:
        connection = await _get_connection_for_scope(
            session, user_id=admin_user.id, admin_scope=True
        )
        report: IndexReport = await index_connection(
            session,
            connection,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            force=force,
        )
        return ReindexResponse(
            ok=not bool(report.errors) or report.files_indexed > 0,
            report=report.as_dict(),
        )
    except HTTPException:
        raise  # 400/404 from _get_connection_for_scope — pass through as-is
    except Exception as exc:
        logger.exception("Unexpected error during admin folder reindex")
        raise HTTPException(
            status_code=500,
            detail=(
                f"Reindex failed: {exc}. "
                "Check server logs for the full traceback."
            ),
        ) from exc


@router.get("/status", response_model=IndexStatusResponse)
async def index_status(
    session: DBSession,
    current_user: CurrentUser,
    scope: str = Query(default="user", pattern="^(user|admin)$"),
) -> IndexStatusResponse:
    """Report what's been indexed for the requested scope."""
    admin_scope = scope == "admin"
    try:
        connection = await _get_connection_for_scope(
            session, user_id=current_user.id, admin_scope=admin_scope
        )
    except HTTPException:
        # No folder selected yet — return an empty status instead of 400 so
        # the UI can render a clean "not configured" state.
        return IndexStatusResponse(
            total_files=0,
            successful=0,
            failed=0,
            skipped=0,
            total_chunks=0,
            files=[],
            is_admin_folder=admin_scope,
        )

    stmt = sm_select(IndexedDriveFile).where(
        IndexedDriveFile.is_admin == admin_scope,
    )
    if admin_scope:
        stmt = stmt.where(IndexedDriveFile.owner_user_id == connection.user_id)
    else:
        stmt = stmt.where(IndexedDriveFile.owner_user_id == current_user.id)

    stmt = stmt.order_by(IndexedDriveFile.last_indexed_at.desc())
    result = await session.execute(stmt)
    files = list(result.scalars().all())
    successful = sum(1 for f in files if not f.last_error)
    skipped = sum(1 for f in files if _is_skip_error(f.last_error))
    failed = sum(1 for f in files if f.last_error and not _is_skip_error(f.last_error))
    total_chunks = sum(f.qdrant_chunk_count for f in files)

    return IndexStatusResponse(
        folder_id=connection.selected_drive_folder_id,
        folder_name=connection.selected_drive_folder_name,
        is_admin_folder=admin_scope,
        total_files=len(files),
        successful=successful,
        failed=failed,
        skipped=skipped,
        total_chunks=total_chunks,
        files=[_serialise_indexed_file(f) for f in files],
    )


@router.post("/reset", response_model=ReindexResponse)
async def reset_user_index(
    session: DBSession,
    current_user: CurrentUser,
) -> ReindexResponse:
    """Wipe every indexed vector + tracker row for this user's scope."""
    await reset_scope(session, owner_user_id=current_user.id, is_admin=False)
    return ReindexResponse(ok=True, report={"reset": "user"})


@router.post("/reset-admin", response_model=ReindexResponse)
async def reset_admin_index(
    session: DBSession,
    admin_user: AdminUser,
) -> ReindexResponse:
    """Admin-only: clear the shared index."""
    await reset_scope(session, owner_user_id=admin_user.id, is_admin=True)
    return ReindexResponse(ok=True, report={"reset": "admin"})
