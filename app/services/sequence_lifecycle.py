"""
Sequence lifecycle reconciler.

Answers the rep's core question — "is this prospect's sequence actually
working?" — by joining every signal we have about the cadence into one
coherent per-step state.

Input signals we reconcile:
  - OutreachSequence: when it was launched, status, Instantly campaign id/status
  - OutreachStep: the plan (step_number, channel, delay) pushed to Instantly
  - Contact.enrichment_data.sequence_plan.steps: the pre-launch plan, which
    includes call / LinkedIn steps that Instantly doesn't own
  - Activity: every email_sent / email_opened / email_clicked / reply / call
    / linkedin touch tied to this contact
  - Contact.sequence_status: the contact-level state (sent / replied /
    interested / not_interested / bounced / ...)

Output: one timeline the UI can render honestly. If a step fired late,
didn't fire at all, was skipped on reply, or the Instantly campaign
silently paused — the state reflects that, and an `issues` list surfaces
each anomaly so reps know exactly what to check.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.outreach import OutreachSequence, OutreachStep
from app.models.user import User


# Cache of user-id -> display label inside a single lifecycle build. Avoids
# repeating the same SELECT for every step recorded by the same rep.
async def _resolve_user_label(session: AsyncSession, user_id: Optional[UUID], cache: dict[UUID, str]) -> Optional[str]:
    if not user_id:
        return None
    if user_id in cache:
        return cache[user_id]
    user = await session.get(User, user_id)
    label = (user.name or user.email) if user else None
    if label:
        cache[user_id] = label
    return label


def _activity_payload(activity: Activity) -> dict[str, Any]:
    """Build the rich per-activity payload the drawer needs. Pulls every
    user-visible field on the row — body, email envelope, call meta,
    recording, AI summary, source — so the UI can render without going
    back to the server."""
    meta = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    return {
        "activity_id": str(activity.id) if activity.id else None,
        "created_at": activity.created_at.isoformat() if activity.created_at else None,
        "source": activity.source or None,
        "external_source": activity.external_source or None,
        "external_source_id": activity.external_source_id or None,
        "medium": activity.medium or None,
        "content": activity.content or None,           # full body / log text
        "ai_summary": activity.ai_summary or None,
        "email_subject": activity.email_subject or meta.get("subject") or None,
        "email_from": activity.email_from or meta.get("from") or None,
        "email_to": activity.email_to or meta.get("to") or None,
        "email_cc": activity.email_cc or None,
        "call_duration_seconds": activity.call_duration,
        "recording_url": activity.recording_url or None,
        "aircall_user_name": activity.aircall_user_name or None,
        "call_id": activity.call_id or None,
        "created_by_id": str(activity.created_by_id) if activity.created_by_id else None,
        "event_type": str(meta.get("event_type") or "").lower() or None,
        "metadata": meta or None,
    }


# Sequence-level status — answers "is anything happening here?"
#   never_launched: no sequence row yet (rep hasn't generated or launched)
#   ready:          generated but not launched to Instantly
#   in_progress:    launched and still inside the cadence window
#   replied:        prospect replied; rest of email cadence auto-stops
#   booked:         meeting booked; everything else is history
#   stopped:        unsubscribed / bounced / not_interested / DNC
#   stalled:        launched 7+ days ago and no email_sent activity
#                   (Instantly push likely broken)
#   completed:      campaign finished all steps

_TERMINAL_CONTACT_STATES = {
    "replied",
    "meeting_booked",
    "not_interested",
    "unsubscribed",
    "bounced",
    "completed",
    "interested",
}


def _channel_of_step_record(step: OutreachStep) -> str:
    try:
        return (step.channel or "email").strip().lower()
    except Exception:
        return "email"


def _plan_from_contact(contact: Contact) -> list[dict[str, Any]]:
    """Pull the pre-launch sequence plan (which includes non-email steps)
    from contact enrichment. Returns [] if absent."""
    ed = contact.enrichment_data
    if not isinstance(ed, dict):
        return []
    plan = ed.get("sequence_plan")
    if not isinstance(plan, dict):
        return []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return []
    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        channel = str(raw.get("channel") or "").strip().lower()
        if channel in {"connector_request", "connector_follow_up"}:
            channel = "linkedin"
        if channel not in {"email", "call", "linkedin"}:
            continue
        try:
            day = int(raw.get("day_offset") or 0)
        except (TypeError, ValueError):
            day = 0
        normalized.append(
            {
                "index": idx,
                "channel": channel,
                "day_offset": day,
                "objective": (raw.get("objective") or "").strip() or None,
            }
        )
    return normalized


def _steps_from_outreach(steps: list[OutreachStep], launched_at: Optional[datetime]) -> list[dict[str, Any]]:
    """Derive a normalized step plan from the OutreachStep rows. Used when
    the contact has no enrichment sequence_plan (falls back to the email
    steps Instantly knows about)."""
    delays_in_days = []
    running_offset = 0
    for step in sorted(steps, key=lambda s: s.step_number):
        unit = (step.delay_unit or "Days").lower()
        value = int(step.delay_value or 0)
        days = value if unit.startswith("day") else (value / 24 if unit.startswith("hour") else value / 1440)
        running_offset += days
        delays_in_days.append(running_offset)
    normalized: list[dict[str, Any]] = []
    for idx, step in enumerate(sorted(steps, key=lambda s: s.step_number)):
        normalized.append(
            {
                "index": idx,
                "channel": _channel_of_step_record(step),
                "day_offset": int(delays_in_days[idx]) if idx < len(delays_in_days) else 0,
                "objective": None,
                "step_row_id": str(step.id) if step.id else None,
                "subject": step.subject,
            }
        )
    return normalized


async def _load_activities(session: AsyncSession, contact_id: UUID) -> list[Activity]:
    stmt = (
        select(Activity)
        .where(Activity.contact_id == contact_id)
        .order_by(Activity.created_at.asc())
        .limit(200)
    )
    return list((await session.execute(stmt)).scalars().all())


def _reconcile_email_step(
    step: dict[str, Any],
    launched_at: datetime,
    email_activities: list[Activity],
    now: datetime,
    prior_steps_any_fired: bool,
) -> dict[str, Any]:
    """Resolve the state of an email step from Instantly-signal activities."""
    # Which activity counts as this step's "sent" event? The nth email_sent
    # after launch, where n is the email-index of this step in the plan.
    # We count email-only steps before this one to pick the right occurrence.
    due = launched_at + timedelta(days=step["day_offset"])
    state = "upcoming"
    fired_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    clicked_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    bounced_at: Optional[datetime] = None
    subject: Optional[str] = step.get("subject")

    # Match: earliest email_sent with created_at >= due-window (±2h) that
    # hasn't been claimed by a prior step. For simplicity the caller passes
    # the pre-filtered list of email activities already sorted.
    for activity in email_activities:
        if activity._claimed:  # type: ignore[attr-defined]
            continue
        meta = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
        event_type = str(meta.get("event_type") or "").lower()
        if event_type != "email_sent":
            continue
        # Accept email_sent activity if it occurred on or after the due day
        # (some senders fire slightly early — we allow a 12h lead).
        delta = activity.created_at - due
        if delta < timedelta(hours=-12):
            continue
        fired_at = activity.created_at
        subject = subject or activity.email_subject or meta.get("subject")
        activity._claimed = True  # type: ignore[attr-defined]
        state = "sent"
        break

    # Per-step engagement counts. Counted from the actual Activity rows so the
    # drawer's "Opened N times" label always matches what's in the timeline
    # for this step's window — never the contact-wide aggregate, which would
    # over-count for multi-step sequences.
    open_count = 0
    click_count = 0
    open_activities: list[Activity] = []
    click_activities: list[Activity] = []
    if fired_at:
        # Find opens/clicks/replies/bounces that happened after this send
        # but before the next step's sent (caller won't pass future ones).
        for activity in email_activities:
            if activity.created_at <= fired_at:
                continue
            meta = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
            event_type = str(meta.get("event_type") or "").lower()
            if event_type == "email_opened":
                open_count += 1
                open_activities.append(activity)
                if not opened_at:
                    opened_at = activity.created_at
                    state = "opened"
            elif event_type == "email_link_clicked":
                click_count += 1
                click_activities.append(activity)
                if not clicked_at:
                    clicked_at = activity.created_at
                    state = "clicked"
            elif event_type == "reply_received" and not replied_at:
                replied_at = activity.created_at
                state = "replied"
                break
            elif event_type == "email_bounced" and not bounced_at:
                bounced_at = activity.created_at
                state = "failed"
                break
    else:
        # No send activity — decide between upcoming / overdue / skipped
        if due <= now:
            if prior_steps_any_fired or step["index"] == 0:
                # Step day has passed and we haven't seen it fire: either
                # Instantly is slow or the campaign is broken.
                state = "overdue"
            else:
                state = "upcoming"
        else:
            state = "upcoming"

    # Collect rich payloads for the send event + every follow-up signal the
    # drawer wants to render (open / click / reply / bounce). The state of
    # the step already captures *which one* is the latest event; the
    # drawer can fan out the full list for forensics.
    send_activity = None
    open_activity = None
    click_activity = None
    reply_activity = None
    bounce_activity = None
    for activity in email_activities:
        meta = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
        et = str(meta.get("event_type") or "").lower()
        if fired_at and activity.created_at == fired_at and et == "email_sent" and send_activity is None:
            send_activity = activity
        elif opened_at and activity.created_at == opened_at and et == "email_opened" and open_activity is None:
            open_activity = activity
        elif clicked_at and activity.created_at == clicked_at and et == "email_link_clicked" and click_activity is None:
            click_activity = activity
        elif replied_at and activity.created_at == replied_at and et == "reply_received" and reply_activity is None:
            reply_activity = activity
        elif bounced_at and activity.created_at == bounced_at and et == "email_bounced" and bounce_activity is None:
            bounce_activity = activity

    return {
        **step,
        "state": state,
        "due_at": due.isoformat(),
        "fired_at": fired_at.isoformat() if fired_at else None,
        "opened_at": opened_at.isoformat() if opened_at else None,
        "clicked_at": clicked_at.isoformat() if clicked_at else None,
        "replied_at": replied_at.isoformat() if replied_at else None,
        "bounced_at": bounced_at.isoformat() if bounced_at else None,
        "open_count": open_count,
        "click_count": click_count,
        "subject": subject,
        # Rich event payloads (None if the event hasn't fired yet)
        "send_event": _activity_payload(send_activity) if send_activity else None,
        "open_event": _activity_payload(open_activity) if open_activity else None,
        "click_event": _activity_payload(click_activity) if click_activity else None,
        "open_events": [_activity_payload(a) for a in open_activities],
        "click_events": [_activity_payload(a) for a in click_activities],
        "reply_event": _activity_payload(reply_activity) if reply_activity else None,
        "bounce_event": _activity_payload(bounce_activity) if bounce_activity else None,
    }


def _reconcile_call_step(
    step: dict[str, Any],
    launched_at: datetime,
    call_activities: list[Activity],
    now: datetime,
) -> dict[str, Any]:
    due = launched_at + timedelta(days=step["day_offset"])
    fired_at: Optional[datetime] = None
    outcome: Optional[str] = None
    note: Optional[str] = None
    state = "upcoming"
    matched_activity: Optional[Activity] = None

    for activity in call_activities:
        if activity._claimed:  # type: ignore[attr-defined]
            continue
        delta = activity.created_at - due
        if delta < timedelta(hours=-12):
            continue
        fired_at = activity.created_at
        outcome = activity.call_outcome
        # Keep a short `note` for backwards-compat with the rail UI, but
        # the drawer reads `call_event.content` for the full body.
        note = (activity.ai_summary or activity.content or "")[:200] or None
        matched_activity = activity
        activity._claimed = True  # type: ignore[attr-defined]
        state = "done"
        break

    if not fired_at:
        state = "overdue" if due <= now else "upcoming"

    return {
        **step,
        "state": state,
        "due_at": due.isoformat(),
        "fired_at": fired_at.isoformat() if fired_at else None,
        "call_outcome": outcome,
        "note": note,
        "call_event": _activity_payload(matched_activity) if matched_activity else None,
    }


def _reconcile_linkedin_step(
    step: dict[str, Any],
    launched_at: datetime,
    linkedin_activities: list[Activity],
    now: datetime,
) -> dict[str, Any]:
    due = launched_at + timedelta(days=step["day_offset"])
    fired_at: Optional[datetime] = None
    note: Optional[str] = None
    state = "upcoming"
    matched_activity: Optional[Activity] = None

    for activity in linkedin_activities:
        if activity._claimed:  # type: ignore[attr-defined]
            continue
        delta = activity.created_at - due
        if delta < timedelta(hours=-12):
            continue
        fired_at = activity.created_at
        note = (activity.content or "")[:200] or None
        matched_activity = activity
        activity._claimed = True  # type: ignore[attr-defined]
        state = "done"
        break

    if not fired_at:
        state = "overdue" if due <= now else "upcoming"

    return {
        **step,
        "state": state,
        "due_at": due.isoformat(),
        "fired_at": fired_at.isoformat() if fired_at else None,
        "note": note,
        "linkedin_event": _activity_payload(matched_activity) if matched_activity else None,
    }


def _categorize_activity(activity: Activity) -> str | None:
    meta = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    event_type = str(meta.get("event_type") or "").lower()
    if event_type in {"email_sent", "email_opened", "email_link_clicked", "reply_received", "email_bounced"}:
        return "email"
    if activity.type == "email" or (activity.source or "").lower() == "instantly":
        return "email"
    if activity.type == "call":
        return "call"
    if activity.type == "linkedin" or (activity.medium or "").lower() == "linkedin":
        return "linkedin"
    return None


async def build_sequence_lifecycle(
    session: AsyncSession, contact_id: UUID
) -> dict[str, Any]:
    contact = await session.get(Contact, contact_id)
    if not contact:
        return {"error": "Contact not found"}

    seq = (
        await session.execute(
            select(OutreachSequence).where(OutreachSequence.contact_id == contact_id)
        )
    ).scalars().first()

    # No sequence at all — report so the rep can generate one.
    if not seq:
        return {
            "contact_id": str(contact_id),
            "status": "never_launched",
            "sequence": None,
            "launched_at": None,
            "days_since_launch": None,
            "current_step_index": None,
            "total_steps": 0,
            "steps": [],
            "issues": [
                {
                    "severity": "info",
                    "code": "no_sequence_generated",
                    "message": "No outreach sequence has been generated for this prospect yet.",
                }
            ],
        }

    # Pick the plan. Prefer the rich contact-level plan (includes non-email
    # steps). Fall back to OutreachStep rows (email-only).
    plan = _plan_from_contact(contact)
    if not plan:
        step_rows = (
            await session.execute(
                select(OutreachStep).where(OutreachStep.sequence_id == seq.id)
            )
        ).scalars().all()
        if step_rows:
            plan = _steps_from_outreach(list(step_rows), seq.launched_at)

    launched_at = seq.launched_at

    if not launched_at:
        return {
            "contact_id": str(contact_id),
            "status": "ready",
            "sequence": {
                "id": str(seq.id),
                "status": seq.status,
                "instantly_campaign_status": seq.instantly_campaign_status,
            },
            "launched_at": None,
            "days_since_launch": None,
            "current_step_index": None,
            "total_steps": len(plan),
            "steps": [{**s, "state": "upcoming"} for s in plan],
            "issues": [
                {
                    "severity": "info",
                    "code": "not_launched",
                    "message": "Sequence generated but not launched to Instantly yet.",
                }
            ],
        }

    now = datetime.utcnow()
    activities = await _load_activities(session, contact_id)
    # Claim tracker (mutates activities in place — we only use this request
    # scope).
    for a in activities:
        a._claimed = False  # type: ignore[attr-defined]

    email_acts = [a for a in activities if _categorize_activity(a) == "email"]
    call_acts = [a for a in activities if _categorize_activity(a) == "call"]
    linkedin_acts = [a for a in activities if _categorize_activity(a) == "linkedin"]

    reconciled: list[dict[str, Any]] = []
    any_fired = False
    for step in plan:
        channel = step["channel"]
        if channel == "email":
            row = _reconcile_email_step(step, launched_at, email_acts, now, any_fired)
        elif channel == "call":
            row = _reconcile_call_step(step, launched_at, call_acts, now)
        elif channel == "linkedin":
            row = _reconcile_linkedin_step(step, launched_at, linkedin_acts, now)
        else:
            row = {**step, "state": "upcoming", "due_at": (launched_at + timedelta(days=step["day_offset"])).isoformat()}
        if row.get("fired_at"):
            any_fired = True
        reconciled.append(row)

    # Post-pass: resolve created_by_id → display name on every embedded
    # event payload so the drawer can render "by Sarthak" without an extra
    # round trip. Single cache so a rep who logged 5 steps only triggers
    # one User lookup.
    user_cache: dict[UUID, str] = {}
    event_keys = ("send_event", "open_event", "click_event", "reply_event", "bounce_event", "call_event", "linkedin_event")
    event_list_keys = ("open_events", "click_events")
    for row in reconciled:
        for k in event_keys:
            ev = row.get(k)
            if not ev:
                continue
            uid_raw = ev.get("created_by_id")
            if uid_raw:
                try:
                    label = await _resolve_user_label(session, UUID(uid_raw), user_cache)
                    ev["created_by_name"] = label
                except (ValueError, TypeError):
                    pass
        for k in event_list_keys:
            events = row.get(k) or []
            if not isinstance(events, list):
                continue
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                uid_raw = ev.get("created_by_id")
                if uid_raw:
                    try:
                        label = await _resolve_user_label(session, UUID(uid_raw), user_cache)
                        ev["created_by_name"] = label
                    except (ValueError, TypeError):
                        pass

    # If the prospect replied or booked, mark remaining email steps skipped.
    contact_status = (contact.sequence_status or "").lower()
    if contact_status in {"replied", "meeting_booked", "interested"}:
        hit_terminal = False
        for row in reconciled:
            if row["state"] in {"replied"}:
                hit_terminal = True
                continue
            if hit_terminal and row["state"] in {"upcoming", "overdue"} and row["channel"] == "email":
                row["state"] = "skipped"
                row["skip_reason"] = "prospect_responded"
    elif contact_status in {"unsubscribed", "not_interested", "bounced"}:
        for row in reconciled:
            if row["state"] in {"upcoming", "overdue"}:
                row["state"] = "skipped"
                row["skip_reason"] = contact_status

    # Determine current pointer: first non-terminal step.
    current_step_index: Optional[int] = None
    for row in reconciled:
        if row["state"] in {"upcoming", "overdue", "sent", "opened", "clicked"}:
            current_step_index = row["index"]
            break

    # Top-level status rollup.
    status = "in_progress"
    days_since_launch = (now - launched_at).days
    if contact_status == "replied":
        status = "replied"
    elif contact_status == "meeting_booked":
        status = "booked"
    elif contact_status in {"unsubscribed", "not_interested", "bounced"}:
        status = "stopped"
    elif all(r["state"] in {"sent", "opened", "clicked", "done", "skipped", "replied"} for r in reconciled):
        status = "completed"
    elif days_since_launch >= 7 and not any_fired:
        status = "stalled"

    # Issues: surfaceable problems the rep should look at.
    issues: list[dict[str, Any]] = []
    if status == "stalled":
        issues.append(
            {
                "severity": "error",
                "code": "sequence_stalled",
                "message": (
                    f"Sequence launched {days_since_launch} days ago but no email_sent "
                    f"activity yet. Check the Instantly campaign status and sending account."
                ),
            }
        )
    for row in reconciled:
        if row["state"] == "overdue":
            due = datetime.fromisoformat(row["due_at"])
            hrs = int((now - due).total_seconds() // 3600)
            issues.append(
                {
                    "severity": "warning",
                    "code": "step_overdue",
                    "step_index": row["index"],
                    "message": f"Step {row['index'] + 1} ({row['channel']}) is {hrs}h overdue.",
                }
            )
        if row["state"] == "failed":
            issues.append(
                {
                    "severity": "error",
                    "code": "email_bounced",
                    "step_index": row["index"],
                    "message": f"Step {row['index'] + 1} bounced — the email address is invalid.",
                }
            )

    if (seq.instantly_campaign_status or "").lower() == "paused" and status == "in_progress":
        issues.append(
            {
                "severity": "warning",
                "code": "campaign_paused",
                "message": "Instantly campaign is paused — future email steps will not fire until resumed.",
            }
        )

    return {
        "contact_id": str(contact_id),
        "status": status,
        "sequence": {
            "id": str(seq.id),
            "status": seq.status,
            "instantly_campaign_id": seq.instantly_campaign_id,
            "instantly_campaign_status": seq.instantly_campaign_status,
        },
        "launched_at": launched_at.isoformat(),
        "days_since_launch": days_since_launch,
        "current_step_index": current_step_index,
        "total_steps": len(reconciled),
        "steps": reconciled,
        "issues": issues,
    }


async def build_sequence_lifecycle_summaries(
    session: AsyncSession, contact_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    """Compact summary used for list views: {status, done_count, total_steps,
    current_channel, overdue_count}. One query per contact is fine for the
    typical list page size (≤ 50)."""
    out: dict[UUID, dict[str, Any]] = {}
    for cid in contact_ids:
        try:
            full = await build_sequence_lifecycle(session, cid)
        except Exception:
            continue
        if full.get("error"):
            continue
        steps = full.get("steps") or []
        done_states = {"sent", "opened", "clicked", "done", "replied", "skipped"}
        done = sum(1 for s in steps if s["state"] in done_states)
        overdue = sum(1 for s in steps if s["state"] == "overdue")
        current_channel: Optional[str] = None
        for s in steps:
            if s["state"] in {"upcoming", "overdue", "sent", "opened", "clicked"}:
                current_channel = s["channel"]
                break
        out[cid] = {
            "status": full["status"],
            "done_count": done,
            "total_steps": full["total_steps"],
            "overdue_count": overdue,
            "current_channel": current_channel,
            "current_step_index": full.get("current_step_index"),
            "days_since_launch": full.get("days_since_launch"),
            "has_issues": bool(full.get("issues")),
        }
    return out
