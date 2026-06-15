"""
Load performance-analytics settings with lazy defaults.

The `workspace_settings.analytics_settings` column is nullable — we fill it
from `build_default_analytics_settings()` on first read and persist. Every
metric/scorecard/funnel route calls `get_analytics_settings(session)` and
gets a fully-populated dict.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_defaults import build_default_analytics_settings
from app.models.settings import WorkspaceSettings


async def _load_or_create_row(session: AsyncSession) -> WorkspaceSettings:
    result = await session.execute(select(WorkspaceSettings).where(WorkspaceSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = WorkspaceSettings(id=1)
        session.add(row)
        await session.flush()
    return row


async def get_analytics_settings(session: AsyncSession) -> dict:
    row = await _load_or_create_row(session)
    if not row.analytics_settings:
        row.analytics_settings = build_default_analytics_settings()
        await session.commit()
    # Merge in any missing keys — keeps things forward-compatible as we add
    # new defaults without forcing a migration or admin-UI edit.
    defaults = build_default_analytics_settings()
    merged = {**defaults, **row.analytics_settings}
    return merged


async def update_analytics_settings(session: AsyncSession, patch: dict) -> dict:
    row = await _load_or_create_row(session)
    current = row.analytics_settings or build_default_analytics_settings()
    current.update(patch)
    row.analytics_settings = current
    await session.commit()
    return current
