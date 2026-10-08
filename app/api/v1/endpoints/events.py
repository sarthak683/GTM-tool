"""Event tags on prospects and accounts — the shared event list (filter
dropdown options) and the bulk add/remove used by the Prospecting and Account
Sourcing "Add to event" actions and the detail-page chips."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import ValidationError
from app.models.company import Company
from app.models.contact import Contact
from app.repositories.company import CompanyRepository
from app.repositories.contact import ContactRepository
from app.services.contact_access import authorize_contact_edit
from app.services.record_access import authorize_company_edit, can_edit_record
from app.services.event_tags import canonicalize, clean_event, known_events, merge_events

router = APIRouter(prefix="/events", tags=["events"])

MAX_EVENT_LEN = 200


class EventTagPayload(BaseModel):
    event: str
    contact_ids: list[UUID] = Field(default_factory=list)
    company_ids: list[UUID] = Field(default_factory=list)
    action: Literal["add", "remove"] = "add"
    # Add only: also tag the selected prospects' accounts, so the Account
    # Sourcing filter agrees with the Prospecting one.
    include_companies: bool = True


class EventTagResult(BaseModel):
    event: str
    contacts_changed: int
    companies_changed: int


@router.get("", response_model=list[str])
async def list_events(session: DBSession, _user: CurrentUser):
    """Every event name currently tagged on any prospect or account."""
    names = await known_events(session)
    return sorted(names.values(), key=str.lower)


@router.post("/tag", response_model=EventTagResult)
async def tag_event(payload: EventTagPayload, session: DBSession, current_user: CurrentUser):
    event = clean_event(payload.event)
    if not event:
        raise ValidationError("Event name is required")
    if len(event) > MAX_EVENT_LEN:
        raise ValidationError(f"Event name must be {MAX_EVENT_LEN} characters or fewer")
    if not payload.contact_ids and not payload.company_ids:
        raise ValidationError("Select at least one prospect or account")

    known = await known_events(session)
    event = canonicalize([event], known)[0]
    adding = payload.action == "add"

    contacts: list[Contact] = []
    if payload.contact_ids:
        contacts = list(
            (
                await session.execute(
                    (await ContactRepository.visible_to(session, current_user)).where(
                        Contact.id.in_(payload.contact_ids)
                    )
                )
            ).scalars().all()
        )

    for contact in contacts:
        await authorize_contact_edit(session, current_user, contact)

    company_ids = set(payload.company_ids)
    if adding and payload.include_companies:
        company_ids.update(c.company_id for c in contacts if c.company_id)
    companies: list[Company] = []
    if company_ids:
        companies = list(
            (
                await session.execute(
                    CompanyRepository.visible_to(current_user, include_disabled=True).where(
                        Company.id.in_(company_ids)
                    )
                )
            ).scalars().all()
        )

    for company in companies:
        if company.id in payload.company_ids:
            authorize_company_edit(current_user, company)
    companies = [company for company in companies if can_edit_record(current_user, company)]

    def _apply(record) -> bool:
        current = list(record.events or [])
        if adding:
            updated = merge_events(current, [event])
        else:
            updated = [e for e in current if e.lower() != event.lower()]
        if updated == current:
            return False
        record.events = updated
        session.add(record)
        return True

    contacts_changed = sum(1 for c in contacts if _apply(c))
    companies_changed = sum(1 for c in companies if _apply(c))
    await session.commit()
    return EventTagResult(event=event, contacts_changed=contacts_changed, companies_changed=companies_changed)
