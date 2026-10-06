"""Targeted, atomic writes to ``workspace_settings.sync_schedule_settings``.

That column is ONE JSON blob shared by every scheduled job (tldv sync, RecoTap
watermarks, the pod reports, the weekly digest, the team activity report).
The old pattern — load the row, copy the dict, set one key, write the whole
dict back — is a lost-update race: a job that loaded the row early and writes
late silently erases keys another job saved in between. On 2026-10-05 that
erased the team activity report's "already sent this week" key and the report
went out twice.

The helpers here are single ``UPDATE ... jsonb_set(...)`` statements, so they
only touch the keys they name and are serialised by the row lock. The team
activity report uses them; other jobs are untouched.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_MERGE_BLOCK_SQL = """
UPDATE workspace_settings
SET sync_schedule_settings = jsonb_set(
    COALESCE(sync_schedule_settings::jsonb, '{}'::jsonb),
    ARRAY[CAST(:block AS text)],
    COALESCE(sync_schedule_settings::jsonb -> CAST(:block AS text), '{}'::jsonb)
        || CAST(:fields AS jsonb),
    true
)::json
WHERE id = 1
"""


async def merge_sync_settings_block(
    session: AsyncSession, block_key: str, fields: dict[str, Any]
) -> None:
    """Merge ``fields`` into the object stored under ``block_key`` (e.g.
    ``"team_activity_report"``), leaving its other fields and every other
    top-level key alone. Does not commit."""
    await session.execute(text(_MERGE_BLOCK_SQL), {"block": block_key, "fields": json.dumps(fields)})


async def claim_scheduled_send(
    session: AsyncSession, block_key: str, send_key: str, claimed_at_iso: str
) -> bool:
    """Atomically claim this period's send BEFORE sending.

    True means this caller owns the send. False means another run already
    claimed ``send_key`` — skip. The compare-and-set happens inside one UPDATE
    under the row lock, so two overlapping ticks can never both win, and a
    later whole-blob rewrite by another job is the only thing that could undo
    it (which is why those writers now use the targeted helpers above).
    Commits, so the claim is visible to other workers immediately.
    """
    result = await session.execute(
        text(
            _MERGE_BLOCK_SQL
            + """
            AND COALESCE(
                sync_schedule_settings::jsonb -> CAST(:block AS text) ->> 'last_scheduled_send_key', ''
            ) <> CAST(:send_key AS text)
            RETURNING id
            """
        ),
        {
            "block": block_key,
            "send_key": send_key,
            "fields": json.dumps(
                {"last_scheduled_send_key": send_key, "last_scheduled_send_at": claimed_at_iso}
            ),
        },
    )
    claimed = result.first() is not None
    await session.commit()
    return claimed
