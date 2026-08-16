"""THE metric dictionary both engines share.

Two metric engines exist for good structural reasons — the performance
scorecard (``app/services/performance_metrics.py``) computes per-rep SQL
aggregates, while SalesAnalytics (``app/api/v1/endpoints/analytics.py``) does
one bulk pass over a window for every rep at once. What they must NEVER do is
disagree on what a metric MEANS. Every definition that both engines need lives
here, and only here:

- what counts as an outbound email / a reply,
- the unit a phone call is counted in,
- which Meeting rows are the same real-world meeting (cross-source dedupe),
- who a meeting is attributed to,
- what "the meeting happened" means.

Attribution note (deliberate, shared): outcome metrics attribute to the deal's
CURRENT ``assigned_to_id`` — reassigning a deal moves its history to the new
owner. Point-in-time ownership would need an ownership-history table that does
not exist yet; both engines share the same rule so at least they agree.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timezone as dt_timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, func, or_

from app.models.activity import Activity

logger = logging.getLogger(__name__)


# ── Workspace timezone ───────────────────────────────────────────────────────


def workspace_zoneinfo(analytics_settings: dict | None) -> ZoneInfo:
    """The workspace's reporting timezone as a ZoneInfo, falling back to UTC.

    ``workspace_timezone`` (analytics settings) decides which calendar day an
    event belongs to on every reporting surface. Storage stays naive UTC —
    only WINDOW BOUNDARIES are computed in this zone and converted back.
    A bad/unknown name falls back to UTC loudly rather than 500ing dashboards.
    """
    name = str((analytics_settings or {}).get("workspace_timezone") or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("Unknown workspace_timezone %r — falling back to UTC", name)
        return ZoneInfo("UTC")


def local_midnight_utc(day: date, tz: ZoneInfo) -> datetime:
    """Naive-UTC instant of midnight (00:00) of ``day`` in ``tz`` — the form
    every DB timestamp comparison in this codebase uses."""
    local = datetime.combine(day, time.min, tzinfo=tz)
    return local.astimezone(dt_timezone.utc).replace(tzinfo=None)


def workspace_today(tz: ZoneInfo, now_utc: Optional[datetime] = None) -> date:
    """Today's DATE in the workspace timezone (given a naive-UTC now)."""
    now_utc = now_utc or datetime.utcnow()
    return now_utc.replace(tzinfo=dt_timezone.utc).astimezone(tz).date()


# ── Emails ───────────────────────────────────────────────────────────────────


def email_event_type():
    """The Instantly event type stored in Activity.event_metadata.

    Rows written before the webhook stamped event_type (and manual UI-logged
    emails) have none — those are sends, hence the 'email_sent' coalesce.
    """
    return func.coalesce(
        func.jsonb_extract_path_text(Activity.event_metadata, "event_type"),
        "email_sent",
    )


def outbound_email_filter():
    """Predicate: this Activity row is an email a rep actually SENT.

    Two producer families, matched by construction (see app/tasks/email_sync.py
    and app/api/v1/endpoints/webhooks.py):
      * Instantly/webhook + manual rows: medium="email" — but the webhook also
        logs replies/opens/clicks under type="email", so gate on
        event_type == 'email_sent' (old rows without one are sends).
      * Gmail-synced sends: NO medium, source="gmail_sync"; rep sends carry
        created_by_id (resolved from the sender address). Deal-thread rows
        without created_by_id include the PROSPECT's inbound mail — excluded.
    """
    return and_(
        Activity.type == "email",
        or_(
            and_(
                Activity.medium == "email",
                Activity.source.is_distinct_from("email_reply"),
                email_event_type() == "email_sent",
            ),
            and_(
                Activity.source == "gmail_sync",
                Activity.created_by_id.is_not(None),
            ),
        ),
    )


def reply_email_filter():
    """Predicate: this Activity row is a prospect REPLY.

    Matches what the system actually writes for replies: Instantly
    webhook/sync rows with event_metadata.event_type == 'reply_received', plus
    the legacy source='email_reply' marker kept for old rows.
    """
    return and_(
        Activity.type == "email",
        or_(
            Activity.source == "email_reply",
            func.jsonb_extract_path_text(Activity.event_metadata, "event_type")
            == "reply_received",
        ),
    )


# ── Calls ────────────────────────────────────────────────────────────────────


def call_unit():
    """The unit a "call" is counted in: one PHONE CALL, not one activity row.

    An Aircall call emits several rows sharing one call_id (call.created,
    call.answered, call.ended, transcript) — counting rows made one connected
    dial read as 3-4 calls. Manual call logs have no call_id and fall back to
    their own row id, i.e. one row = one call.
    """
    return func.coalesce(Activity.call_id, cast(Activity.id, String))


# ── Meetings ─────────────────────────────────────────────────────────────────

# tl;dv and Google Calendar both ingest the SAME real-world meeting as separate
# Meeting rows (different external_source). When both exist we must count it
# once. Prefer tl;dv (only fires on calls that actually happened) over
# google_calendar (can include rescheduled/no-show events) over manual.
MEETING_SOURCE_PRIORITY = {"tldv": 0, "google_calendar": 1, "manual": 2, "": 3}

# Cross-source clock skew: tl;dv stamps `happenedAt` (real start) while Google
# Calendar stamps the scheduled time, so the "same" meeting can differ by a
# minute or two (e.g. 4:30 vs 4:31). Match within this window rather than on an
# exact-minute key, which silently double-counted skewed pairs.
MEETING_DEDUP_TOLERANCE_SECONDS = 5 * 60

# Statuses that EXPLICITLY say the meeting happened. Rarely set by reps — the
# shared inference in `meeting_happened` also treats a past, non-cancelled
# meeting as done, matching what the dashboard has always shown.
HELD_MEETING_STATUSES = frozenset({"held", "completed", "done"})


def meeting_happened(row, now: Optional[datetime] = None) -> bool:
    """One definition of "this meeting actually happened".

    Explicit held-ish status wins; otherwise a meeting whose scheduled time has
    passed and that was not cancelled counts as done (reps almost never flip
    the status by hand, so requiring it board-wide undercounted everyone —
    that was the scorecard/dashboard divergence).
    """
    status = str(getattr(row, "status", "") or "").strip().lower()
    if status in HELD_MEETING_STATUSES:
        return True
    if status == "cancelled":
        return False
    scheduled_at = getattr(row, "scheduled_at", None)
    now = now or datetime.utcnow()
    return scheduled_at is not None and scheduled_at <= now


def meeting_entity_key(row):
    """Entity a meeting belongs to, for cross-source grouping.

    Prefer company_id; fall back to deal_id. Deliberately does NOT use owner —
    tl;dv resolves a rep owner while the Google Calendar row of the SAME meeting
    often has owner=None, so keying on owner splits true duplicates. Nor deal_id
    when a company exists: one source may map the deal and the other only the
    company. Company + time-proximity is the reliable shared signal.
    """
    if row.company_id is not None:
        return ("company", row.company_id)
    if row.deal_id is not None:
        return ("deal", row.deal_id)
    # No company/deal anchor — never cluster these with each other (they share
    # no entity). Key on the row's own id so an unanchored row can only ever be
    # its own singleton group.
    return ("meeting", row.id)


def dedupe_meetings_across_sources(rows) -> list:
    """Collapse cross-source duplicates of the same real meeting to one row.

    Groups by entity (company, falling back to deal) and, within each group,
    clusters rows whose scheduled_at falls within the tolerance window — so a
    tl;dv row and a Google Calendar row a minute apart count once even when
    their owners differ. The tl;dv row wins via `MEETING_SOURCE_PRIORITY`.
    Returns one row per real meeting; order is not guaranteed (callers sort).
    """
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[meeting_entity_key(row)].append(row)

    kept: list = []
    for group in groups.values():
        # Sort by time (None last) so within-tolerance rows sit adjacent.
        group.sort(key=lambda r: (r.scheduled_at is None, r.scheduled_at or datetime.min))
        clusters: list[dict] = []
        for row in group:
            t = row.scheduled_at
            placed = False
            for cluster in clusters:
                anchor = cluster["anchor"]
                if t is not None and anchor is not None:
                    if abs((t - anchor).total_seconds()) <= MEETING_DEDUP_TOLERANCE_SECONDS:
                        cluster["rows"].append(row)
                        placed = True
                        break
                elif t is None and anchor is None:
                    cluster["rows"].append(row)
                    placed = True
                    break
            if not placed:
                clusters.append({"anchor": t, "rows": [row]})
        for cluster in clusters:
            kept.append(
                min(
                    cluster["rows"],
                    key=lambda r: MEETING_SOURCE_PRIORITY.get(
                        str(r.external_source or "").strip().lower(), 99
                    ),
                )
            )
    return kept


def meeting_attendee_rep_ids(row, *, user_ids_by_email: dict[str, UUID]) -> list[UUID]:
    attendees = row.attendees if isinstance(row.attendees, list) else []
    rep_ids: list[UUID] = []
    seen: set[UUID] = set()
    for attendee in attendees:
        if not isinstance(attendee, dict):
            continue
        email = str(attendee.get("email") or "").strip().lower()
        rep_id = user_ids_by_email.get(email)
        if rep_id and rep_id not in seen:
            seen.add(rep_id)
            rep_ids.append(rep_id)
    return rep_ids


def meeting_rep_ids(
    row,
    *,
    deal_owner: dict[UUID, UUID | None],
    user_ids_by_email: dict[str, UUID],
) -> list[UUID | None]:
    """Who a meeting counts for: explicit owner, else the deal's owner, plus
    every workspace rep on the attendee list. [None] = Unassigned bucket."""
    rep_ids: list[UUID | None] = []
    seen: set[UUID] = set()
    primary_id = row.owner_user_id or deal_owner.get(row.deal_id)
    if primary_id:
        rep_ids.append(primary_id)
        seen.add(primary_id)
    for attendee_rep_id in meeting_attendee_rep_ids(row, user_ids_by_email=user_ids_by_email):
        if attendee_rep_id not in seen:
            seen.add(attendee_rep_id)
            rep_ids.append(attendee_rep_id)
    return rep_ids or [None]
