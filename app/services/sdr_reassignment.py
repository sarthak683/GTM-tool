from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.models.contact import Contact


class CascadeResult(NamedTuple):
    """Outcome of cascading a company-level owner change to its contacts.

    ``kept_divergent`` are deliberate per-contact overrides (a third rep holds
    the prospect) that the cascade leaves alone — callers MUST surface the
    count to the user instead of letting the divergence stay invisible; silent
    skips are how account and prospect ownership drifted apart in prod.
    """

    contacts: list[Contact]
    moved: list[Contact]
    kept_divergent: list[Contact]


def reset_contact_outreach_progress(contact: Contact) -> None:
    """Clear current prospect progress when SDR ownership changes.

    Two things happen here:

    1. The denormalized per-channel state (the columns the prospect table and
       ProgressCell read directly) is wiped so the incoming SDR sees an
       untouched prospect.
    2. `sdr_assigned_at` is stamped. Counts that are *aggregates* over the
       Activity table — call attempts, latest-activity signal, sequence step
       state — cannot be zeroed without destroying the audit trail, so read
       paths compare against this watermark and ignore anything older.

    Raw Activity rows are deliberately left untouched: they are the record of
    what the previous SDR actually did.
    """
    now = datetime.utcnow()
    # Instantly holds LIFETIME totals per lead and the poller only ever moves
    # the count upwards, so remember what we wiped — otherwise the next sync
    # restores the previous SDR's opens/clicks. See app/tasks/instantly_sync.py.
    contact.instantly_open_baseline = (contact.instantly_open_baseline or 0) + (
        contact.email_open_count or 0
    )
    contact.instantly_click_baseline = (contact.instantly_click_baseline or 0) + (
        contact.email_click_count or 0
    )
    contact.email_open_count = 0
    contact.email_click_count = 0
    contact.email_last_opened_at = None
    contact.call_status = None
    contact.call_disposition = None
    contact.call_notes = None
    contact.call_last_at = None
    contact.next_followup_at = None
    contact.linkedin_status = None
    contact.linkedin_last_at = None
    contact.sequence_status = None
    contact.instantly_status = None
    contact.sdr_assigned_at = now


def instantly_counts_since_assignment(contact: Contact, lead: dict) -> tuple[int, int]:
    """Return the (opens, clicks) an Instantly lead has earned under the current SDR.

    Instantly's API reports LIFETIME totals per lead and has no notion of our
    reassignments, so the raw numbers would resurrect the previous SDR's
    engagement on the first poll after a reset. Subtracting the baseline
    recorded at reassignment leaves only what accrued since.
    """
    opens = int(lead.get("email_open_count") or 0) - (contact.instantly_open_baseline or 0)
    clicks = int(lead.get("email_click_count") or 0) - (contact.instantly_click_baseline or 0)
    return max(0, opens), max(0, clicks)


def open_timestamp_within_assignment(
    contact: Contact, opened_at: datetime | None
) -> datetime | None:
    """Drop an Instantly open timestamp that predates the current SDR's tenure."""
    if opened_at is None:
        return None
    watermark = contact.sdr_assigned_at
    if watermark is not None and opened_at < watermark:
        return None
    return opened_at


def status_within_assignment(
    contact: Contact, last_activity_at: datetime | None
) -> bool:
    """Whether an Instantly-derived STATUS may be applied to this prospect.

    Instantly has no notion of our reassignments, so on every poll it re-offers
    the campaign outcome it has always held ("bounced", "unsubscribed", …). For a
    prospect that changed hands, re-applying an outcome earned under the previous
    SDR undoes the reset — the count reset alone is not enough, because the
    status is what lights the channel lane up in the UI.

    `last_activity_at` is when Instantly last did something with this lead
    (normally `timestamp_last_contact`). A prospect that was never reassigned is
    unaffected. A missing timestamp means Instantly cannot show the activity is
    the current owner's, so for a reassigned prospect it is treated as stale.

    NOTE: suppressing the status does NOT lose the "do not email this address"
    signal — a bounce also clears `email_verified`, which is never restored by
    the reset, and the bounce Activity row is kept for the timeline.
    """
    watermark = contact.sdr_assigned_at
    if watermark is None:
        return True
    if last_activity_at is None:
        return False
    return last_activity_at >= watermark


async def sync_company_sdr_assignment_to_contacts(
    session: AsyncSession,
    company: Company,
    previous_sdr_id: UUID | None,
) -> CascadeResult:
    """Cascade company SDR changes to contacts that followed the old account SDR.

    Contacts pointed at a DIFFERENT SDR (a deliberate per-contact split, e.g. a
    timezone handoff) are left entirely alone — both their owner and their
    progress. Because the watermark lives on the contact, skipping them here is
    enough: their aggregates keep reading their own full history. The skipped
    contacts come back in ``kept_divergent`` so every caller can SHOW the split
    instead of silently leaving account and prospect ownership to drift.
    """
    sdr_changed = company.sdr_id != previous_sdr_id
    if sdr_changed:
        company.sdr_assigned_at = datetime.utcnow()

    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    moved: list[Contact] = []
    kept_divergent: list[Contact] = []
    for contact in contacts:
        if contact.sdr_id not in (None, previous_sdr_id):
            kept_divergent.append(contact)
            continue
        contact.sdr_id = company.sdr_id
        contact.sdr_name = company.sdr_name
        if sdr_changed:
            reset_contact_outreach_progress(contact)
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        moved.append(contact)

    return CascadeResult(contacts=list(contacts), moved=moved, kept_divergent=kept_divergent)


async def sync_company_ae_assignment_to_contacts(
    session: AsyncSession,
    company: Company,
    previous_ae_id: UUID | None,
) -> CascadeResult:
    """AE mirror of the SDR cascade, with the same skip-and-report semantics.

    Previously this logic was copy-pasted inline in both assignment endpoints
    (single + bulk), which is exactly how the two drifted. AE moves never reset
    outreach progress — the cadence belongs to the SDR motion.
    """
    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    moved: list[Contact] = []
    kept_divergent: list[Contact] = []
    for contact in contacts:
        if contact.assigned_to_id not in (None, previous_ae_id):
            kept_divergent.append(contact)
            continue
        contact.assigned_to_id = company.assigned_to_id
        contact.assigned_rep_email = company.assigned_rep_email
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        moved.append(contact)

    return CascadeResult(contacts=list(contacts), moved=moved, kept_divergent=kept_divergent)
