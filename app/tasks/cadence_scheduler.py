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


async def _anchor_dates(
    session: AsyncSession, contact_ids: list[UUID]
) -> dict[UUID, datetime | None]:
    """When does each contact's plan day 0 start?

    Prefer the OutreachSequence.launched_at (when we actually pushed to
    Instantly). Fall back to the contact's updated_at, though this is a
    weaker signal because any edit bumps it. If nothing is usable, the value
    is None and the scheduler skips this contact until a sequence is launched.

    One bulk SELECT replaces the old per-contact lookup; like the old
    unordered `.first()`, the first row returned per contact wins.
    """
    if not contact_ids:
        return {}
    seqs = (
        await session.execute(
            select(OutreachSequence).where(OutreachSequence.contact_id.in_(contact_ids))
        )
    ).scalars().all()
    anchors: dict[UUID, datetime | None] = {}
    for seq in seqs:
        if seq.contact_id not in anchors:
            anchors[seq.contact_id] = seq.launched_at if seq.launched_at else None
    return anchors


async def _latest_channel_activity(
    session: AsyncSession, contact_ids: list[UUID], since: datetime
) -> dict[tuple[UUID, str], datetime]:
    """Bulk prefetch for `_has_channel_activity_since`: latest call/linkedin
    activity per (contact, type) created at/after `since` (the earliest anchor
    across contacts). The per-contact predicate then compares against that
    contact's own anchor, so max(created_at) >= anchor is exactly equivalent
    to the old per-(contact, channel) EXISTS query."""
    if not contact_ids:
        return {}
    rows = (
        await session.execute(
            select(Activity.contact_id, Activity.type, Activity.created_at).where(
                Activity.contact_id.in_(contact_ids),
                Activity.type.in_(("call", "linkedin")),
                Activity.created_at >= since,
            )
        )
    ).all()
    latest: dict[tuple[UUID, str], datetime] = {}
    for contact_id, activity_type, created_at in rows:
        key = (contact_id, activity_type)
        if key not in latest or created_at > latest[key]:
            latest[key] = created_at
    return latest


def _has_channel_activity_since(
    activity_latest: dict[tuple[UUID, str], datetime],
    contact_id: UUID,
    channel: str,
    since: datetime,
) -> bool:
    type_filter = "call" if channel == "call" else "linkedin"
    latest = activity_latest.get((contact_id, type_filter))
    return latest is not None and latest >= since


async def _process_contact(
    session: AsyncSession,
    contact: Contact,
    now: datetime,
    anchor: datetime | None,
    company_names: dict[UUID, str],
    activity_latest: dict[tuple[UUID, str], datetime],
) -> int:
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

    if not anchor:
        return 0

    company_name = "this account"
    if contact.company_id and contact.company_id in company_names:
        company_name = company_names[contact.company_id]
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
        if _has_channel_activity_since(activity_latest, contact.id, simple_channel, anchor):
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

        # Bulk prefetch (one query each) of everything _process_contact used
        # to fetch per contact: sequence anchor dates, company names, and
        # call/linkedin activity. Nothing in the loop writes to these tables,
        # so prefetching is outcome-identical to the old per-contact reads.
        anchors = await _anchor_dates(session, [c.id for c in rows])

        company_names: dict[UUID, str] = {}
        company_ids = list({c.company_id for c in rows if c.company_id})
        if company_ids:
            company_names = {
                row[0]: row[1]
                for row in (
                    await session.execute(
                        select(Company.id, Company.name).where(Company.id.in_(company_ids))
                    )
                ).all()
            }

        activity_latest: dict[tuple[UUID, str], datetime] = {}
        anchored_ids = [cid for cid, a in anchors.items() if a]
        if anchored_ids:
            min_anchor = min(anchors[cid] for cid in anchored_ids)
            activity_latest = await _latest_channel_activity(session, anchored_ids, min_anchor)

        for contact in rows:
            try:
                count = await _process_contact(
                    session,
                    contact,
                    now,
                    anchors.get(contact.id),
                    company_names,
                    activity_latest,
                )
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
