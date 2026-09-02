from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.models.settings import WorkspaceSettings
from app.models.user import User


DEFAULT_ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    "ae": {
        "crm_import": False,
        "prospect_migration": True,
        "manage_team": False,
        "run_pre_meeting_intel": True,
        "manage_reports": False,
    },
    "sdr": {
        "crm_import": False,
        "prospect_migration": True,
        "manage_team": False,
        "run_pre_meeting_intel": False,
        "manage_reports": False,
    },
    "marketing": {
        "crm_import": False,
        "prospect_migration": False,
        "manage_team": False,
        "run_pre_meeting_intel": False,
        "manage_reports": False,
    },
}


def normalize_role_permissions(value: Any) -> dict[str, dict[str, bool]]:
    raw = value if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, bool]] = {}
    for role, defaults in DEFAULT_ROLE_PERMISSIONS.items():
        current = raw.get(role) if isinstance(raw.get(role), dict) else {}
        normalized[role] = {
            key: bool(current.get(key, default))
            for key, default in defaults.items()
        }
    return normalized


async def get_workspace_settings_row(session: AsyncSession) -> WorkspaceSettings:
    row = await session.get(WorkspaceSettings, 1)
    if row is None:
        row = WorkspaceSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def get_role_permissions(session: AsyncSession) -> dict[str, dict[str, bool]]:
    row = await get_workspace_settings_row(session)
    return normalize_role_permissions(row.role_permissions)


async def user_has_permission(session: AsyncSession, user: User, permission_key: str) -> bool:
    if user.is_admin:
        return True
    permissions = await get_role_permissions(session)
    return bool(permissions.get(user.role, {}).get(permission_key, False))


async def require_workspace_permission(session: AsyncSession, user: User, permission_key: str) -> None:
    if await user_has_permission(session, user, permission_key):
        return
    raise ForbiddenError("You do not have permission to perform this action")


async def can_view_all_prospects(session: AsyncSession, user: User) -> bool:
    """True if the user may see every prospect (not just their own + unassigned).

    Admins always can. Specific non-admins can be granted broader access by an
    admin via WorkspaceSettings.prospect_view_all_user_ids.

    SDRs are NEVER view-all, even if their id sits in the grant list: SDRs are
    hard-restricted to their OWN prospects everywhere, so the own-only rule can't
    be bypassed via a grant.
    """
    if user.is_admin:
        return True
    if (user.role or "").lower() == "sdr":
        return False
    row = await session.get(WorkspaceSettings, 1)
    granted = (row.prospect_view_all_user_ids if row else None) or []
    return str(user.id) in {str(uid) for uid in granted}


async def can_view_all_deals(session: AsyncSession, user: User) -> bool:
    """The live deal pipeline is visible to every authenticated teammate.

    Keep this helper (and its signature) so callers cannot accidentally bring
    back ownership-based deal scoping. The legacy per-user allowlist is no
    longer consulted; it remains in the settings table solely for backwards
    compatibility with existing workspaces.
    """
    return True
