"""
Google Drive endpoints — folder picker + folder selection.

The OAuth flow that connects a user's Gmail already requests
`drive.readonly` scope (see app/services/gmail_oauth.py), so any user with
an active UserEmailConnection can list their Drive folders through these
endpoints without a second OAuth step.

Endpoints:
  GET  /drive/folders                  — list top-level or child folders
  GET  /drive/folders/search           — name-based search
  POST /drive/folder/select            — save the user's chosen folder
  POST /drive/folder/select-admin      — save the admin's chosen folder (admin-only)
  GET  /drive/folder/current           — return the folder currently selected for this user
  GET  /drive/folder/admin             — return the admin-selected folder (readable by all)
  POST /drive/folder/clear             — clear the current user's selection
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlmodel import select as sm_select

from app.clients import google_drive
from app.config import settings
from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.core.exceptions import NotFoundError, ValidationError
from app.models.user_email_connection import UserEmailConnection

router = APIRouter(prefix="/drive", tags=["drive"])


# ── Request / response schemas ────────────────────────────────────────────────


class DriveFolder(BaseModel):
    id: str
    name: str
    parents: list[str] = []
    modified_time: Optional[str] = None
    owned_by_me: bool = False
    shared: bool = False
    drive_id: Optional[str] = None


class DriveFolderList(BaseModel):
    folders: list[DriveFolder]
    parent_id: Optional[str] = None


class SelectFolderRequest(BaseModel):
    folder_id: str
    folder_name: Optional[str] = None  # Optional — we can re-fetch if not provided


class SelectedFolder(BaseModel):
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    is_admin_folder: bool = False
    owner_email: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _as_drive_folder(raw: dict[str, Any]) -> DriveFolder:
    return DriveFolder(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        parents=raw.get("parents") or [],
        modified_time=raw.get("modifiedTime"),
        owned_by_me=bool(raw.get("ownedByMe")),
        shared=bool(raw.get("shared")),
        drive_id=raw.get("driveId"),
    )


async def _get_active_connection(session, user_id) -> UserEmailConnection:
    result = await session.execute(
        sm_select(UserEmailConnection).where(
            UserEmailConnection.user_id == user_id,
            UserEmailConnection.is_active == True,  # noqa: E712
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise NotFoundError(
            "No active Gmail/Drive connection. Connect your Google account first."
        )
    return connection


async def _persist_refreshed_token(session, connection: UserEmailConnection, updated_token: dict) -> None:
    """If the Drive client refreshed the access token, persist it back."""
    if updated_token and updated_token != connection.token_data:
        connection.token_data = updated_token
        connection.updated_at = datetime.utcnow()
        session.add(connection)
        await session.commit()


# ── List / search folders ─────────────────────────────────────────────────────


@router.get("/folders", response_model=DriveFolderList)
async def list_drive_folders(
    session: DBSession,
    current_user: CurrentUser,
    parent_id: Optional[str] = Query(
        default=None,
        description="If set, only return folders directly inside this parent. Omit for top-level.",
    ),
):
    """
    List Drive folders visible to the current user.

    Call without `parent_id` to get the top-level list, then pass a folder's
    ID as `parent_id` to drill into it (standard tree-picker UX).
    """
    connection = await _get_active_connection(session, current_user.id)

    try:
        folders, updated_token = await google_drive.list_folders(
            token_data=connection.token_data,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            parent_id=parent_id,
        )
    except PermissionError as exc:
        raise ValidationError(str(exc))

    await _persist_refreshed_token(session, connection, updated_token)

    return DriveFolderList(
        folders=[_as_drive_folder(f) for f in folders],
        parent_id=parent_id,
    )


@router.get("/folders/search", response_model=DriveFolderList)
async def search_drive_folders(
    session: DBSession,
    current_user: CurrentUser,
    q: str = Query(..., min_length=1, description="Folder name to search for"),
):
    """Search folders by name (contains match)."""
    connection = await _get_active_connection(session, current_user.id)

    try:
        folders, updated_token = await google_drive.search_folders(
            query=q,
            token_data=connection.token_data,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
        )
    except PermissionError as exc:
        raise ValidationError(str(exc))

    await _persist_refreshed_token(session, connection, updated_token)

    return DriveFolderList(folders=[_as_drive_folder(f) for f in folders])


# ── Select / clear folder ─────────────────────────────────────────────────────


async def _resolve_folder_name(
    connection: UserEmailConnection,
    folder_id: str,
    provided_name: Optional[str],
) -> str:
    """If name wasn't provided by the UI, fetch it from Drive."""
    if provided_name:
        return provided_name

    meta, _updated = await google_drive.get_folder_metadata(
        folder_id=folder_id,
        token_data=connection.token_data,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
    )
    if not meta:
        raise NotFoundError(f"Folder {folder_id} not found or not accessible")
    return meta.get("name") or folder_id


@router.post("/folder/select", response_model=SelectedFolder)
async def select_user_folder(
    payload: SelectFolderRequest,
    session: DBSession,
    current_user: CurrentUser,
):
    """Save the current user's personal Drive folder selection."""
    connection = await _get_active_connection(session, current_user.id)

    folder_name = await _resolve_folder_name(connection, payload.folder_id, payload.folder_name)

    connection.selected_drive_folder_id = payload.folder_id
    connection.selected_drive_folder_name = folder_name
    # A normal user's selection is never the admin folder — that has its own endpoint.
    connection.is_admin_folder = False
    connection.updated_at = datetime.utcnow()
    session.add(connection)
    await session.commit()
    await session.refresh(connection)

    return SelectedFolder(
        folder_id=connection.selected_drive_folder_id,
        folder_name=connection.selected_drive_folder_name,
        is_admin_folder=False,
        owner_email=connection.email_address,
    )


@router.post("/folder/select-admin", response_model=SelectedFolder)
async def select_admin_folder(
    payload: SelectFolderRequest,
    session: DBSession,
    admin: AdminUser,
):
    """
    Save the admin's shared Drive folder.

    This folder is treated as the workspace-wide source — all users can read
    what's in it. Only one connection at a time can be flagged as the admin
    folder; we clear any previous flag before setting this one.
    """
    connection = await _get_active_connection(session, admin.id)

    folder_name = await _resolve_folder_name(connection, payload.folder_id, payload.folder_name)

    # Unset any previous admin-folder flag (on any user's connection).
    existing = await session.execute(
        sm_select(UserEmailConnection).where(UserEmailConnection.is_admin_folder == True)  # noqa: E712
    )
    for row in existing.scalars().all():
        if row.id != connection.id:
            row.is_admin_folder = False
            row.updated_at = datetime.utcnow()
            session.add(row)

    connection.selected_drive_folder_id = payload.folder_id
    connection.selected_drive_folder_name = folder_name
    connection.is_admin_folder = True
    connection.updated_at = datetime.utcnow()
    session.add(connection)
    await session.commit()
    await session.refresh(connection)

    return SelectedFolder(
        folder_id=connection.selected_drive_folder_id,
        folder_name=connection.selected_drive_folder_name,
        is_admin_folder=True,
        owner_email=connection.email_address,
    )


@router.get("/folder/current", response_model=SelectedFolder)
async def get_current_user_folder(session: DBSession, current_user: CurrentUser):
    """Return the folder currently selected by the logged-in user."""
    result = await session.execute(
        sm_select(UserEmailConnection).where(
            UserEmailConnection.user_id == current_user.id,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        return SelectedFolder()
    return SelectedFolder(
        folder_id=connection.selected_drive_folder_id,
        folder_name=connection.selected_drive_folder_name,
        is_admin_folder=connection.is_admin_folder,
        owner_email=connection.email_address,
    )


@router.get("/folder/admin", response_model=SelectedFolder)
async def get_admin_folder(session: DBSession, current_user: CurrentUser):
    """
    Return the admin-selected shared folder (if any).

    Readable by any authenticated user so the frontend can show the shared
    workspace folder alongside the user's personal one.
    """
    result = await session.execute(
        sm_select(UserEmailConnection).where(
            UserEmailConnection.is_admin_folder == True,  # noqa: E712
            UserEmailConnection.is_active == True,  # noqa: E712
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        return SelectedFolder()
    return SelectedFolder(
        folder_id=connection.selected_drive_folder_id,
        folder_name=connection.selected_drive_folder_name,
        is_admin_folder=True,
        owner_email=connection.email_address,
    )


@router.post("/folder/clear", response_model=SelectedFolder)
async def clear_user_folder(session: DBSession, current_user: CurrentUser):
    """Clear the current user's folder selection (does not touch admin folder)."""
    result = await session.execute(
        sm_select(UserEmailConnection).where(
            UserEmailConnection.user_id == current_user.id,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        return SelectedFolder()

    # If this connection is the admin folder, require admin to use /folder/select-admin
    # with a different folder to replace it, rather than clearing silently.
    if connection.is_admin_folder and current_user.role != "admin":
        raise ValidationError("Admin folder can only be changed by an admin")

    connection.selected_drive_folder_id = None
    connection.selected_drive_folder_name = None
    connection.is_admin_folder = False
    connection.updated_at = datetime.utcnow()
    session.add(connection)
    await session.commit()
    return SelectedFolder()
