"""
Multichannel cadence scheduler — Celery beat task.

For every contact with a `sequence_plan.steps` list, walks the plan and,
whenever a non-email step's `day_offset` has elapsed since the sequence was
launched, creates a "system" task for the rep to complete that touch. Email
steps are skipped because Instantly already sends those.

Why this exists
---------------
Before this, `sequence_plan` was a static artifact: account sourcing wrote
{email/call/linkedin × day_offset} into enrichment_data, but nothing
actually walked the list or surfaced the call/LinkedIn steps to the rep.
Reps treated each channel as a separate to-do, losing the cadence. Now the
scheduler turns the plan into live tasks that appear in the rep's queue on
the right day.

Idempotency
-----------
Each created Task has a stable `system_key` of
`cadence:{sequence_id or contact_id}:step{index}` so we never create the
same step task twice; `_upsert_system_task` silently updates if already
present.

Skipping rules
--------------
- Skip email steps (Instantly owns those).
- Skip if the contact's `sequence_status` is a terminal state
  (meeting_booked / not_interested / unsubscribed / bounced / completed).
- Skip step day_offset=0 of the first email step is the launch itself —
  we don't duplicate.
- Skip if the step's day hasn't arrived yet.
- Skip if a later channel has already been logged (e.g., if the call on day
  3 was done, don't re-create the task).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.database import task_session
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.outreach import OutreachSequence
from app.services.tasks import _upsert_system_task, _resolve_system_task

logger = logging.getLogger(__name__)

_TERMINAL = {
    "meeting_booked",
    "not_interested",
    "unsubscribed",
    "bounced",
    "completed",
}

_NON_EMAIL_CHANNELS = {"call", "linkedin", "connector_request", "connector_follow_up"}

_CHANNEL_LABELS = {
    "call": ("Call", "call"),
    "linkedin": ("LinkedIn message", "linkedin"),
    "connector_request": ("LinkedIn connect request", "linkedin"),
    "connector_follow_up": ("LinkedIn follow-up", "linkedin"),
}


def _within_send_window(timezone_str: str | None, now_utc: datetime) -> bool:
    """Timezone-aware send window: only nudge during the prospect's local
    business hours (Mon–Fri, 08:00–18:00). Falls back to UTC when the contact
    has no usable timezone. Replaces the old fixed global cadence — timing now
    respects where the prospect actually is."""
    tz_name = (timezone_str or "").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    if local.weekday() >= 5:  # Saturday / Sunday
        return False
    return 8 <= local.hour < 18


def _extract_plan(contact: Contact) -> list[dict[str, Any]]:
    ed = contact.enrichment_data
    if not isinstance(ed, dict):
        return []
    plan = ed.get("sequence_plan")
    if not isinstance(plan, dict):
        return []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]


async def _anchor_date(
    session: AsyncSession, contact: Contact
) -> datetime | None:
    """When does the plan's day 0 start?

    Prefer the OutreachSequence.launched_at (when we actually pushed to
    Instantly). Fall back to the contact's updated_at, though this is a
    weaker signal because any edit bumps it. If nothing is usable, return
    None and the scheduler skips this contact until a sequence is launched.
    """
    seq = (
        await session.execute(
            select(OutreachSequence).where(OutreachSequence.contact_id == contact.id)
        )
    ).scalars().first()
    if seq and seq.launched_at:
        return seq.launched_at
    return None


async def _has_channel_activity_since(
    session: AsyncSession,
    contact_id: UUID,
    channel: str,
    since: datetime,
) -> bool:
    type_filter = "call" if channel == "call" else "linkedin"
    stmt = (
        select(Activity.id)
        .where(
            Activity.contact_id == contact_id,
            Activity.type == type_filter,
            Activity.created_at >= since,
        )
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    return row is not None


async def _process_contact(session: AsyncSession, contact: Contact, now: datetime) -> int:
    seq_status = (contact.sequence_status or "").lower()
    if seq_status in _TERMINAL:
        return 0

    # Timing is now driven by the rep's per-prospect follow-up date, not a fixed
    # global cadence. If a future follow-up is set, hold all cadence touches
    # until that date arrives — the rep decided when to come back.
    if contact.next_followup_at and contact.next_followup_at > now:
        return 0

    # Only nudge inside the prospect's local business hours / weekdays.
    if not _within_send_window(contact.timezone, now):
        return 0

    steps = _extract_plan(contact)
    if not steps:
        return 0

    anchor = await _anchor_date(session, contact)
    if not anchor:
        return 0

    company: Company | None = None
    if contact.company_id:
        company = await session.get(Company, contact.company_id)
    company_name = company.name if company else "this account"
    display_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or "prospect"

    created_or_updated = 0
    for idx, step in enumerate(steps):
        channel = str(step.get("channel") or "").strip().lower()
        if channel not in _NON_EMAIL_CHANNELS:
            continue
        try:
            day = int(step.get("day_offset") or 0)
        except (TypeError, ValueError):
            day = 0

        due = anchor + timedelta(days=day)
        if due > now:
            # Day hasn't arrived yet — don't surface the task early.
            continue

        # If the rep already touched this channel since the anchor, don't
        # pester them with a duplicate task.
        simple_channel = "linkedin" if channel in {"linkedin", "connector_request", "connector_follow_up"} else channel
        if await _has_channel_activity_since(session, contact.id, simple_channel, anchor):
            # There's already an activity — close any lingering system task.
            await _resolve_system_task(
                session,
                entity_type="contact",
                entity_id=contact.id,
                system_key=f"cadence:{contact.id}:step{idx}",
                status="completed",
            )
            continue

        label, _ = _CHANNEL_LABELS.get(channel, ("Cadence touch", "note"))
        system_key = f"cadence:{contact.id}:step{idx}"
        title = f"{label} · {display_name}"
        desc_parts = [
            f"Sequence step {idx + 1} (day {day}) — {label} on the planned cadence.",
            f"Account: {company_name}.",
        ]
        objective = step.get("objective")
        if isinstance(objective, str) and objective.strip():
            desc_parts.append(f"Objective: {objective.strip()}")

        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key=system_key,
            title=title,
            description=" ".join(desc_parts),
            priority="medium" if day <= 3 else "low",
            source="cadence_scheduler",
            recommended_action=f"complete_cadence_{simple_channel}",
            action_payload={
                "contact_id": str(contact.id),
                "channel": simple_channel,
                "step_index": idx,
                "day_offset": day,
            },
            assigned_role="sdr",
        )
        created_or_updated += 1

    return created_or_updated


async def _run() -> dict[str, int]:
    now = datetime.utcnow()
    stats = {"scanned": 0, "tasks_upserted": 0, "skipped": 0}
    async with task_session() as session:
        # Limit the scan to contacts with a launched sequence in the last 30
        # days — the cadence is typically done by then and we don't want to
        # create tasks for cold, abandoned sequences.
        cutoff = now - timedelta(days=30)
        stmt = (
            select(Contact)
            .join(OutreachSequence, OutreachSequence.contact_id == Contact.id)
            .where(OutreachSequence.launched_at >= cutoff)
            .distinct()
        )
        rows = list((await session.execute(stmt)).scalars().all())
        stats["scanned"] = len(rows)
        for contact in rows:
            try:
                count = await _process_contact(session, contact, now)
                stats["tasks_upserted"] += count
            except Exception:
                stats["skipped"] += 1
                logger.exception("cadence_scheduler: failed contact %s", contact.id)
        await session.commit()
    return stats


@celery_app.task(name="app.tasks.cadence_scheduler.advance_multichannel_cadence")
def advance_multichannel_cadence() -> dict[str, int]:
    """Celery entry. Runs periodically (beat schedule)."""
    try:
        return asyncio.run(_run())
    except RuntimeError:
        # asyncio.run raises RuntimeError if an event loop is already
        # running in this worker. In that case fall back to creating a new
        # loop manually.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
