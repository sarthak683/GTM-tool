from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.models.contact import Contact


def reset_contact_outreach_progress(contact: Contact) -> None:
    """Clear current prospect progress when account SDR ownership changes."""
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


async def sync_company_sdr_assignment_to_contacts(
    session: AsyncSession,
    company: Company,
    previous_sdr_id: UUID | None,
) -> list[Contact]:
    """Cascade company SDR changes to contacts that followed the old account SDR.

    Historical Activity rows are kept for audit/timeline purposes. The company
    timestamp lets read paths ignore pre-reassignment call activity, while the
    denormalized contact fields are cleared so the new SDR starts from zero.
    """
    sdr_changed = company.sdr_id != previous_sdr_id
    if sdr_changed:
        company.sdr_assigned_at = datetime.utcnow()

    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    for contact in contacts:
        if contact.sdr_id not in (None, previous_sdr_id):
            continue
        contact.sdr_id = company.sdr_id
        contact.sdr_name = company.sdr_name
        if sdr_changed:
            reset_contact_outreach_progress(contact)
        contact.updated_at = datetime.utcnow()
        session.add(contact)

    return contacts
