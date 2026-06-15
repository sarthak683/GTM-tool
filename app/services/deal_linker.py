"""Deal ↔ account ↔ prospect stakeholder linker.

89% of open deals had no linked contact, starving MEDDPICC + client engagement.
This reconciles a deal's stakeholders from data the deal ALREADY owns — zero
external cost, deal-scoped:

  1) the deal company's existing CRM contacts not yet linked,
  2) external attendees on the deal's meetings (create the contact + link it),
  3) external human participants on the deal's emails (create + link).

Contact creation is email-deduped and quality-filtered (internal domains, free
mail providers, and no-reply/automation noise are skipped), so it won't mint
junk. Idempotent: re-running only links/creates what's missing.
"""
from __future__ import annotations

import logging
import re
from uuid import UUID

from sqlalchemy import func
from sqlmodel import select

from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting
from app.repositories.deal import _is_noise_email_from
from app.services.internal_domains import get_internal_domains

logger = logging.getLogger(__name__)

# Personal mailboxes can't be reliably attributed to a company account.
FREE_EMAIL_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com",
    "hotmail.com", "live.com", "icloud.com", "aol.com", "proton.me", "protonmail.com",
    "rediffmail.com", "ymail.com", "msn.com",
}

# Per-deal cap on newly-minted stakeholders, so one chatty thread can't flood a deal.
_MAX_NEW_PER_DEAL = 12


def _clean_email(addr: str | None) -> str:
    return (addr or "").strip().lower()


def _split_addrs(value: str | None) -> list[str]:
    if not value:
        return []
    return [p for p in re.split(r"[,;]", value) if p.strip()]


def _candidate_ok(email: str, internal_domains: set[str]) -> bool:
    """A real external company stakeholder — not us, not a personal inbox, not noise."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    if domain in internal_domains or domain in FREE_EMAIL_PROVIDERS:
        return False
    if _is_noise_email_from(email):
        return False
    return True


def _name_from(name: str, email: str) -> tuple[str, str]:
    """Best-effort (first, last). `contacts.last_name` is NOT NULL, so last is
    always a string ("" when there's only one token)."""
    parts = (name or "").split()
    if parts:
        return parts[0], " ".join(parts[1:])
    # Derive from the email local part, splitting on . / _ (e.g. raghav.shastri).
    bits = [b for b in re.split(r"[._]+", email.split("@", 1)[0]) if b]
    if len(bits) >= 2:
        return bits[0].title(), " ".join(b.title() for b in bits[1:])
    return (bits[0].title() if bits else "Contact"), ""


async def reconcile_deal_stakeholders(
    session, deal: Deal, *, create_from_signals: bool = True
) -> dict:
    """Attach stakeholders to a deal from its own account + meetings + emails.

    Returns {"linked": n, "created": n}. No-ops on a deal with no company_id
    (can't anchor a contact to an account).
    """
    if not deal.company_id:
        return {"linked": 0, "created": 0}

    existing_links: set[UUID] = {
        row[0]
        for row in (
            await session.execute(select(DealContact.contact_id).where(DealContact.deal_id == deal.id))
        ).all()
    }
    linked = 0
    created = 0

    async def _link(contact_id: UUID) -> None:
        nonlocal linked
        if contact_id in existing_links:
            return
        session.add(DealContact(deal_id=deal.id, contact_id=contact_id, role="auto_linked"))
        existing_links.add(contact_id)
        linked += 1

    # 1) The account's existing contacts — link any not already on the deal.
    by_email: dict[str, Contact] = {}
    company_contacts = (
        await session.execute(select(Contact).where(Contact.company_id == deal.company_id))
    ).scalars().all()
    for contact in company_contacts:
        if contact.email:
            by_email[contact.email.strip().lower()] = contact
        await _link(contact.id)

    if not create_from_signals:
        return {"linked": linked, "created": created}

    # Only mint a stakeholder when their email domain MATCHES the deal's account
    # domain — meeting/email threads include third parties (advisors, partners,
    # other vendors) we must NOT attribute to this company. Skip entirely when
    # the account has no real domain (placeholder ".unknown"); those deals get
    # stakeholders via domain backfill + paid enrichment instead.
    company = await session.get(Company, deal.company_id)
    company_domain = (getattr(company, "domain", "") or "").strip().lower()
    if not company_domain or company_domain.endswith(".unknown"):
        return {"linked": linked, "created": created}

    internal_domains = await get_internal_domains(session)

    def _is_company_stakeholder(email: str) -> bool:
        return _candidate_ok(email, internal_domains) and email.rsplit("@", 1)[1] == company_domain

    # Gather stakeholder emails from the deal's own meetings + emails (domain-matched).
    candidates: dict[str, dict] = {}  # email -> {name, title}
    meetings = (
        await session.execute(select(Meeting).where(Meeting.deal_id == deal.id))
    ).scalars().all()
    for meeting in meetings:
        for attendee in meeting.attendees if isinstance(meeting.attendees, list) else []:
            if not isinstance(attendee, dict):
                continue
            email = _clean_email(attendee.get("email"))
            if _is_company_stakeholder(email):
                candidates.setdefault(email, {"name": (attendee.get("name") or "").strip(), "title": (attendee.get("title") or "").strip()})

    email_acts = (
        await session.execute(
            select(Activity).where(Activity.deal_id == deal.id, func.lower(Activity.type) == "email")
        )
    ).scalars().all()
    for act in email_acts:
        for raw in [act.email_from, *(_split_addrs(act.email_to))]:
            email = _clean_email(raw)
            if _is_company_stakeholder(email):
                candidates.setdefault(email, {"name": "", "title": ""})

    for email, meta in candidates.items():
        if created >= _MAX_NEW_PER_DEAL:
            break
        if email in by_email:
            await _link(by_email[email].id)
            continue
        existing = (
            await session.execute(select(Contact).where(func.lower(Contact.email) == email))
        ).scalars().first()
        if existing:
            by_email[email] = existing
            await _link(existing.id)
            continue
        first, last = _name_from(meta["name"], email)
        contact = Contact(
            first_name=first or "Contact",
            last_name=last,  # NOT NULL column — "" is valid, None is not
            email=email,
            title=meta["title"] or None,
            company_id=deal.company_id,
        )
        session.add(contact)
        await session.flush()
        by_email[email] = contact
        await _link(contact.id)
        created += 1

    return {"linked": linked, "created": created}
