"""
Celery task: poll Gmail shared inbox and create deal activities.

Flow:
  1. Read last_sync_epoch from Redis (or default to 1 hour ago)
  2. Fetch new emails from Gmail API via after:EPOCH query
  3. For each email:
     a. Extract all email addresses (from/to/cc)
     b. Match them against contacts table
     c. Find deals linked to those contacts via deal_contacts
     d. Deduplicate using email_message_id + deal_id
     e. Create Activity records with type="email"
     f. Optionally generate AI summary via Claude Haiku
  4. Update last_sync_epoch in Redis
"""
import asyncio
import logging
import time
from email.utils import parseaddr

from app.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)

REDIS_KEY_LAST_SYNC = "email_sync:last_epoch"


def _split_inbox_parts(inbox: str) -> tuple[str, str]:
    _name, addr = parseaddr(inbox or "")
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return "", ""
    local, domain = addr.split("@", 1)
    return local, domain


def _extract_deal_aliases(addresses: set[str], inbox: str) -> list[str]:
    local, domain = _split_inbox_parts(inbox)
    if not local or not domain:
        return []

    aliases: list[str] = []
    prefix = f"{local}+"
    for addr in addresses:
        normalized = (addr or "").strip().lower()
        if not normalized or "@" not in normalized:
            continue
        addr_local, addr_domain = normalized.split("@", 1)
        if addr_domain != domain:
            continue
        if not addr_local.startswith(prefix):
            continue
        alias = addr_local[len(prefix):].strip()
        if alias:
            aliases.append(alias)
    return list(dict.fromkeys(aliases))


def _normalize_domain(value: str | None) -> str:
    domain = (value or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _infer_name_from_email(addr: str) -> tuple[str, str]:
    local = (addr.split("@", 1)[0] if "@" in addr else addr).strip()
    parts = [part for part in local.replace("_", ".").replace("-", ".").split(".") if part]
    if not parts:
        return "Unknown", "Contact"
    if len(parts) == 1:
        return parts[0].title(), "Contact"
    return parts[0].title(), " ".join(part.title() for part in parts[1:])


def _build_display_name(addr: str, explicit_name: str | None = None) -> str:
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()
    first, last = _infer_name_from_email(addr)
    return f"{first} {last}".strip()


async def _notify_unmatched_zippy_alias(session, msg, aliases: list[str]) -> None:
    """Zippy-only mode: a ``zippy+<alias>`` address matched no deal.

    Tell the sender (if they're a Beacon user) so they can create the deal /
    fix the CC. We deliberately do NOT auto-create a deal — a typo'd alias
    shouldn't spawn a junk deal — and we don't silently drop it: the rep is
    notified. The notification is deduped per message so re-runs don't spam.
    """
    from sqlalchemy import func, select

    from app.models.user import User
    from app.services.notifications import create_notification

    sender = (msg.from_addr or "").strip().lower()
    if not sender or not aliases:
        return
    user = (
        await session.execute(select(User).where(func.lower(User.email) == sender))
    ).scalars().first()
    if not user:
        return  # external sender — no in-app inbox to notify
    alias_str = ", ".join(aliases)
    try:
        await create_notification(
            session,
            user_id=user.id,
            type="zippy_email_unmatched",
            title="Email not tracked — no matching deal",
            body=(
                f'Your email "{(msg.subject or "(no subject)")[:120]}" was sent to '
                f"zippy+{alias_str} but no deal owns that address. Create the deal, then "
                f"add its Zippy CC address — emails will track from then on."
            ),
            dedup_key=f"zippy_unmatched:{msg.message_id}",
            push=False,
        )
    except Exception:
        logger.exception("Failed to notify sender of unmatched zippy alias %s", alias_str)


@celery_app.task(
    name="app.tasks.email_sync.sync_gmail_inbox",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def sync_gmail_inbox(self) -> dict:
    """Poll shared Gmail inbox and log emails as deal activities."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_async_sync())
        finally:
            loop.close()
        return result
    except Exception as exc:
        logger.error(f"Email sync failed: {exc}")
        raise self.retry(exc=exc)


async def _async_sync() -> dict:
    import redis
    from sqlalchemy import and_, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.clients.google_docs import fetch_google_doc_context
    from app.clients.gmail_inbox import GmailInboxClient
    from app.models.activity import Activity
    from app.models.company import Company
    from app.models.contact import Contact
    from app.models.deal import Deal, DealContact
    from app.models.settings import WorkspaceSettings
    from app.services.activity_signal_classifier import detect_latest_intent_from_segments
    from app.services.personal_email_sync import _load_existing_thread_segments
    from app.services.tasks import refresh_system_tasks_for_entity

    # Fresh engine per task
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Redis for cursor tracking
    r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    try:
        async with SessionLocal() as session:
            settings_row = await session.get(WorkspaceSettings, 1)
            inbox = (
                (settings_row.gmail_shared_inbox if settings_row and settings_row.gmail_shared_inbox else settings.GMAIL_SHARED_INBOX).strip()
                if (settings_row and settings_row.gmail_shared_inbox) or settings.GMAIL_SHARED_INBOX
                else ""
            )
            token_payload = settings_row.gmail_token_data if settings_row and settings_row.gmail_token_data else None
            # Zippy-only mode (default off): when on, an email is tracked ONLY
            # if it's CC'd to zippy+<deal-alias>. Stored in the existing
            # sync_schedule_settings JSON so the cutover needs no DB migration.
            zippy_only = bool((settings_row.sync_schedule_settings or {}).get("zippy_only_email_sync")) if settings_row else False

        if not inbox or not (token_payload or settings.GMAIL_TOKEN_JSON):
            return {"status": "skipped", "reason": "gmail not connected"}

        # Get last sync timestamp (default: 1 hour ago)
        last_epoch_str = r.get(REDIS_KEY_LAST_SYNC)
        if last_epoch_str:
            last_epoch = int(last_epoch_str)
        else:
            last_epoch = int(time.time()) - 3600

        current_epoch = int(time.time())

        # Fetch new emails
        gmail = GmailInboxClient(inbox=inbox, token_payload=token_payload)
        messages = gmail.fetch_new_messages(after_epoch=last_epoch, max_results=50)

        if not messages:
            if gmail.updated_token_payload:
                async with SessionLocal() as session:
                    settings_row = await session.get(WorkspaceSettings, 1)
                    if settings_row:
                        settings_row.gmail_token_data = gmail.updated_token_payload
                        settings_row.gmail_last_error = None
                        session.add(settings_row)
                        await session.commit()
            r.set(REDIS_KEY_LAST_SYNC, str(current_epoch))
            return {"status": "completed", "emails_found": 0, "activities_created": 0}

        activities_created = 0
        touched_deal_ids: set = set()
        thread_context_cache: dict[tuple[str, str], list[str]] = {}

        async with SessionLocal() as session:
            for msg in messages:
                # Collect all email addresses from this message
                all_addrs = set()
                all_addrs.add(msg.from_addr)
                all_addrs.update(msg.to_addrs)
                all_addrs.update(msg.cc_addrs)
                # Remove the shared inbox address itself
                all_addrs.discard(inbox.lower())
                all_addrs.discard("")

                if not all_addrs:
                    continue

                matched_via = "contacts"
                matched_aliases = _extract_deal_aliases(all_addrs, inbox)
                deal_ids: list = []

                # Zippy-only mode: a message is tracked ONLY if it carries a
                # zippy+<deal-alias> address. No alias → not opted-in; skip
                # without falling back to contact-email matching.
                if zippy_only and not matched_aliases:
                    continue

                if matched_aliases:
                    matched_via = "alias"
                    deal_rows = await session.execute(
                        select(Deal.id).where(Deal.email_cc_alias.in_(matched_aliases))
                    )
                    deal_ids = list(dict.fromkeys([row.id for row in deal_rows.all()]))
                    if not deal_ids:
                        # Alias present but no deal owns it. In zippy-only mode
                        # tell the sender so they can fix it — we never auto-
                        # create a deal (a CC typo shouldn't spawn junk).
                        if zippy_only:
                            await _notify_unmatched_zippy_alias(session, msg, matched_aliases)
                        logger.warning(
                            "Email sync skipped message %s because alias %s did not map to a deal",
                            msg.message_id,
                            ", ".join(matched_aliases),
                        )
                        continue

                # Find matching contacts
                contact_result = await session.execute(
                    select(Contact.id, Contact.email, Contact.company_id).where(
                        Contact.email.in_(list(all_addrs))
                    )
                )
                matched_contacts = contact_result.all()

                if not matched_contacts and not deal_ids:
                    continue

                contact_ids = [c.id for c in matched_contacts]
                matched_contact_emails = {str(c.email or "").strip().lower() for c in matched_contacts if c.email}

                # Find deals linked to these contacts
                if not deal_ids and contact_ids:
                    deal_result = await session.execute(
                        select(DealContact.deal_id).where(
                            DealContact.contact_id.in_(contact_ids)
                        ).distinct()
                    )
                    deal_ids = [row.deal_id for row in deal_result.all()]

                if not deal_ids:
                    continue

                # Determine sender contact (for activity.contact_id)
                sender_contact_id = None
                for c in matched_contacts:
                    if c.email == msg.from_addr:
                        sender_contact_id = c.id
                        break

                # Batch-fetch all linked Deals and their Companies in two queries
                # instead of N individual session.get() calls (avoids N+1 pattern).
                deals_result = await session.execute(
                    select(Deal).where(Deal.id.in_(deal_ids))
                )
                deals_by_id: dict = {str(deal.id): deal for deal in deals_result.scalars().all()}
                company_ids = list({deal.company_id for deal in deals_by_id.values() if deal and deal.company_id})
                companies_result = await session.execute(
                    select(Company).where(Company.id.in_(company_ids))
                ) if company_ids else None
                companies_by_id: dict = {str(c.id): c for c in (companies_result.scalars().all() if companies_result else [])}

                # Dedup check: skip this message entirely if already logged for all linked deals.
                # This avoids calling Claude when the email is already fully processed.
                new_deal_ids = []
                for deal_id in deal_ids:
                    existing = await session.execute(
                        select(Activity.id).where(
                            and_(
                                Activity.email_message_id == msg.message_id,
                                Activity.deal_id == deal_id,
                            )
                        )
                    )
                    if not existing.first():
                        new_deal_ids.append(deal_id)

                if not new_deal_ids:
                    continue  # All deals already have this email — skip Claude entirely

                # Only call Claude and Google Docs after confirming at least one deal needs this email
                google_doc_contexts, updated_token = await fetch_google_doc_context(
                    msg.body_text,
                    token_data=gmail.updated_token_payload or token_payload,
                    client_id=settings.gmail_client_id,
                    client_secret=settings.gmail_client_secret,
                )
                if updated_token and updated_token != (gmail.updated_token_payload or token_payload):
                    gmail.updated_token_payload = updated_token
                google_doc_transcript = "\n\n".join(context["text"] for context in google_doc_contexts if context.get("text")).strip()
                latest_message_text = "\n".join(
                    part for part in [msg.subject or "", msg.body_text or "", google_doc_transcript] if part
                ).strip()

                ai_summary = None
                if len(msg.body_text) >= settings.EMAIL_SUMMARY_MIN_CHARS:
                    ai_summary = await _summarize_email(msg.subject, msg.body_text)

                # Create activity for each deal that doesn't already have this email
                for deal_id in new_deal_ids:
                    deal = deals_by_id.get(str(deal_id))
                    company = companies_by_id.get(str(deal.company_id)) if deal and deal.company_id else None
                    company_domain = _normalize_domain(company.domain if company else None)
                    linked_contact_rows = (
                        await session.execute(
                            select(Contact.id, Contact.email)
                            .join(DealContact, DealContact.contact_id == Contact.id)
                            .where(DealContact.deal_id == deal_id)
                        )
                    ).all()
                    linked_contact_ids = {row.id for row in linked_contact_rows}
                    linked_contact_emails = {
                        str(row.email or "").strip().lower() for row in linked_contact_rows if row.email
                    }
                    suggested_existing_contacts = []
                    for contact in matched_contacts:
                        if matched_via != "alias":
                            continue
                        if contact.id in linked_contact_ids:
                            continue
                        suggested_existing_contacts.append({
                            "contact_id": str(contact.id),
                            "email": str(contact.email or "").strip().lower(),
                        })

                    suggested_new_participants = []
                    if matched_via == "alias" and company_domain and not company_domain.endswith(".unknown"):
                        for addr in sorted(all_addrs):
                            normalized_addr = (addr or "").strip().lower()
                            if not normalized_addr or "@" not in normalized_addr:
                                continue
                            if normalized_addr in linked_contact_emails or normalized_addr in matched_contact_emails:
                                continue
                            addr_domain = _normalize_domain(normalized_addr.split("@", 1)[1])
                            if addr_domain != company_domain:
                                continue
                            display_name = _build_display_name(
                                normalized_addr,
                                msg.from_name if normalized_addr == msg.from_addr else None,
                            )
                            first_name, last_name = _infer_name_from_email(normalized_addr)
                            suggested_new_participants.append({
                                "email": normalized_addr,
                                "display_name": display_name,
                                "first_name": first_name,
                                "last_name": last_name,
                            })

                    thread_cache_key = (str(deal_id), msg.thread_id or msg.message_id)
                    if thread_cache_key not in thread_context_cache:
                        thread_context_cache[thread_cache_key] = await _load_existing_thread_segments(
                            session,
                            deal_id=deal_id,
                            thread_id=msg.thread_id or msg.message_id,
                        )
                    thread_segments = [*thread_context_cache[thread_cache_key], latest_message_text]
                    thread_latest_intent = detect_latest_intent_from_segments(thread_segments)
                    thread_context_excerpt = "\n\n".join(thread_segments[-4:])[:4000]

                    activity = Activity(
                        type="email",
                        source="gmail_sync",
                        deal_id=deal_id,
                        contact_id=sender_contact_id,
                        content=msg.body_text[:2000] if msg.body_text else None,
                        ai_summary=ai_summary,
                        email_message_id=msg.message_id,
                        email_subject=msg.subject,
                        email_from=msg.from_addr,
                        email_to=", ".join(msg.to_addrs),
                        email_cc=", ".join(msg.cc_addrs),
                        event_metadata={
                            "matched_via": matched_via,
                            "matched_aliases": matched_aliases or None,
                            "gmail_thread_id": msg.thread_id or None,
                            "suggested_existing_contacts": suggested_existing_contacts or None,
                            "suggested_new_participants": suggested_new_participants or None,
                            "thread_latest_intent": thread_latest_intent,
                            "thread_latest_message_text": latest_message_text[:2500],
                            "thread_context_excerpt": thread_context_excerpt,
                            "google_doc_links": [context["url"] for context in google_doc_contexts] or None,
                            "google_doc_transcript": google_doc_transcript[:4000] or None,
                        },
                    )
                    session.add(activity)
                    activities_created += 1
                    touched_deal_ids.add(deal_id)
                    thread_context_cache[thread_cache_key] = thread_segments

            settings_row = await session.get(WorkspaceSettings, 1)
            if settings_row:
                if gmail.updated_token_payload:
                    settings_row.gmail_token_data = gmail.updated_token_payload
                settings_row.gmail_last_error = None
                session.add(settings_row)

            await session.commit()

            for deal_id in touched_deal_ids:
                await refresh_system_tasks_for_entity(session, "deal", deal_id)
            await session.commit()

        # Update cursor
        r.set(REDIS_KEY_LAST_SYNC, str(current_epoch))

        logger.info(f"Email sync complete: {len(messages)} emails → {activities_created} activities")
        return {
            "status": "completed",
            "emails_found": len(messages),
            "activities_created": activities_created,
        }
    except Exception as exc:
        async with SessionLocal() as session:
            settings_row = await session.get(WorkspaceSettings, 1)
            if settings_row:
                settings_row.gmail_last_error = str(exc)[:500]
                session.add(settings_row)
                await session.commit()
        raise

    finally:
        await engine.dispose()
        r.close()


async def _summarize_email(subject: str, body: str) -> str | None:
    """Generate a concise 1-line email summary using Claude Haiku."""
    if not settings.claude_api_key:
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
                    "Summarize this sales email in one short sentence (max 15 words). "
                    "Focus on the key action or decision.\n\n"
                    f"Subject: {subject}\n\n"
                    f"{body[:1500]}"
                ),
            }],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.warning(f"Email summary failed: {e}")
        return None
