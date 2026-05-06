"""
Calendar sync service.

Maps Google Calendar events to CRM Meeting records.

Matching logic (same 3-pass approach as personal email sync):
  Pass 1: attendee email → CRM contact → linked deal
  Pass 2: attendee email domain → company → deal
  Pass 3: event title text match against known company names

For each matched event:
  - Upsert a Meeting (dedup by external_source_id = "gcal:{event_id}")
  - Link attendees from CRM contacts
  - Create/update the scheduled_at, meeting_type, and meeting_url

Called from the personal_email_sync Celery task so both mail + calendar
sync in the same job cycle.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.google_calendar import CalendarEvent
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting

logger = logging.getLogger(__name__)

FREE_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "icloud.com", "protonmail.com", "googlemail.com",
}


def _domain_from_email(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1].lower().strip().lstrip("www.")


def _normalize_name_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return " ".join(cleaned.split())


def _infer_display_name(addr: str) -> str:
    local = (addr.split("@", 1)[0] if "@" in addr else addr).strip()
    parts = [part for part in local.replace("_", ".").replace("-", ".").split(".") if part]
    if not parts:
        return addr
    return " ".join(part.title() for part in parts)


async def _ensure_deal_contact(session: AsyncSession, deal_id: UUID, contact_id: UUID) -> None:
    existing = await session.execute(
        select(DealContact).where(
            DealContact.deal_id == deal_id,
            DealContact.contact_id == contact_id,
        )
    )
    if not existing.scalar_one_or_none():
        session.add(DealContact(deal_id=deal_id, contact_id=contact_id))


def _title_company_match(
    title: str,
    company_name_candidates: list[tuple[str, UUID, str]],
) -> UUID | None:
    """Return a company id if the event title contains a strong, unambiguous company-name match.

    Uses the pre-sorted (longest-first) candidate list so "Procore Tech" wins over "Proc".
    Only considers candidates whose normalized name is >=4 chars, matches as a whole-token
    substring in the normalized title, and has no other longer candidate also matching.
    Returns None on ambiguity — we never guess.
    """
    norm_title = _normalize_name_key(title)
    if not norm_title:
        return None
    padded = f" {norm_title} "
    matches: list[tuple[str, UUID]] = []
    for norm_name, company_id, _display in company_name_candidates:
        if len(norm_name) < 4:
            continue
        if f" {norm_name} " in padded:
            matches.append((norm_name, company_id))
    if not matches:
        return None
    # Prefer the longest match; reject if two equally-long matches disagree.
    matches.sort(key=lambda m: len(m[0]), reverse=True)
    longest_len = len(matches[0][0])
    top_ids = {cid for name, cid in matches if len(name) == longest_len}
    if len(top_ids) == 1:
        return next(iter(top_ids))
    return None


def _infer_meeting_type(title: str) -> str:
    lower = title.lower()
    if any(w in lower for w in ["demo", "product demo", "platform demo"]):
        return "demo"
    if any(w in lower for w in ["discovery", "intro", "initial call", "first call"]):
        return "discovery"
    if any(w in lower for w in ["poc", "pilot", "trial", "proof of concept"]):
        return "poc"
    if any(w in lower for w in ["qbr", "business review", "quarterly"]):
        return "qbr"
    return "discovery"


def _is_internal_meeting(attendee_emails: list[str], user_email: str) -> bool:
    user_domain = _domain_from_email(user_email)
    external = [
        email for email in attendee_emails
        if email and email.lower().strip() != user_email.lower().strip() and _domain_from_email(email) != user_domain
    ]
    return len(external) == 0


def _naive_utc(dt: datetime) -> datetime:
    """Convert a tz-aware datetime to naive UTC (matches DB column type)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def sync_calendar_events(
    session: AsyncSession,
    events: list[CalendarEvent],
    user_email: str,
    owner_user_id: UUID | None = None,
) -> dict:
    """
    Process a list of CalendarEvents and upsert Meeting records.

    Returns stats dict: {meetings_created, meetings_updated, skipped}
    """
    stats = {"meetings_created": 0, "meetings_updated": 0, "skipped": 0}

    if not events:
        return stats

    # Pre-load company domains and names for matching
    company_rows = (await session.execute(
        select(Company.id, Company.name, Company.domain)
    )).all()
    company_domain_map: dict[str, tuple[UUID, str]] = {}
    company_name_candidates: list[tuple[str, UUID, str]] = []
    for row in company_rows:
        domain = (row.domain or "").strip().lower().lstrip("www.")
        if domain:
            company_domain_map[domain] = (row.id, row.name)
        norm = _normalize_name_key(row.name)
        if len(norm) >= 4:
            company_name_candidates.append((norm, row.id, row.name))
    company_name_candidates.sort(key=lambda x: len(x[0]), reverse=True)

    # Pre-load contact emails
    contact_rows = (await session.execute(
        select(Contact.id, Contact.email, Contact.company_id)
    )).all()
    contact_email_map: dict[str, UUID] = {}
    contact_company_map: dict[UUID, UUID | None] = {}
    for row in contact_rows:
        if row.email:
            contact_email_map[row.email.lower().strip()] = row.id
        contact_company_map[row.id] = row.company_id

    user_domain = _domain_from_email(user_email)

    deal_rows = (await session.execute(
        select(Deal.id, Deal.company_id, Deal.stage)
    )).all()
    deal_map: dict[UUID, tuple[UUID | None, str]] = {
        row.id: (row.company_id, row.stage or "")
        for row in deal_rows
    }

    for event in events:
        try:
            source_id = f"gcal:{event.event_id}"
            if _is_internal_meeting(event.attendee_emails, user_email):
                stats["skipped"] += 1
                continue

            external_attendees = [
                e for e in event.attendee_emails
                if e != user_email and _domain_from_email(e) != user_domain
            ]

            matched_deal_id: UUID | None = None
            matched_company_id: UUID | None = None
            domain_matched_company_id: UUID | None = None

            # ── Pass 1: exact contact email → deal (only when unambiguous) ─
            matched_contact_ids: list[UUID] = []
            for addr in external_attendees:
                cid = contact_email_map.get(addr)
                if cid:
                    matched_contact_ids.append(cid)

            deal_ids: list[UUID] = []
            if matched_contact_ids:
                dc_rows = (await session.execute(
                    select(DealContact.deal_id).where(
                        DealContact.contact_id.in_(matched_contact_ids)
                    ).distinct()
                )).all()
                deal_ids = [r.deal_id for r in dc_rows]
                unique_deal_ids = list(dict.fromkeys(deal_ids))
                if len(unique_deal_ids) == 1:
                    matched_deal_id = unique_deal_ids[0]
                    matched_company_id = deal_map.get(matched_deal_id, (None, ""))[0]
                elif unique_deal_ids:
                    candidate_company_ids = {
                        deal_map[deal_id][0]
                        for deal_id in unique_deal_ids
                        if deal_id in deal_map and deal_map[deal_id][0]
                    }
                    if len(candidate_company_ids) == 1:
                        matched_company_id = next(iter(candidate_company_ids))

            # ── Pass 2: attendee domain → company (deal only if unambiguous) ─
            domain_company_ids: set[UUID] = set()
            for addr in external_attendees:
                domain = _domain_from_email(addr)
                if not domain or domain in FREE_EMAIL_PROVIDERS:
                    continue
                if domain in company_domain_map:
                    domain_company_ids.add(company_domain_map[domain][0])
            if len(domain_company_ids) == 1:
                domain_matched_company_id = next(iter(domain_company_ids))
                matched_company_id = domain_matched_company_id
                if matched_deal_id:
                    deal_company_id = deal_map.get(matched_deal_id, (None, ""))[0]
                    if deal_company_id != domain_matched_company_id:
                        matched_deal_id = None
                company_deal_ids = [
                    deal_id
                    for deal_id, (company_id, stage) in deal_map.items()
                    if company_id == matched_company_id and stage not in {"closed", "closed_won"}
                ]
                if len(company_deal_ids) == 1:
                    matched_deal_id = company_deal_ids[0]

            # ── Pass 3 intentionally avoids fuzzy title-only linking ───────
            # If we cannot safely match by attendee email or attendee domain,
            # keep the meeting unlinked so users can re-link it explicitly.

            # ── Conflict guard: if title strongly names a DIFFERENT company,
            # don't trust the attendee-derived link. Drop both and let the
            # user re-link explicitly. This is the Procore→Azentio case:
            # internal/shared contact attended a meeting titled "Procore X ...".
            if matched_company_id is not None:
                title_company_id = _title_company_match(event.title, company_name_candidates)
                if title_company_id and title_company_id != matched_company_id:
                    logger.info(
                        "calendar_sync: title/attendee company mismatch for '%s' — "
                        "attendee=%s, title=%s. Leaving unlinked.",
                        event.title,
                        matched_company_id,
                        title_company_id,
                    )
                    matched_company_id = None
                    matched_deal_id = None

            # ── Build attendees payload ───────────────────────────────────
            attendees_payload = []
            matched_contact_ids_by_email = {
                (row.email or "").strip().lower(): row.id
                for row in contact_rows
                if row.email and row.id in matched_contact_ids
            }
            contact_detail_rows = (await session.execute(
                select(
                    Contact.id, Contact.first_name, Contact.last_name,
                    Contact.email, Contact.title, Contact.linkedin_url,
                ).where(Contact.id.in_(matched_contact_ids[:8]))
            )).all() if matched_contact_ids else []
            contact_details_by_id = {
                row.id: row for row in contact_detail_rows
            }
            for attendee_email in external_attendees:
                attendee_email = attendee_email.strip().lower()
                contact_id = matched_contact_ids_by_email.get(attendee_email)
                row = contact_details_by_id.get(contact_id) if contact_id else None
                attendees_payload.append({
                    "contact_id": str(contact_id) if contact_id else None,
                    "name": f"{row.first_name} {row.last_name}".strip() if row else _infer_display_name(attendee_email),
                    "email": attendee_email,
                    "title": row.title if row else None,
                    "linkedin_url": row.linkedin_url if row else None,
                    "matched": bool(contact_id),
                })

            if matched_deal_id and matched_contact_ids:
                for contact_id in dict.fromkeys(matched_contact_ids):
                    await _ensure_deal_contact(session, matched_deal_id, contact_id)

            meeting_type = _infer_meeting_type(event.title)
            scheduled_at = _naive_utc(event.start_dt) if event.start_dt else None

            # ── Upsert one meeting per calendar event ─────────────────────
            existing = (await session.execute(
                select(Meeting).where(
                    Meeting.external_source == "google_calendar",
                    Meeting.external_source_id == source_id,
                )
            )).scalar_one_or_none()

            now = datetime.utcnow()
            if existing:
                changed = False
                if scheduled_at and existing.scheduled_at != scheduled_at:
                    existing.scheduled_at = scheduled_at
                    changed = True
                if event.meeting_link and existing.meeting_url != event.meeting_link:
                    existing.meeting_url = event.meeting_link
                    changed = True
                if existing.title != event.title[:200]:
                    existing.title = event.title[:200]
                    changed = True
                if existing.meeting_type != meeting_type:
                    existing.meeting_type = meeting_type
                    changed = True
                # Don't stomp ownership: a calendar event with N Beacon
                # attendees is upserted by N user syncs. First non-NULL owner
                # wins; subsequent syncs only fill in if the field is empty.
                # Without this guard, whoever syncs last "owns" every shared
                # meeting — typically the user with the most events, which is
                # silently wrong for everyone else.
                if existing.owner_user_id is None and owner_user_id is not None:
                    existing.owner_user_id = owner_user_id
                    changed = True
                # Never stomp a user-established link. If the rep re-linked
                # Procore→Procore after an auto-linker got it wrong, keep it.
                if not getattr(existing, "manually_linked", False):
                    # Don't stomp a found link with a NULL match. The same
                    # event is upserted N times when N Beacon attendees sync
                    # it; if user A matched the company via their contacts
                    # and user B's sync produced no match (different visibility
                    # or stricter ambiguity), B's run would otherwise wipe A's
                    # link. Only update when the new match is non-NULL.
                    if matched_company_id is not None and existing.company_id != matched_company_id:
                        existing.company_id = matched_company_id
                        changed = True
                    if matched_deal_id is not None and existing.deal_id != matched_deal_id:
                        existing.deal_id = matched_deal_id
                        changed = True
                # Attendees: prefer the richer list. If the new sync produced
                # more matched contacts than the existing list, take the new
                # one; otherwise keep what we have.
                if attendees_payload and attendees_payload != existing.attendees:
                    existing_matched = sum(
                        1 for a in (existing.attendees or [])
                        if isinstance(a, dict) and a.get("matched")
                    )
                    new_matched = sum(1 for a in attendees_payload if a.get("matched"))
                    if new_matched >= existing_matched:
                        existing.attendees = attendees_payload
                        changed = True
                if changed:
                    existing.updated_at = now
                    # Only adopt synced_by_user_id when empty — same first-syncer-wins
                    # rule as owner_user_id. Without this, the "synced by" attribution
                    # also flips to whoever ran last.
                    if existing.synced_by_user_id is None and owner_user_id is not None:
                        existing.synced_by_user_id = owner_user_id
                    existing.synced_at = now
                    session.add(existing)
                    stats["meetings_updated"] += 1
            else:
                meeting = Meeting(
                    title=event.title[:200],
                    deal_id=matched_deal_id,
                    company_id=matched_company_id,
                    owner_user_id=owner_user_id,
                    meeting_type=meeting_type,
                    status="scheduled",
                    scheduled_at=scheduled_at,
                    meeting_url=event.meeting_link,
                    external_source="google_calendar",
                    external_source_id=source_id,
                    attendees=attendees_payload or None,
                    synced_by_user_id=owner_user_id,
                    synced_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(meeting)
                stats["meetings_created"] += 1
                logger.info(
                    "calendar_sync: created meeting '%s' (deal=%s, company=%s, start=%s)",
                    event.title,
                    matched_deal_id,
                    matched_company_id,
                    scheduled_at,
                )

        except Exception:
            logger.exception("calendar_sync: failed processing event %s", event.event_id)
            stats["skipped"] += 1

    await session.flush()
    return stats
