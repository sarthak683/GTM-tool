"""
ae_meeting_reminder — Daily Google Chat reminder for AE account meetings.

Celery beat fires this every day at 6 PM IST (12:30 UTC).

Logic
-----
1. Load zippy@beacon.li's calendar OAuth token from WorkspaceSettings.
2. Fetch all events starting tomorrow (IST date) from that calendar.
3. Keep only events that fall on tomorrow's IST date.
4. For each event, match attendee email domains and event title against CRM
   companies (same 2-pass approach as calendar_sync.py).
5. Apply filter:
     INCLUDE  → company in CRM AND (deal stage == demo_scheduled OR no deal at all)
     SKIP     → company in CRM AND deal exists at any other stage
     SKIP     → company not found in CRM
6. Get the AE name from the first attendee whose email ends in @beacon.li
   (falls back to the organizer if no internal attendee is found).
7. Build one combined message and POST to the configured Google Chat webhook.

Message format
--------------
    📅 Tomorrow's Account Meetings — <date>

    Company Name: Acme Corp
    Time: 10:00 AM
    AE: Rakesh

    ---

    Company Name: XYZ Ltd
    Time: 2:00 PM
    AE: Annie

    ---
    📎 Please attach your Account Research Document before the meeting.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
BEACON_DOMAIN = "beacon.li"
FREE_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "icloud.com", "protonmail.com", "googlemail.com",
}

# Deal stages that mean "a real pipeline deal exists, skip this meeting"
_PIPELINE_STAGES_TO_SKIP = frozenset([
    "demo_done", "qualified_lead", "poc_agreed", "poc_wip", "poc_done",
    "commercial_negotiation", "msa_review", "workshop",
    "closed_won", "closed_lost",
])
_INCLUDE_STAGE = "demo_scheduled"


# ── helpers ──────────────────────────────────────────────────────────────────

def _normalize(value: str) -> str:
    """Lower-case and collapse whitespace/punctuation for fuzzy name matching."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return " ".join(cleaned.split())


def _domain_from_email(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1].lower().strip()


def _is_internal(email: str) -> bool:
    return _domain_from_email(email) == BEACON_DOMAIN


async def _get_ae_name(
    session: AsyncSession,
    attendee_emails: list[str],
    organizer_email: str,
    assigned_to_id=None,
) -> str:
    """Return the AE's first name.

    Priority:
    1. Deal's assigned_to user (most reliable — set by the team in CRM)
    2. First @beacon.li attendee found in the Users table
    3. Organizer if @beacon.li
    4. Fallback "AE"
    """
    from app.models.user import User  # local import to avoid circular

    # 1. Use deal's assigned AE if available
    if assigned_to_id:
        user = (
            await session.execute(select(User).where(User.id == assigned_to_id))
        ).scalar_one_or_none()
        if user:
            return user.name.split()[0] if user.name else user.email

    # 2. First @beacon.li attendee
    for email in attendee_emails:
        if _is_internal(email):
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user:
                return user.name.split()[0] if user.name else user.email

    # 3. Fallback to organizer
    if _is_internal(organizer_email):
        user = (
            await session.execute(select(User).where(User.email == organizer_email))
        ).scalar_one_or_none()
        if user:
            return user.name.split()[0] if user.name else user.email

    return "AE"


async def _match_company(
    session: AsyncSession,
    attendee_emails: list[str],
    title: str,
):
    """Return the CRM Company matching this event, or None.

    Pass 1: attendee email domain → company.domain
    Pass 2: event title text contains a company name
    """
    from app.models.company import Company  # local import

    # Pass 1 — domain match
    external_domains = {
        _domain_from_email(e)
        for e in attendee_emails
        if not _is_internal(e) and _domain_from_email(e) not in FREE_EMAIL_PROVIDERS
    }
    if external_domains:
        for domain in external_domains:
            company = (
                await session.execute(
                    select(Company).where(Company.domain == domain)
                )
            ).scalar_one_or_none()
            if company:
                return company

    # Pass 2 — title text match against known company names
    title_key = _normalize(title)
    if len(title_key) < 4:
        return None

    result = await session.execute(select(Company.id, Company.name, Company.domain))
    rows = result.all()
    # Sort longest name first so "Procore Technologies" beats "Procore"
    rows_sorted = sorted(rows, key=lambda r: len(r.name or ""), reverse=True)

    for company_id, company_name, company_domain in rows_sorted:
        cname_key = _normalize(company_name or "")
        if len(cname_key) < 4:
            continue
        # Whole-word / substring match
        pattern = r"(?<![a-z0-9])" + re.escape(cname_key) + r"(?![a-z0-9])"
        if re.search(pattern, title_key):
            company = (
                await session.execute(
                    select(Company).where(Company.id == company_id)
                )
            ).scalar_one_or_none()
            if company:
                return company

    return None


async def _get_deal_stage_for_company(
    session: AsyncSession,
    company_id,
) -> tuple[str | None, object | None]:
    """Return (deal_stage, assigned_to_id) for the company's deal, or (None, None)."""
    from app.models.deal import Deal  # local import

    deal = (
        await session.execute(
            select(Deal).where(Deal.company_id == company_id).limit(1)
        )
    ).scalar_one_or_none()
    if deal:
        return deal.stage, deal.assigned_to_id
    return None, None


async def _async_send_ae_meeting_reminder() -> dict:
    from app.clients.google_calendar import fetch_upcoming_events
    from app.models.settings import WorkspaceSettings

    webhook_url = settings.GOOGLE_CHAT_WEBHOOK_URL
    if not webhook_url:
        logger.warning("ae_meeting_reminder: GOOGLE_CHAT_WEBHOOK_URL not set — skipping")
        return {"status": "skipped", "reason": "no_webhook_url"}

    # Fresh engine per task (same pattern as email_sync, enrichment, etc.)
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ── load zippy's calendar token ──────────────────────────────────────────
    async with SessionLocal() as session:
        ws_row = (
            await session.execute(select(WorkspaceSettings).limit(1))
        ).scalar_one_or_none()

    if not ws_row or not ws_row.zippy_calendar_token_data:
        logger.warning("ae_meeting_reminder: zippy_calendar_token_data not set — skipping")
        return {"status": "skipped", "reason": "no_calendar_token"}

    token_data = ws_row.zippy_calendar_token_data

    # ── fetch calendar events for the next 2 days (covers "tomorrow" in IST) ─
    # Use dedicated zippy calendar credentials if set, otherwise fall back to
    # the shared Gmail OAuth client (allows local dev to work without extra vars).
    cal_client_id = settings.ZIPPY_CALENDAR_CLIENT_ID or settings.GMAIL_CLIENT_ID
    cal_client_secret = settings.ZIPPY_CALENDAR_CLIENT_SECRET or settings.GMAIL_CLIENT_SECRET

    events, updated_token = await fetch_upcoming_events(
        token_data=token_data,
        client_id=cal_client_id,
        client_secret=cal_client_secret,
        days_ahead=2,
        max_results=50,
    )

    # Save refreshed token if it changed
    if updated_token is not token_data:
        async with SessionLocal() as session:
            row = (
                await session.execute(select(WorkspaceSettings).limit(1))
            ).scalar_one_or_none()
            if row:
                row.zippy_calendar_token_data = updated_token
                session.add(row)
                await session.commit()

    # ── determine "tomorrow" in IST ─────────────────────────────────────────
    now_ist = datetime.now(IST)
    tomorrow_ist: date = (now_ist + timedelta(days=1)).date()

    tomorrow_events = []
    for ev in events:
        if ev.start_dt is None:
            continue
        # Convert to IST for date comparison
        ev_date_ist = ev.start_dt.astimezone(IST).date()
        if ev_date_ist == tomorrow_ist:
            tomorrow_events.append(ev)

    if not tomorrow_events:
        logger.info("ae_meeting_reminder: no events tomorrow (%s) — nothing to send", tomorrow_ist)
        return {"status": "ok", "sent": 0}

    # ── match events to CRM and build meeting list ───────────────────────────
    meeting_lines: list[str] = []

    async with SessionLocal() as session:
        for ev in tomorrow_events:
            company = await _match_company(session, ev.attendee_emails, ev.title)
            if not company:
                logger.debug("ae_meeting_reminder: no CRM match for event '%s'", ev.title)
                continue

            deal_stage, deal_assigned_to_id = await _get_deal_stage_for_company(session, company.id)

            if deal_stage is None:
                # Company in CRM but no deal yet — include ("slipped through")
                include = True
            elif deal_stage == _INCLUDE_STAGE:
                # demo_scheduled — include
                include = True
            else:
                # Any other stage — skip
                logger.debug(
                    "ae_meeting_reminder: skipping '%s' — deal stage '%s'",
                    company.name,
                    deal_stage,
                )
                include = False

            if not include:
                continue

            # Prefer company's assigned AE (set in Account Sourcing),
            # fall back to deal's assigned AE, then meeting attendees.
            ae_assigned_id = company.assigned_to_id or deal_assigned_to_id
            ae_name = await _get_ae_name(
                session, ev.attendee_emails, ev.organizer_email,
                assigned_to_id=ae_assigned_id,
            )
            time_ist = ev.start_dt.astimezone(IST).strftime("%-I:%M %p")

            meeting_lines.append(
                f"Company Name: {company.name}\n"
                f"Time: {time_ist}\n"
                f"AE: {ae_name}"
            )

    if not meeting_lines:
        logger.info("ae_meeting_reminder: no includable meetings for tomorrow — nothing to send")
        return {"status": "ok", "sent": 0}

    # ── build and send message ───────────────────────────────────────────────
    date_str = tomorrow_ist.strftime("%d %B %Y")
    separator = "\n\n---\n\n"
    body = separator.join(meeting_lines)

    message_text = (
        f"📅 Tomorrow's Account Meetings — {date_str}\n\n"
        f"{body}\n\n"
        "---\n"
        "📎 Please attach your Account Research Document before the meeting."
    )

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(webhook_url, json={"text": message_text})
        resp.raise_for_status()

    logger.info(
        "ae_meeting_reminder: posted reminder with %d meeting(s) for %s",
        len(meeting_lines),
        tomorrow_ist,
    )
    return {"status": "ok", "sent": len(meeting_lines)}


# ── Celery task wrapper ──────────────────────────────────────────────────────

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.run_until_complete(loop.shutdown_asyncgens())
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            for t in pending:
                t.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@celery_app.task(name="app.tasks.ae_meeting_reminder.send_ae_meeting_reminder")
def send_ae_meeting_reminder() -> dict:
    """Post tomorrow's AE account meetings to the Google Chat space."""
    return _run_async(_async_send_ae_meeting_reminder())
