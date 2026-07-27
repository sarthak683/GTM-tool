"""
Personal email sync service.

Core logic for processing a batch of EmailMessage objects fetched from a
user's personal Gmail. Handles:

  1. Deal/contact matching via email address → domain → AI fallback
  2. CRM gap-filling: auto-create contacts only when they map to an existing account
  3. Activity logging (deduped by message_id + deal_id)
  4. AI-driven task generation from email thread context

Called by the Celery task (personal_email_sync.py) which handles fetching,
token refresh, and cursor management.
"""
from __future__ import annotations

import email.utils
import logging
import re
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.google_docs import fetch_google_doc_context
from app.clients.gmail_inbox import EmailMessage
from app.config import settings
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting
from app.models.task import Task
from app.services.activity_signal_classifier import detect_latest_intent_from_segments
from app.models.user import User
from app.models.user_email_connection import UserEmailConnection
from app.services.prospect_hygiene import is_valid_prospect_candidate
from app.services.tasks import refresh_system_tasks_for_entity

logger = logging.getLogger(__name__)
FREE_EMAIL_PROVIDERS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "protonmail.com",
}
AUTOMATED_EMAIL_LOCAL_PARTS = {
    "accountspayable",
    "accounts",
    "alert",
    "alerts",
    "automated",
    "billing",
    "bounce",
    "broadcast",
    "campaign",
    "community",
    "comms",
    "digest",
    "donotreply",
    "do-not-reply",
    "drive-shares-dm-noreply",
    "drive-shares-noreply",
    "event",
    "events",
    "invoice",
    "mailbot",
    "mailer",
    "mailer-daemon",
    "marketing",
    "meetings-noreply",
    "membership",
    "news",
    "newsletter",
    "notetaker",
    "notetaker-updates",
    "no-reply",
    "noreply",
    "notification",
    "notifications",
    "postmaster",
    "receipts",
    "register",
    "registration",
    "reservations",
    "techcomms",
    "unsubscribe",
    "updates",
}
AUTOMATED_EMAIL_LOCAL_MARKERS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "notification",
    "notifications",
    "newsletter",
    "marketing",
    "campaign",
    "broadcast",
)

# Bulk/ESP "sending subdomain" prefixes. Marketing & transactional mail almost
# always leaves from a dedicated sending subdomain (e.g. emails.hertz.com,
# mail.salesforce.com, tp2.terrapinn.com) rather than the bare corporate domain
# a real buyer types from. A FROM domain with 3+ labels whose left-most label is
# one of these (or matches a short e<n>/t<n> pattern) is treated as bulk. This is
# what was attaching conference blasts (Terrapinn → ABB) to live deals.
BULK_SENDING_SUBDOMAIN_PREFIXES = {
    "email", "emails", "mail", "mailer", "mailing", "mailgun", "sendgrid",
    "e", "em", "send", "sending", "sender", "smtp", "mg", "sg",
    "news", "newsletter", "marketing", "mkt", "campaign", "campaigns",
    "reply", "replies", "bounce", "bounces", "notify", "notification",
    "notifications", "click", "clicks", "link", "links", "list", "lists",
    "cmail", "ccsend", "hs", "info", "events", "engage",
}
AUTOMATED_SUBJECT_MARKERS = (
    "accepted:",
    "automatic reply:",
    "canceled:",
    "cancelled:",
    "declined:",
    "delivery status notification",
    "failure notice",
    "invitation:",
    "meeting notes",
    "notes:",
    "out of office",
    "problem with the notes:",
    "submission of invoice",
    "we couldn't record your meeting",
    "we could not record your meeting",
    "unable to record",
    "updated invitation:",
    "undeliverable:",
    "your action items from",
    "your upcoming meetings",
)
ADMIN_SUBJECT_WORDS = (
    "bill",
    "billing",
    "invoice",
    "payment",
    "receipt",
)

def _normalize_domain(value: str | None) -> str:
    domain = (value or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _domain_from_email(addr: str) -> str:
    if "@" not in addr:
        return ""
    return _normalize_domain(addr.split("@", 1)[1])


# All sending domains that belong to Beacon — treat as internal regardless of
# which specific domain the connected inbox uses.
_ALL_BEACON_DOMAINS = {"beacon.li", "beaconli.co", "beaconli.com"}


def _is_internal_address(addr: str, internal_domain: str) -> bool:
    addr_domain = _domain_from_email(addr)
    return bool(
        addr
        and addr_domain
        and (addr_domain == internal_domain or addr_domain in _ALL_BEACON_DOMAINS)
    )


def _normalize_beacon_sender(addr: str) -> str:
    """Normalize any Beacon sending-domain address to its @beacon.li canonical form.
    e.g. sipra@beaconli.com → sipra@beacon.li.  Non-beacon addresses are returned as-is."""
    if "@" not in addr:
        return addr
    local, domain = addr.rsplit("@", 1)
    if domain.strip().lower() in _ALL_BEACON_DOMAINS:
        return f"{local}@beacon.li"
    return addr


def _is_bulk_sender_domain(addr: str) -> bool:
    """True when the address sends from a dedicated bulk/ESP subdomain.

    Real buyers email from `name@company.com`; marketing and transactional
    platforms send from a sending subdomain (`emails.hertz.com`,
    `mail.salesforce.com`, `tp2.terrapinn.com`). We require 3+ labels so a plain
    `company.com` is never flagged, then check the left-most label against a set
    of known ESP prefixes plus short `e<n>`/`t<n>`/`em<n>` patterns ESPs rotate.
    """
    domain = (addr or "").split("@", 1)[1].strip().lower() if "@" in (addr or "") else ""
    labels = [p for p in domain.split(".") if p]
    if len(labels) < 3:
        return False
    head = labels[0]
    if head in BULK_SENDING_SUBDOMAIN_PREFIXES:
        return True
    # ESP rotation subdomains like tp2, t3, em1, e2, mg5.
    return bool(re.fullmatch(r"(e|em|t|tp|mg|sg|p|cm|mta)\d+", head))


def _is_automated_email(msg: EmailMessage) -> bool:
    subject = (msg.subject or "").strip().lower()
    normalized_subject = re.sub(r"^(re|fw|fwd):\s*", "", subject)
    if any(marker in normalized_subject for marker in AUTOMATED_SUBJECT_MARKERS):
        return True
    if any(re.search(rf"\b{re.escape(word)}\b", normalized_subject) for word in ADMIN_SUBJECT_WORDS):
        return True
    # The FROM address is the strongest bulk signal — a marketing/event blast is
    # defined by who sent it, not who received it.
    if _is_bulk_sender_domain(msg.from_addr):
        return True
    addresses = [msg.from_addr, *msg.to_addrs, *msg.cc_addrs]
    for addr in addresses:
        local = (addr or "").split("@", 1)[0].strip().lower()
        if local in AUTOMATED_EMAIL_LOCAL_PARTS:
            return True
        if any(marker in local for marker in AUTOMATED_EMAIL_LOCAL_MARKERS):
            return True
    return False


def _infer_name_from_email(addr: str) -> tuple[str, str]:
    local = (addr.split("@", 1)[0] if "@" in addr else addr).strip()
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    if not parts:
        return "Unknown", "Contact"
    if len(parts) == 1:
        return parts[0].title(), "Contact"
    return parts[0].title(), " ".join(p.title() for p in parts[1:])


def _normalize_name_key(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return " ".join(cleaned.split())


def _match_company_from_text(
    text: str,
    company_name_candidates: list[tuple[str, UUID, str]],
) -> tuple[UUID, str] | None:
    normalized_text = _normalize_name_key(text)
    if not normalized_text:
        return None

    haystack = f" {normalized_text} "
    for normalized_name, company_id, company_name in company_name_candidates:
        if f" {normalized_name} " in haystack:
            return company_id, company_name
    return None


def _count_distinct_company_mentions(
    text: str,
    company_name_candidates: list[tuple[str, UUID, str]],
    cap: int = 3,
) -> int:
    """How many DISTINCT CRM company names appear in this text.

    A conference/newsletter blast ("Eli Lilly, AstraZeneca & Roche take the
    stage… ABB joins…") name-drops many CRM companies at once. When 2+ are
    present the text is an ambiguous list, not a thread about one account, so we
    must NOT let name/AI matching staple it to any single deal. Stops at `cap`
    to bound work on large bodies.
    """
    normalized_text = _normalize_name_key(text)
    if not normalized_text:
        return 0
    haystack = f" {normalized_text} "
    seen: set[UUID] = set()
    for normalized_name, company_id, _company_name in company_name_candidates:
        if company_id in seen:
            continue
        if f" {normalized_name} " in haystack:
            seen.add(company_id)
            if len(seen) >= cap:
                break
    return len(seen)


def _parse_message_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed is None:
            return datetime.utcnow()
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return datetime.utcnow()

def _is_active_deal_stage(stage: str | None) -> bool:
    normalized = (stage or "").strip().lower()
    return normalized not in {
        "closed_won",
        "closed_lost",
        "not_a_fit",
        "cold",
        "on_hold",
        "nurture",
        "churned",
        "closed",
    }


def _is_trackable_sales_account(company: Company) -> bool:
    """Return True only for real CRM accounts, not empty domain placeholders."""
    return any(
        [
            company.sourcing_batch_id,
            company.assigned_to_id,
            company.assigned_rep_email,
            company.sdr_id,
            company.sdr_email,
            company.outreach_plan,
            company.prospecting_profile,
            company.enrichment_cache,
            company.enriched_at,
            company.icp_score is not None,
            company.icp_tier,
            company.description,
            company.account_thesis,
            company.why_now,
            company.beacon_angle,
            company.recommended_outreach_lane,
            company.outreach_status,
            company.disposition,
            company.last_outreach_at,
        ]
    )


async def _load_existing_thread_segments(
    session: AsyncSession,
    *,
    deal_id: UUID,
    thread_id: str,
) -> list[str]:
    if not thread_id:
        return []
    rows = (
        await session.execute(
            select(
                Activity.created_at,
                Activity.email_subject,
                Activity.content,
                Activity.event_metadata,
            ).where(
                Activity.deal_id == deal_id,
                Activity.type == "email",
            ).order_by(Activity.created_at.asc())
        )
    ).all()

    segments: list[str] = []
    for row in rows:
        metadata = row.event_metadata if isinstance(row.event_metadata, dict) else {}
        if metadata.get("gmail_thread_id") != thread_id:
            continue
        latest_message_text = str(metadata.get("thread_latest_message_text") or "").strip()
        google_doc_transcript = str(metadata.get("google_doc_transcript") or "").strip()
        snippet = "\n".join(
            part for part in [row.email_subject or "", latest_message_text or row.content or "", google_doc_transcript] if part
        ).strip()
        if snippet:
            segments.append(snippet)
    return segments


async def _count_open_system_tasks(session: AsyncSession, deal_id: UUID) -> int:
    result = await session.execute(
        select(Task.id).where(
            Task.entity_type == "deal",
            Task.entity_id == deal_id,
            Task.task_type == "system",
            Task.status == "open",
        )
    )
    return len(result.all())


async def _ai_classify_email(
    subject: str,
    body: str,
    company_names: list[str],
    contact_names: list[str],
) -> dict | None:
    """
    Ask Claude Haiku to identify which company/contact this email is about
    and whether it contains a CRM-relevant intent.

    Returns dict with keys: company_name, contact_name, intent_key (all optional).
    Only called when domain matching fails — keeps cost near-zero.
    """
    from app.config import settings

    if not settings.claude_api_key:
        return None

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.claude_api_key)
        known_companies = ", ".join(company_names[:20]) if company_names else "none known"
        known_contacts = ", ".join(contact_names[:20]) if contact_names else "none known"

        prompt = (
            "You are a CRM assistant. Analyze this sales email and answer ONLY with valid JSON.\n\n"
            f"Known CRM companies: {known_companies}\n"
            f"Known CRM contacts: {known_contacts}\n\n"
            f"Subject: {subject}\n\n"
            f"Email body (first 1000 chars):\n{body[:1000]}\n\n"
            "Return JSON with these optional fields:\n"
            '  "company_name": the CRM company name this email is about (or null)\n'
            '  "contact_name": the CRM contact name in this email (or null)\n'
            '  "intent": one of: poc_agreed, poc_wip, commercial_negotiation, '
            'closed_won, not_a_fit, send_pricing_package, book_workshop_session, '
            'follow_up_buyer_thread, or null\n\n'
            "Only match known CRM companies/contacts. Return null for unknowns."
        )

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        import json
        return json.loads(text)
    except Exception as e:
        logger.warning("AI email classification failed: %s", e)
        return None


async def _generate_email_summary(subject: str, body: str) -> str | None:
    """One-line summary for activity.ai_summary."""
    from app.config import settings

    if not settings.claude_api_key or len(body) < 100:
        return None
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.claude_api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    "Summarize this sales email in one sentence (max 15 words). "
                    "Focus on the key action or outcome.\n\n"
                    f"Subject: {subject}\n\n{body[:1200]}"
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception:
        return None


async def _get_or_create_company_by_domain(
    session: AsyncSession,
    domain: str,
    suggested_name: str | None = None,
) -> Company | None:
    """Look up a company by domain. Accounts are only created via Account Sourcing,
    so unmatched domains return None and the calling sync skips the record."""
    if not domain or domain in FREE_EMAIL_PROVIDERS:
        return None

    result = await session.execute(
        select(Company).where(Company.domain == domain)
    )
    return result.scalar_one_or_none()


async def _get_or_create_contact_by_email(
    session: AsyncSession,
    email_addr: str,
    display_name: str | None,  # kept for signature compatibility; unused
    company_id: UUID | None,   # kept for signature compatibility; unused
    sync_user_id: UUID,        # kept for signature compatibility; unused
) -> Contact | None:
    """Lookup-only: return the existing contact or None.

    We deliberately do NOT create new Contact rows here. Inbound email sync
    was minting junk contacts from every unknown sender (internal staff,
    newsletters, one-off replies) and polluting the CRM. Contacts are only
    created through explicit flows: CSV import, account sourcing, or the
    admin UI.
    """
    result = await session.execute(
        select(Contact).where(Contact.email == email_addr)
    )
    return result.scalar_one_or_none()


async def _ensure_deal_contact(
    session: AsyncSession,
    deal_id: UUID,
    contact_id: UUID,
) -> None:
    """Link a contact to a deal if not already linked."""
    existing = await session.execute(
        select(DealContact).where(
            DealContact.deal_id == deal_id,
            DealContact.contact_id == contact_id,
        )
    )
    if not existing.scalar_one_or_none():
        session.add(DealContact(deal_id=deal_id, contact_id=contact_id))
        await session.flush()


async def _create_ai_task_for_deal(
    session: AsyncSession,
    deal_id: UUID,
    deal: Deal,
    intent_key: str,
    email_subject: str,
    synced_by_user_id: UUID,
) -> bool:
    """
    Create a system task on a deal based on an AI-detected intent.
    Returns True if a task was created.
    """
    # This generator writes Task(task_type="system") directly (it does not go
    # through refresh_system_tasks_for_entity), so it needs its own gate to
    # honour the manual-tasks-only switch.
    if not settings.ENABLE_SYSTEM_TASKS:
        return False

    # intent_key is either "move_deal_stage:POC_AGREED" or a plain action
    if ":" in intent_key:
        action, target_stage = intent_key.split(":", 1)
    else:
        action = intent_key
        target_stage = None

    system_key = f"personal_email_intent:{intent_key}"

    # Dedup: skip if an open task with this system_key already exists for this deal
    existing = await session.execute(
        select(Task).where(
            Task.entity_type == "deal",
            Task.entity_id == deal_id,
            Task.system_key == system_key,
            Task.status == "open",
        )
    )
    if existing.scalar_one_or_none():
        return False

    # Build title from intent
    title_map = {
        "move_deal_stage": f"Move deal to {(target_stage or '').replace('_', ' ').title()}",
        "send_pricing_package": "Send pricing package to client",
        "book_workshop_session": "Book a meeting / workshop session",
        "follow_up_buyer_thread": "Follow up on unanswered email thread",
    }
    title = title_map.get(action, f"Action required: {action.replace('_', ' ').title()}")
    description = (
        f"Detected from email: \"{email_subject}\"\n\n"
        f"AI identified this conversation as requiring: {title}"
    )
    if target_stage:
        description += f"\n\nSuggested stage move: {target_stage.replace('_', ' ').title()}"

    action_payload = {"action": action}
    if target_stage:
        action_payload["target_stage"] = target_stage

    task = Task(
        title=title,
        description=description,
        entity_type="deal",
        entity_id=deal_id,
        task_type="system",
        system_key=system_key,
        action_payload=action_payload,
        status="open",
        priority="normal",
        assigned_to_id=deal.assigned_to_id,
        created_by_id=synced_by_user_id,
        source="personal_email_sync",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(task)
    await session.flush()
    logger.info(
        "personal_email_sync: created task '%s' for deal %s (intent=%s)",
        title, deal_id, intent_key,
    )
    return True


async def _ensure_meeting_for_deal(
    session: AsyncSession,
    deal: Deal,
    msg: EmailMessage,
    contact_ids: list[UUID],
) -> bool:
    """
    Create a meeting record when an email signals a call was booked.
    Deduped by gmail thread_id so re-syncing won't create duplicates.
    Returns True if a new meeting was created.
    """
    # Deduplicate: one meeting per Gmail thread per deal
    thread_source_id = f"gmail_thread:{msg.thread_id}" if msg.thread_id else f"gmail_msg:{msg.message_id}"
    existing = await session.execute(
        select(Meeting).where(
            Meeting.deal_id == deal.id,
            Meeting.external_source == "personal_email_sync",
            Meeting.external_source_id == thread_source_id,
        )
    )
    if existing.scalar_one_or_none():
        return False

    # Build attendees list from contacts in this thread
    attendees = []
    if contact_ids:
        contacts_result = await session.execute(
            select(
                Contact.id, Contact.first_name, Contact.last_name,
                Contact.email, Contact.title,
            ).where(Contact.id.in_(contact_ids[:6]))
        )
        for row in contacts_result.all():
            attendees.append({
                "contact_id": str(row.id),
                "name": f"{row.first_name} {row.last_name}".strip(),
                "email": row.email or "",
                "title": row.title or "",
            })

    # Infer meeting type from subject
    subject_lower = (msg.subject or "").lower()
    if any(w in subject_lower for w in ["demo", "demo call", "product demo"]):
        meeting_type = "demo"
    elif any(w in subject_lower for w in ["discovery", "intro call", "first call"]):
        meeting_type = "discovery"
    elif any(w in subject_lower for w in ["poc", "pilot", "trial"]):
        meeting_type = "poc"
    elif any(w in subject_lower for w in ["qbr", "business review"]):
        meeting_type = "qbr"
    else:
        meeting_type = "discovery"

    title = msg.subject.strip() if msg.subject and msg.subject.strip() else "Meeting (from email)"
    meeting = Meeting(
        title=title[:200],
        deal_id=deal.id,
        company_id=deal.company_id,
        meeting_type=meeting_type,
        status="scheduled",
        external_source="personal_email_sync",
        external_source_id=thread_source_id,
        attendees=attendees or None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(meeting)
    await session.flush()
    logger.info(
        "personal_email_sync: auto-created meeting '%s' for deal %s (thread=%s)",
        title, deal.id, msg.thread_id,
    )
    return True


# ── Main processing entry point ───────────────────────────────────────────────

async def process_personal_emails(
    session: AsyncSession,
    messages: list[EmailMessage],
    connection: UserEmailConnection,
    sync_user: User,
) -> dict:
    """
    Process a batch of EmailMessages fetched from a user's personal inbox.

    Returns summary dict: {activities_created, contacts_created,
                           companies_created, tasks_created, emails_processed}
    """
    stats = {
        "activities_created": 0,
        "contacts_created": 0,
        "companies_created": 0,
        "tasks_created": 0,
        "emails_processed": 0,
    }

    if not messages:
        return stats

    # Pre-load all company domains for fast lookup (avoid N+1 queries)
    all_companies_result = await session.execute(select(Company))
    company_domain_map: dict[str, tuple[UUID, str, bool]] = {}  # domain → (id, name, trackable)
    all_company_names: list[str] = []
    company_name_candidates: list[tuple[str, UUID, str]] = []
    for row in all_companies_result.scalars().all():
        d = _normalize_domain(row.domain)
        if d:
            company_domain_map[d] = (row.id, row.name, _is_trackable_sales_account(row))
        all_company_names.append(row.name)
        normalized_name = _normalize_name_key(row.name)
        if len(normalized_name) >= 4:
            company_name_candidates.append((normalized_name, row.id, row.name))
    company_name_candidates.sort(key=lambda item: len(item[0]), reverse=True)

    # Pre-load all contact emails for fast lookup
    all_contacts_result = await session.execute(
        select(Contact.id, Contact.email, Contact.first_name, Contact.last_name, Contact.company_id)
    )
    contact_email_map: dict[str, UUID] = {}
    contact_company_map: dict[UUID, UUID | None] = {}
    all_contact_names: list[str] = []
    for row in all_contacts_result.all():
        if row.email:
            contact_email_map[row.email.lower().strip()] = row.id
        contact_company_map[row.id] = row.company_id
        all_contact_names.append(f"{row.first_name} {row.last_name}")

    all_deals_result = await session.execute(
        select(Deal.id, Deal.company_id, Deal.stage)
    )
    deal_company_map: dict[UUID, UUID | None] = {}
    deal_stage_map: dict[UUID, str] = {}
    for row in all_deals_result.all():
        deal_company_map[row.id] = row.company_id
        deal_stage_map[row.id] = row.stage or ""

    touched_deal_ids: set[UUID] = set()
    open_task_counts_before: dict[UUID, int] = {}
    thread_context_cache: dict[tuple[UUID, str], list[str]] = {}

    async def create_email_activity(
        *,
        msg: EmailMessage,
        deal_id: UUID | None,
        contact_id: UUID | None,
        ai_summary: str | None,
        latest_message_text: str,
        thread_context_excerpt: str = "",
        thread_latest_intent: str | None = None,
        google_doc_contexts: list[dict] | None = None,
        google_doc_transcript: str = "",
    ) -> bool:
        dedup_filters = [
            Activity.email_message_id == msg.message_id,
            Activity.created_by_id == sync_user.id,
        ]
        if deal_id:
            dedup_filters.append(Activity.deal_id == deal_id)
        else:
            dedup_filters.append(Activity.deal_id.is_(None))
        existing = await session.execute(select(Activity.id).where(and_(*dedup_filters)))
        if existing.first():
            return False

        activity = Activity(
            type="email",
            source="personal_email_sync",
            medium="email",
            deal_id=deal_id,
            contact_id=contact_id,
            content=msg.body_text[:2000] if msg.body_text else None,
            ai_summary=ai_summary,
            email_message_id=msg.message_id,
            email_subject=msg.subject,
            email_from=_normalize_beacon_sender(msg.from_addr),
            email_to=", ".join(msg.to_addrs),
            email_cc=", ".join(msg.cc_addrs),
            created_by_id=sync_user.id,
            created_at=_parse_message_datetime(msg.date),
            event_metadata={
                "synced_by_user_id": str(sync_user.id),
                "synced_by_email": connection.email_address,
                "raw_email_from": msg.from_addr,
                "gmail_thread_id": msg.thread_id or None,
                "intent_detected": thread_latest_intent,
                "thread_latest_intent": thread_latest_intent,
                "thread_latest_message_text": latest_message_text[:2500],
                "thread_context_excerpt": thread_context_excerpt,
                "google_doc_links": [context["url"] for context in (google_doc_contexts or [])] or None,
                "google_doc_transcript": google_doc_transcript[:4000] or None,
                "crm_match_scope": "deal" if deal_id else "account_or_contact",
            },
        )
        session.add(activity)
        stats["activities_created"] += 1
        if deal_id:
            touched_deal_ids.add(deal_id)
        return True

    for msg in messages:
        stats["emails_processed"] += 1
        if _is_automated_email(msg):
            continue

        user_domain = _domain_from_email(connection.email_address)

        # Collect all addresses in this message, excluding the user's own address
        all_addrs: set[str] = set()
        all_addrs.add(msg.from_addr)
        all_addrs.update(msg.to_addrs)
        all_addrs.update(msg.cc_addrs)
        all_addrs = {
            addr.strip().lower()
            for addr in all_addrs
            if addr and addr.strip()
        }
        all_addrs.discard(connection.email_address.lower())
        # Never use internal rep addresses for CRM entity matching. Internal
        # contacts may be linked on deals for collaboration, but they should
        # not cause personal inbox sync to attach external threads to those deals.
        all_addrs = {
            addr for addr in all_addrs
            if not _is_internal_address(addr, user_domain)
        }

        if not all_addrs:
            continue

        # Dedup BEFORE any AI spend (mirrors app/tasks/email_sync.py): if this
        # sync user already logged this message — with or without a deal — the
        # downstream create_email_activity would detect the duplicate and
        # return False anyway, but only AFTER the pass-4 Haiku classification
        # and the Haiku summary were re-billed. Skip the message up-front so
        # already-processed mail costs nothing. The dedup check inside
        # create_email_activity stays as the second line of defence, and the
        # final DB state / stats counters match the old duplicate path
        # (emails_processed was already incremented above; activities_created
        # and touched_deal_ids were never bumped for duplicates).
        already_logged = await session.execute(
            select(Activity.id).where(
                and_(
                    Activity.email_message_id == msg.message_id,
                    Activity.created_by_id == sync_user.id,
                )
            )
        )
        if already_logged.first():
            continue

        google_doc_contexts, updated_token = await fetch_google_doc_context(
            msg.body_text,
            token_data=connection.token_data,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
        )
        if updated_token is not connection.token_data:
            connection.token_data = updated_token
        google_doc_transcript = "\n\n".join(context["text"] for context in google_doc_contexts if context.get("text")).strip()
        latest_message_text = "\n".join(
            part for part in [msg.subject or "", msg.body_text or "", google_doc_transcript] if part
        ).strip()

        # ── Pass 1: exact email address match → contact → deal ──────────────
        matched_contact_ids: list[UUID] = []
        matched_company_id: UUID | None = None
        deterministic_account_match_id: UUID | None = None
        for addr in all_addrs:
            cid = contact_email_map.get(addr)
            if cid:
                matched_contact_ids.append(cid)
                if not matched_company_id:
                    matched_company_id = contact_company_map.get(cid)
                if not deterministic_account_match_id:
                    deterministic_account_match_id = contact_company_map.get(cid)

        deal_ids: list[UUID] = []
        meeting_candidate_deal_id: UUID | None = None
        if matched_contact_ids:
            dc_result = await session.execute(
                select(DealContact.deal_id).where(
                    DealContact.contact_id.in_(matched_contact_ids)
                ).distinct()
            )
            matched_deal_ids = [row.deal_id for row in dc_result.all()]
            unique_deal_ids = list(dict.fromkeys(matched_deal_ids))
            if len(unique_deal_ids) == 1:
                deal_ids = unique_deal_ids
                meeting_candidate_deal_id = unique_deal_ids[0]

        # ── Pass 2: company domain match (only when one active deal is clear) ─
        if not deal_ids:
            external_domains = {
                _domain_from_email(addr)
                for addr in all_addrs
                if _domain_from_email(addr)
            }
            # Remove the user's own company domain (don't match internal mail)
            external_domains.discard(user_domain)

            domain_matched_company_ids: set[UUID] = set()
            company_domain_deal_candidates: set[UUID] = set()
            for domain in external_domains:
                if domain in company_domain_map:
                    company_id, _, is_trackable_account = company_domain_map[domain]
                    matched_company_id = company_id
                    if is_trackable_account:
                        deterministic_account_match_id = company_id
                    domain_matched_company_ids.add(company_id)
                    # Find deals linked to this company
                    deal_result = await session.execute(
                        select(Deal.id, Deal.assigned_to_id, Deal.stage).where(
                            Deal.company_id == company_id
                        )
                    )
                    for row in deal_result.all():
                        company_domain_deal_candidates.add(row.id)
            if len(domain_matched_company_ids) == 1:
                active_company_deal_ids = [
                    deal_id for deal_id in company_domain_deal_candidates
                    if _is_active_deal_stage(deal_stage_map.get(deal_id))
                ]
                if len(active_company_deal_ids) == 1:
                    deal_ids = [active_company_deal_ids[0]]
                    meeting_candidate_deal_id = active_company_deal_ids[0]

        # Ambiguity guard: if the message name-drops 2+ CRM companies it's a
        # blast/list (e.g. a conference exhibitor roundup), not a 1:1 thread.
        # Both the text-match and AI passes infer "the company this is about"
        # from content, so a blast would wrongly attach to whichever company it
        # happened to mention. Skip content-based attachment entirely for these.
        is_multi_company_blast = (
            _count_distinct_company_mentions(
                f"{msg.subject}\n{msg.body_text}", company_name_candidates
            )
            >= 2
        )

        if not deal_ids and not is_multi_company_blast and (msg.subject or msg.body_text):
            company_match = _match_company_from_text(
                f"{msg.subject}\n{msg.body_text}",
                company_name_candidates,
            )
            if company_match:
                matched_company_id, _ = company_match
                deal_result = await session.execute(
                    select(Deal.id, Deal.stage).where(Deal.company_id == matched_company_id)
                )
                active_company_deal_ids = [
                    row.id for row in deal_result.all()
                    if _is_active_deal_stage(row.stage)
                ]
                if len(active_company_deal_ids) == 1:
                    deal_ids = [active_company_deal_ids[0]]
                    meeting_candidate_deal_id = active_company_deal_ids[0]

        # ── Pass 4: AI classification fallback ───────────────────────────────
        if not deal_ids and not is_multi_company_blast and (msg.subject or msg.body_text):
            ai_result = await _ai_classify_email(
                subject=msg.subject,
                body=msg.body_text,
                company_names=all_company_names,
                contact_names=all_contact_names,
            )
            if ai_result:
                ai_company = (ai_result.get("company_name") or "").strip()
                if ai_company:
                    # Try to find a matching company by name (case-insensitive)
                    comp_result = await session.execute(
                        select(Company.id).where(
                            Company.name.ilike(ai_company)
                        )
                    )
                    comp_row = comp_result.scalar_one_or_none()
                    if comp_row:
                        matched_company_id = comp_row
                        deal_result = await session.execute(
                            select(Deal.id, Deal.stage).where(Deal.company_id == comp_row)
                        )
                        active_company_deal_ids = [
                            row.id for row in deal_result.all()
                            if _is_active_deal_stage(row.stage)
                        ]
                        if len(active_company_deal_ids) == 1:
                            deal_ids = [active_company_deal_ids[0]]
                            meeting_candidate_deal_id = active_company_deal_ids[0]

        if not deal_ids:
            # No match found — still may need gap-fill (new contact from external domain)
            await _gap_fill_contacts(
                session, msg, all_addrs, connection, sync_user.id,
                company_domain_map, contact_email_map, stats,
                matched_company_id=deterministic_account_match_id,
            )
            if not deterministic_account_match_id and not matched_contact_ids:
                continue

            refreshed_contact_ids: list[UUID] = []
            for addr in all_addrs:
                cid = contact_email_map.get(addr)
                if cid:
                    refreshed_contact_ids.append(cid)
            sender_contact_id = None
            if not _is_internal_address(msg.from_addr, user_domain):
                sender_contact_id = contact_email_map.get(msg.from_addr)
            elif refreshed_contact_ids:
                sender_contact_id = refreshed_contact_ids[0]

            ai_summary = await _generate_email_summary(msg.subject, msg.body_text)
            await create_email_activity(
                msg=msg,
                deal_id=None,
                contact_id=sender_contact_id or (refreshed_contact_ids[0] if refreshed_contact_ids else None),
                ai_summary=ai_summary,
                latest_message_text=latest_message_text,
                google_doc_contexts=google_doc_contexts,
                google_doc_transcript=google_doc_transcript,
            )
            continue

        # ── Gap-fill: create missing contacts ────────────────────────────────
        newly_created_contact_ids: list[tuple[UUID, UUID | None]] = []  # (contact_id, deal_id_hint)
        for addr in all_addrs:
            if addr in contact_email_map:
                continue
            domain = _domain_from_email(addr)
            if not domain:
                continue
            company_id: UUID | None = matched_company_id
            if not company_id and domain in company_domain_map:
                company_id = company_domain_map[domain][0]
            elif not company_id:
                # Match the sender domain to an existing account; if no match,
                # the message syncs with company_id=NULL.
                company = await _get_or_create_company_by_domain(
                    session, domain,
                    suggested_name=None,
                )
                if company:
                    company_id = company.id
                    company_domain_map[domain] = (company.id, company.name, _is_trackable_sales_account(company))
            display_name = msg.from_name if addr == msg.from_addr else None
            contact = await _get_or_create_contact_by_email(
                session, addr, display_name, company_id, sync_user.id,
            )
            if not contact:
                continue
            contact_email_map[addr] = contact.id
            matched_contact_ids.append(contact.id)
            stats["contacts_created"] += 1
            newly_created_contact_ids.append((contact.id, deal_ids[0] if deal_ids else None))

        # Link new contacts to deals
        for contact_id, _ in newly_created_contact_ids:
            for deal_id in deal_ids:
                await _ensure_deal_contact(session, deal_id, contact_id)

        # ── Activity logging ──────────────────────────────────────────────────
        sender_contact_id: UUID | None = None
        if not _is_internal_address(msg.from_addr, user_domain):
            sender_contact_id = contact_email_map.get(msg.from_addr)
        elif matched_contact_ids:
            sender_contact_id = matched_contact_ids[0]
        ai_summary = await _generate_email_summary(msg.subject, msg.body_text)

        for deal_id in deal_ids:
            deal = await session.get(Deal, deal_id)
            if not deal:
                continue

            if deal_id not in open_task_counts_before:
                open_task_counts_before[deal_id] = await _count_open_system_tasks(session, deal_id)

            thread_cache_key = (deal_id, msg.thread_id or msg.message_id)
            if thread_cache_key not in thread_context_cache:
                thread_context_cache[thread_cache_key] = await _load_existing_thread_segments(
                    session,
                    deal_id=deal_id,
                    thread_id=msg.thread_id or msg.message_id,
                )
            thread_segments = [*thread_context_cache[thread_cache_key], latest_message_text]
            thread_latest_intent = detect_latest_intent_from_segments(thread_segments)
            thread_context_excerpt = "\n\n".join(thread_segments[-4:])[:4000]

            await create_email_activity(
                msg=msg,
                deal_id=deal_id,
                contact_id=sender_contact_id,
                ai_summary=ai_summary,
                latest_message_text=latest_message_text,
                thread_context_excerpt=thread_context_excerpt,
                thread_latest_intent=thread_latest_intent,
                google_doc_contexts=google_doc_contexts,
                google_doc_transcript=google_doc_transcript,
            )
            thread_context_cache[thread_cache_key] = thread_segments

            # Intentionally NOT creating a Meeting row from email threads anymore.
            # The old _ensure_meeting_for_deal() path was minting empty Meeting
            # rows (no transcript, no recording, no notes — just the email
            # subject as title) and polluting the Meetings page with entries
            # that aren't actually meetings.  Emails already land as Activities
            # on the deal, which is the correct place for them.  A real meeting
            # row should come from a real meeting source (tldv, Google Calendar),
            # never from inbox parsing.

    await session.commit()
    for deal_id in touched_deal_ids:
        await refresh_system_tasks_for_entity(session, "deal", deal_id)
        tasks_after = await _count_open_system_tasks(session, deal_id)
        stats["tasks_created"] += max(0, tasks_after - open_task_counts_before.get(deal_id, 0))
    await session.commit()
    return stats


async def _gap_fill_contacts(
    session: AsyncSession,
    msg: EmailMessage,
    all_addrs: set[str],
    connection: UserEmailConnection,
    sync_user_id: UUID,
    company_domain_map: dict[str, tuple[UUID, str, bool]],
    contact_email_map: dict[str, UUID],
    stats: dict,
    matched_company_id: UUID | None = None,
) -> None:
    """
    When no deal match is found, still capture new stakeholders by:
      1. attaching them to a company inferred from the conversation text
      2. attaching them to a known company by email domain
      3. auto-creating a stub company from a corporate domain
    """
    user_domain = _domain_from_email(connection.email_address)
    for addr in all_addrs:
        domain = _domain_from_email(addr)
        if not domain or domain == user_domain:
            continue

        contact_result = await session.execute(
            select(Contact.id).where(Contact.email == addr)
        )
        if contact_result.scalar_one_or_none():
            continue

        company_id = matched_company_id
        if not company_id and domain in company_domain_map:
            company_id = company_domain_map[domain][0]
        elif not company_id:
            company = await _get_or_create_company_by_domain(session, domain)
            if company:
                company_id = company.id
                company_domain_map[domain] = (company.id, company.name, _is_trackable_sales_account(company))
        if not company_id and domain in FREE_EMAIL_PROVIDERS:
            continue

        display_name = msg.from_name if addr == msg.from_addr else None
        contact = await _get_or_create_contact_by_email(
            session, addr, display_name, company_id, sync_user_id,
        )
        if not contact:
            continue
        contact_email_map[addr] = contact.id
        stats["contacts_created"] += 1
