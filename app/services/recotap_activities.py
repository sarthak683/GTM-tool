"""Beacon → Recotap sales activities (``POST /sales-activities``).

Recotap's account intelligence is intent-side: ads, web visits, G2, Bombora. It
has no idea which accounts a rep has actually been working. Pushing calls and
emails closes that loop — an account with rising intent AND twelve outbound
touches is a very different picture from one with rising intent and silence.

Shape notes that drive the code below:

* ``activityType`` accepts only ``call`` and ``email``. Anything else comes back
  as ``skipped``, so LinkedIn touches and meetings are filtered out here rather
  than sent to be rejected.
* ``domain``, ``ownerEmail`` and at least one ``contacts[].email`` are ALL
  required. An activity missing any of them cannot be linked to an account on
  their side, so it is never sent — those are counted and reported instead.
* ``externalActivityId`` is the dedup key and a repeat comes back ``failed``.
  We send the Beacon activity UUID, and a watermark keeps normal runs from
  replaying history in the first place.
* Batch limit is 50 (the client chunks), and HTTP is 200 whatever happens, so
  ``results[]`` is the only source of truth.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.visibility import unscoped_for_background_job
from app.clients.recotap import RecotapClient
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.settings import WorkspaceSettings
from app.models.user import User
from app.services.recotap import is_pushable_domain, normalize_domain

logger = logging.getLogger(__name__)

_WATERMARK_KEY = "recotap_activities_synced_through"

# Recotap accepts these two and skips everything else.
_CALL = "call"
_EMAIL = "email"


# A real address, not merely "contains an @". Six prod contacts carry prose in
# the email column -- "inferred — scott.cravotta@genesys.com", "⚠️ not available
# — inferred: ...", "skambo@descartes.com ." -- which an `"@" in value` test
# waves through. Recotap rejects them, and because ONE bad address 400s the
# whole 50-item batch, those 6 rows failed 1,273 otherwise-good pushes on the
# first backfill attempt. Validate the shape, and skip rather than poison.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _clean_email(value: Optional[str]) -> Optional[str]:
    """Return a syntactically valid address, or None."""
    email = str(value or "").strip()
    return email if _EMAIL_RE.match(email) else None


def _iso(value: Optional[datetime]) -> Optional[str]:
    """ISO 8601 with an explicit UTC marker — every datetime here is naive UTC."""
    if value is None:
        return None
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity_type(activity: Activity) -> Optional[str]:
    """Map a Beacon activity to Recotap's two-value enum, or None to skip it.

    `medium` is the reliable discriminator: calls carry medium="call", and
    Gmail-synced sends carry type="email" with no medium at all (see
    metric_definitions.sent_email_filter), so both are checked.
    """
    medium = str(activity.medium or "").strip().lower()
    kind = str(activity.type or "").strip().lower()
    if medium == _CALL or kind == _CALL:
        return _CALL
    if medium == _EMAIL or kind == _EMAIL:
        return _EMAIL
    return None


def build_activity_payload(
    activity: Activity,
    *,
    contact: Optional[Contact],
    company: Optional[Company],
    owner: Optional[User],
) -> Optional[dict[str, Any]]:
    """One Beacon activity → one Recotap activity object, or None if unsendable.

    Returns None rather than a partial payload whenever a required link is
    missing — Recotap would reject it, and a rejected item is indistinguishable
    from a real failure in the response.
    """
    activity_type = _activity_type(activity)
    if activity_type is None:
        return None
    occurred = _iso(activity.created_at)
    if not occurred:
        return None

    # Domain: the account this activity attaches to on their side.
    domain = normalize_domain(company.domain if company else None)
    if not is_pushable_domain(domain):
        return None

    owner_email = _clean_email(getattr(owner, "email", None))
    if not owner_email:
        return None

    contact_email = _clean_email(getattr(contact, "email", None))
    if not contact_email:
        return None

    contact_obj: dict[str, Any] = {"email": contact_email}
    if contact is not None:
        contact_obj["externalContactId"] = str(contact.id)
        name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
        if name:
            contact_obj["name"] = name
        for key, value in (
            ("title", getattr(contact, "title", None)),
            ("phone", getattr(contact, "phone", None)),
            ("linkedinUrl", getattr(contact, "linkedin_url", None)),
        ):
            if str(value or "").strip():
                contact_obj[key] = str(value).strip()

    payload: dict[str, Any] = {
        "externalActivityId": str(activity.id),
        "activityType": activity_type,
        "occurredAt": occurred,
        "domain": domain,
        "ownerEmail": owner_email,
        "contacts": [contact_obj],
    }
    if company is not None and company.name:
        payload["accountName"] = company.name
    if getattr(owner, "name", None):
        payload["ownerName"] = owner.name
    if owner is not None:
        payload["ownerId"] = str(owner.id)

    if activity_type == _CALL:
        if activity.call_duration:
            # Recotap wants minutes; we store seconds.
            payload["durationMinutes"] = round(activity.call_duration / 60, 2)
        if activity.call_outcome:
            payload["outcome"] = str(activity.call_outcome)
        # Everything the CRM logs is rep-initiated outbound.
        payload["direction"] = "outbound"
    else:
        subject = str(getattr(activity, "email_subject", "") or "").strip()
        if subject:
            payload["subject"] = subject[:300]

    return payload


async def push_activities(
    session: AsyncSession,
    *,
    since: Optional[datetime] = None,
    limit: int = 500,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Push calls and emails to Recotap, newest-first from a watermark.

    ``dry_run`` defaults to True so an accidental call sends nothing. The
    watermark (workspace settings) advances only after a real run, and only to
    the newest activity actually SENT — advancing it past items we skipped would
    silently strand them forever.
    """
    client = RecotapClient()
    if not dry_run and not client.configured():
        return {"configured": 0, "sent": 0, "reason": "recotap_not_configured"}

    settings_row = (
        await session.execute(select(WorkspaceSettings).where(WorkspaceSettings.id == 1))
    ).scalar_one_or_none()
    if since is None and settings_row is not None and isinstance(settings_row.sync_schedule_settings, dict):
        raw = settings_row.sync_schedule_settings.get(_WATERMARK_KEY)
        if raw:
            try:
                since = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                since = None

    # Restrict to the two types Recotap accepts, IN SQL. Without this the window
    # is filled by whatever is oldest, and this CRM's oldest 588 activities are
    # `import_note`/`note` rows from the original data load — none of them
    # sendable. The push then sent 0 every run, and because the watermark only
    # moved on a successful send it never advanced past them: 24,505 real calls
    # and emails sat behind a permanent block. Filtering here means the window
    # only ever contains candidates that could plausibly go.
    stmt = select(Activity).where(
        Activity.created_by_id.is_not(None),
        or_(
            func.lower(func.coalesce(Activity.medium, "")).in_((_CALL, _EMAIL)),
            func.lower(func.coalesce(Activity.type, "")).in_((_CALL, _EMAIL)),
        ),
    )
    if since is not None:
        stmt = stmt.where(Activity.created_at > since)
    rows = (
        await session.execute(stmt.order_by(Activity.created_at.asc()).limit(limit))
    ).scalars().all()
    if not rows:
        return {"configured": int(client.configured()), "candidates": 0, "sent": 0,
                "dry_run": dry_run, "since": _iso(since)}

    contact_ids = {a.contact_id for a in rows if a.contact_id}
    owner_ids = {a.created_by_id for a in rows if a.created_by_id}
    contacts = {
        c.id: c for c in (
            await session.execute(unscoped_for_background_job(Contact, "recotap activities system work").where(Contact.id.in_(contact_ids)))
        ).scalars().all()
    } if contact_ids else {}
    owners = {
        u.id: u for u in (
            await session.execute(select(User).where(User.id.in_(owner_ids)))
        ).scalars().all()
    } if owner_ids else {}
    company_ids = {c.company_id for c in contacts.values() if c.company_id}
    companies = {
        c.id: c for c in (
            await session.execute(unscoped_for_background_job(Company, "recotap activities system work").where(Company.id.in_(company_ids)))
        ).scalars().all()
    } if company_ids else {}

    payloads: list[dict[str, Any]] = []
    newest_sent: Optional[datetime] = None
    unsendable = 0
    for activity in rows:
        contact = contacts.get(activity.contact_id) if activity.contact_id else None
        company = companies.get(contact.company_id) if contact and contact.company_id else None
        owner = owners.get(activity.created_by_id) if activity.created_by_id else None
        payload = build_activity_payload(activity, contact=contact, company=company, owner=owner)
        if payload is None:
            unsendable += 1
            continue
        payloads.append(payload)
        if newest_sent is None or (activity.created_at and activity.created_at > newest_sent):
            newest_sent = activity.created_at

    if dry_run:
        return {
            "configured": int(client.configured()), "dry_run": True,
            "candidates": len(rows), "would_send": len(payloads),
            "unsendable": unsendable, "since": _iso(since),
            "sample": payloads[:3],
        }

    if not payloads:
        # Every row in this window was unsendable. Nothing here can become
        # sendable by waiting, so step the watermark past the window rather than
        # re-reading the same rows on every future run — that deadlock is what
        # kept 24,505 activities from ever being pushed.
        newest_seen = max((a.created_at for a in rows if a.created_at), default=None)
        if settings_row is not None and newest_seen is not None:
            sched = dict(settings_row.sync_schedule_settings or {})
            sched[_WATERMARK_KEY] = newest_seen.isoformat()
            settings_row.sync_schedule_settings = sched
            session.add(settings_row)
            await session.commit()
        return {"configured": 1, "candidates": len(rows), "sent": 0,
                "unsendable": unsendable, "since": _iso(since),
                "advanced_past_unsendable_window_to": _iso(newest_seen)}

    body = await client.push_sales_activities(payloads)
    summary = body.get("summary") or {}

    # Advance only on a run that actually created something, and only to the
    # newest activity we SENT — never past one we skipped, because a row skipped
    # for a missing contact email may gain one later. The wholly-unsendable
    # window is handled separately above; that case cannot resolve itself and
    # would otherwise wedge the sync permanently.
    if settings_row is not None and newest_sent is not None and int(summary.get("created") or 0) > 0:
        sched = dict(settings_row.sync_schedule_settings or {})
        sched[_WATERMARK_KEY] = newest_sent.isoformat()
        settings_row.sync_schedule_settings = sched
        session.add(settings_row)
        await session.commit()

    failures = [r for r in (body.get("results") or []) if r.get("status") != "created"]
    if failures:
        logger.info("recotap activities: %s non-created items (first: %s)", len(failures), failures[0])
    return {
        "configured": 1,
        "candidates": len(rows),
        "sent": len(payloads),
        "unsendable": unsendable,
        "summary": summary,
        "since": _iso(since),
        "synced_through": _iso(newest_sent),
    }
