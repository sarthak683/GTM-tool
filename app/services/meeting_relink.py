"""Re-link historically orphaned meetings (backfill).

Calendar/tl;dv linking runs exactly once, at ingest
(``app.services.calendar_sync`` / ``app.services.tldv_sync``). If the company,
its web domain, the deal, or the attendee's CRM contact did not exist *yet* when
the event synced, the meeting is saved with ``company_id = deal_id = NULL`` and
is never re-evaluated — there is no second pass. Over time this strands real
meetings, and because the analytics meetings metric only counts CRM-linked
meetings (``app/api/v1/endpoints/analytics.py``), they silently drop off a rep's
scorecard even though the recording exists.

This module re-runs the **same precision matcher the live sync uses**
(``app.services.tldv_sync._match_*``) over unlinked, non-internal,
non-cancelled meetings so they self-heal once the CRM catches up. Matching is
domain-first and only attaches when exactly one already-sourced company matches;
it never guesses from the title. That mirrors the live ingest's
"precision over recall" stance — re-running it can only attach a meeting to the
unambiguously-correct account.

``dry_run=True`` (the default) computes the proposed links **without writing**,
so a run can be previewed (e.g. on prod) before anything is committed.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.meeting import Meeting
from app.services.tldv_sync import (
    _ensure_deal_contact,
    _match_company_from_domains,
    _match_contacts,
    _match_deal_from_contacts,
    _match_single_deal_for_company,
    _normalize_domain,
)

logger = logging.getLogger(__name__)


def _attendee_emails(meeting: Meeting) -> list[str]:
    """External attendee emails stored on the meeting (deduped, normalized)."""
    attendees = meeting.attendees if isinstance(meeting.attendees, list) else []
    emails: list[str] = []
    seen: set[str] = set()
    for attendee in attendees:
        if not isinstance(attendee, dict):
            continue
        email = str(attendee.get("email") or "").strip().lower()
        if email and "@" in email and email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


async def _resolve_link(
    session: AsyncSession, meeting: Meeting
) -> tuple[Company | None, Any | None, list]:
    """Return (company, deal, contact_ids) for a meeting using the live-sync
    precision rules, or (None, None, []) when nothing matches unambiguously.

    The reconciliation steps mirror ``tldv_sync`` ingest exactly so the backfill
    can never link a meeting differently than a fresh sync would have.
    """
    emails = _attendee_emails(meeting)
    if not emails:
        return None, None, []
    domains = list(
        dict.fromkeys(
            _normalize_domain(email.split("@", 1)[1]) for email in emails if "@" in email
        )
    )

    company = await _match_company_from_domains(session, domains)
    contacts = await _match_contacts(session, emails)
    contact_ids = [contact.id for contact in contacts if contact.id]
    deal = await _match_deal_from_contacts(session, contact_ids)

    # Same reconciliation as tldv_sync._process_meeting:
    if deal and company and deal.company_id and deal.company_id != company.id:
        # Deal belongs to a different company than the attendee domain says —
        # trust the domain, drop the deal link.
        deal = None
    if deal and not company and deal.company_id:
        company = await session.get(Company, deal.company_id)
    if company and not deal:
        # Exactly one sourced company AND exactly one deal for it = safe link.
        deal = await _match_single_deal_for_company(session, company.id)

    return company, deal, contact_ids


async def relink_unlinked_meetings(
    session: AsyncSession, *, dry_run: bool = True, limit: int | None = None
) -> dict:
    """Re-run the matcher over every unlinked meeting and (optionally) persist.

    Only touches meetings where BOTH ``company_id`` and ``deal_id`` are NULL —
    already-linked meetings are never re-pointed. Internal and cancelled
    meetings are skipped (they intentionally stay unlinked).
    """
    stmt = (
        select(Meeting)
        .where(
            Meeting.company_id.is_(None),
            Meeting.deal_id.is_(None),
            Meeting.is_internal.is_(False),
            Meeting.status != "cancelled",
        )
        .order_by(Meeting.scheduled_at.desc().nullslast(), Meeting.created_at.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    meetings = (await session.execute(stmt)).scalars().all()

    proposals: list[dict] = []
    linked_company = 0
    linked_deal = 0
    for meeting in meetings:
        company, deal, contact_ids = await _resolve_link(session, meeting)
        if not company and not deal:
            continue
        new_company_id = company.id if company else (deal.company_id if deal else None)
        new_deal_id = deal.id if deal else None
        proposals.append(
            {
                "meeting_id": str(meeting.id),
                "title": meeting.title,
                "scheduled_at": meeting.scheduled_at.isoformat() if meeting.scheduled_at else None,
                "source": meeting.external_source,
                "company_id": str(new_company_id) if new_company_id else None,
                "company_name": company.name if company else None,
                "deal_id": str(new_deal_id) if new_deal_id else None,
                "deal_name": deal.name if deal else None,
            }
        )
        if new_company_id:
            linked_company += 1
        if new_deal_id:
            linked_deal += 1
        if not dry_run:
            meeting.company_id = new_company_id
            meeting.deal_id = new_deal_id
            session.add(meeting)
            # Keep DealContact rows in sync, exactly like the live ingest does,
            # so the linked deal shows its participants.
            if new_deal_id:
                for contact_id in contact_ids:
                    await _ensure_deal_contact(session, new_deal_id, contact_id)

    if not dry_run and proposals:
        await session.commit()

    summary = {
        "dry_run": dry_run,
        "scanned": len(meetings),
        "matched": len(proposals),
        "linked_company": linked_company,
        "linked_deal": linked_deal,
        "proposals": proposals,
    }
    logger.info(
        "meeting_relink: dry_run=%s scanned=%s matched=%s linked_company=%s linked_deal=%s",
        dry_run,
        summary["scanned"],
        summary["matched"],
        linked_company,
        linked_deal,
    )
    return summary
