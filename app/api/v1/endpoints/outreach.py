from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body
from sqlmodel import select

from app.clients.instantly import InstantlyClient, InstantlyError
from app.clients.instantly_events import INSTANTLY_WEBHOOK_EVENTS
from app.config import settings
from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import NotFoundError, ValidationError
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.outreach import (
    OutreachSequence,
    OutreachSequenceRead,
    OutreachStep,
    OutreachStepCreate,
    OutreachStepRead,
    OutreachStepUpdate,
)
from app.repositories.outreach import OutreachRepository
from app.services.outreach_generator import generate_sequence

router = APIRouter(prefix="/outreach", tags=["outreach"])


@router.get("/instantly/campaigns")
async def list_instantly_campaigns(_user: CurrentUser):
    """List Instantly campaigns (for the bulk-start campaign picker).

    Returns a slim {id, name, status} list. On any Instantly error we return an
    empty list rather than 500 so the picker degrades gracefully.
    """
    try:
        campaigns = await InstantlyClient().list_campaigns(limit=200)
    except Exception:
        return {"campaigns": []}
    slim = []
    for c in campaigns or []:
        cid = c.get("id") or c.get("campaign_id")
        if not cid:
            continue
        slim.append({
            "id": str(cid),
            "name": c.get("name") or c.get("campaign_name") or str(cid),
            "status": c.get("status"),
        })
    return {"campaigns": slim}


@router.post("/instantly/bulk-add")
async def bulk_add_to_instantly_campaign(
    session: DBSession,
    _user: CurrentUser,
    contact_ids: list[UUID] = Body(..., embed=True),
    campaign_id: str = Body(..., embed=True),
):
    """Add a selected set of prospects to an EXISTING Instantly campaign.

    The bulk analog of the single-contact sequence launch: reps tick prospects on
    the Prospects page, pick a campaign, and we bulk-add them as leads via
    Instantly's add_leads_bulk (≤1000/call). The campaign already owns its email
    steps in Instantly, so we don't create/activate anything here — we just enroll
    leads. Contacts without an email are skipped and reported.
    """
    from app.models.company import Company

    ids = [cid for cid in contact_ids if cid]
    if not ids:
        raise ValidationError("Select at least one prospect.")
    if len(set(ids)) > 1000:
        raise ValidationError("Too many prospects in one request (max 1000). Start in smaller batches.")
    if not (campaign_id or "").strip():
        raise ValidationError("Pick a campaign to start.")

    contacts = (
        await session.execute(select(Contact).where(Contact.id.in_(list(set(ids)))))
    ).scalars().all()
    if not contacts:
        raise NotFoundError("No matching prospects found.")

    # Batch-fetch company names for the lead payloads (contacts may span companies).
    company_ids = list({c.company_id for c in contacts if c.company_id})
    companies = (
        await session.execute(select(Company).where(Company.id.in_(company_ids)))
    ).scalars().all() if company_ids else []
    company_name_by_id = {str(co.id): co.name for co in companies}

    leads: list[dict] = []
    enrolled = []
    skipped_no_email = 0
    for contact in contacts:
        if not (contact.email or "").strip():
            skipped_no_email += 1
            continue
        leads.append({
            "email": contact.email,
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "company_name": company_name_by_id.get(str(contact.company_id), "") if contact.company_id else "",
            "job_title": contact.title or "",
            "linkedin_url": contact.linkedin_url or "",
        })
        enrolled.append(contact)

    if not leads:
        raise ValidationError("None of the selected prospects have an email address.")

    client = InstantlyClient()
    # Register webhooks so we get status callbacks (non-fatal if it fails).
    if settings.INSTANTLY_WEBHOOK_URL:
        try:
            await client.ensure_webhook(url=settings.INSTANTLY_WEBHOOK_URL, event_types=INSTANTLY_WEBHOOK_EVENTS)
        except Exception:
            pass

    try:
        await client.add_leads_bulk(campaign_id=campaign_id, leads=leads)
    except InstantlyError as e:
        raise ValidationError(f"Instantly bulk-add failed: {e.detail}")

    # Stamp each enrolled contact so the UI reflects the queued state immediately.
    for contact in enrolled:
        contact.instantly_status = "pushed"
        contact.sequence_status = "queued_instantly"
        contact.instantly_campaign_id = campaign_id
        session.add(contact)
    await session.commit()

    return {
        "campaign_id": campaign_id,
        "requested": len(set(ids)),
        "enrolled": len(enrolled),
        "skipped_no_email": skipped_no_email,
    }


_ALLOWED_SEQUENCE_FIELDS = frozenset(
    ["email_1", "email_2", "email_3", "subject_1", "subject_2", "subject_3",
     "linkedin_message", "status"]
)


def _normalize_variants_payload(raw):
    if isinstance(raw, dict):
        payload = dict(raw)
        payload_variants = payload.get("variants")
        payload["variants"] = payload_variants if isinstance(payload_variants, list) else []
        channel = str(payload.get("channel") or "email").strip().lower()
        payload["channel"] = channel if channel in {"email", "call", "linkedin"} else "email"
        return payload
    if isinstance(raw, list):
        return {"channel": "email", "variants": raw}
    return {"channel": "email", "variants": []}


def _sequence_started(seq: OutreachSequence) -> bool:
    return bool(
        seq.instantly_campaign_id
        or seq.launched_at
        or seq.status in {"launched", "sent", "replied", "completed", "meeting_booked"}
        or seq.instantly_campaign_status in {"active", "paused", "completed"}
    )


# ── Sequence generation ────────────────────────────────────────────────────────

@router.post("/generate/{contact_id}", response_model=OutreachSequenceRead)
async def generate_contact_sequence(contact_id: UUID, session: DBSession, _user: CurrentUser):
    """Generate a multi-step email cadence + LinkedIn message for a contact."""
    seq = await generate_sequence(contact_id, session)
    if not seq:
        raise NotFoundError("Contact not found")
    return seq


@router.post("/bulk/{company_id}")
async def generate_bulk_sequences(
    company_id: UUID,
    session: DBSession,
    _user: CurrentUser,
    persona_filter: Optional[str] = None,
):
    """Generate sequences for all contacts at a company (skips existing)."""
    query = select(Contact).where(Contact.company_id == company_id)
    if persona_filter:
        query = query.where(Contact.persona == persona_filter)

    contacts = (await session.execute(query)).scalars().all()
    if not contacts:
        raise NotFoundError("No contacts found for this company")

    repo = OutreachRepository(session)
    generated, skipped, failed = [], [], []

    for contact in contacts:
        if await repo.exists_for_contact(contact.id):
            skipped.append(str(contact.id))
            continue
        try:
            seq = await generate_sequence(contact.id, session)
            if seq:
                generated.append({
                    "contact_id": str(contact.id),
                    "name": f"{contact.first_name} {contact.last_name}",
                    "persona": contact.persona,
                    "sequence_id": str(seq.id),
                })
        except Exception as e:
            failed.append({"contact_id": str(contact.id), "error": str(e)})

    return {
        "company_id": str(company_id),
        "total_contacts": len(contacts),
        "generated": len(generated),
        "skipped_existing": len(skipped),
        "failed": len(failed),
        "sequences": generated,
    }


# ── Sequence read / update ─────────────────────────────────────────────────────

@router.get("/sequences/{contact_id}", response_model=OutreachSequenceRead)
async def get_contact_sequence(contact_id: UUID, session: DBSession, _user: CurrentUser):
    seq = await OutreachRepository(session).get_by_contact(contact_id)
    if not seq:
        raise NotFoundError(
            "No sequence found. Call POST /outreach/generate/{contact_id} first."
        )
    return seq


@router.get("/contacts/{contact_id}/sequence", response_model=Optional[OutreachSequenceRead])
async def get_contact_sequence_optional(contact_id: UUID, session: DBSession, _user: CurrentUser):
    """Return the contact's sequence when present without logging a 404 in the browser."""
    return await OutreachRepository(session).get_by_contact(contact_id)


@router.patch("/sequences/{sequence_id}", response_model=OutreachSequenceRead)
async def update_sequence(sequence_id: UUID, updates: dict, session: DBSession, _user: CurrentUser):
    repo = OutreachRepository(session)
    seq = await repo.get_or_raise(sequence_id)

    clean = {k: v for k, v in updates.items() if k in _ALLOWED_SEQUENCE_FIELDS}
    if not clean:
        raise ValidationError(f"No valid fields. Allowed: {sorted(_ALLOWED_SEQUENCE_FIELDS)}")

    clean["updated_at"] = datetime.utcnow()
    return await repo.update(seq, clean)


@router.get("/company/{company_id}")
async def get_company_sequences(company_id: UUID, session: DBSession, _user: CurrentUser):
    rows = (
        await session.execute(
            select(OutreachSequence, Contact)
            .join(Contact, OutreachSequence.contact_id == Contact.id)
            .where(OutreachSequence.company_id == company_id)
        )
    ).all()

    return [
        {
            "sequence_id": str(seq.id),
            "contact_id": str(seq.contact_id),
            "contact_name": f"{contact.first_name} {contact.last_name}",
            "title": contact.title,
            "persona": seq.persona,
            "status": seq.status,
            "instantly_campaign_id": seq.instantly_campaign_id,
            "instantly_campaign_status": seq.instantly_campaign_status,
            "subject_1": seq.subject_1,
            "email_1_preview": (seq.email_1 or "")[:200] + "..." if seq.email_1 else None,
            "generated_at": seq.generated_at.isoformat() if seq.generated_at else None,
            "launched_at": seq.launched_at.isoformat() if seq.launched_at else None,
        }
        for seq, contact in rows
    ]


# ── Steps CRUD ────────────────────────────────────────────────────────────────

@router.get("/sequences/{sequence_id}/steps", response_model=list[OutreachStepRead])
async def get_steps(sequence_id: UUID, session: DBSession, _user: CurrentUser):
    """Get all steps for a sequence, ordered by step_number."""
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")

    result = await session.execute(
        select(OutreachStep)
        .where(OutreachStep.sequence_id == sequence_id)
        .order_by(OutreachStep.step_number)
    )
    return result.scalars().all()


@router.post("/sequences/{sequence_id}/steps", response_model=OutreachStepRead)
async def add_step(sequence_id: UUID, step_in: OutreachStepCreate, session: DBSession, _user: CurrentUser):
    """Add a new step to a sequence (before it's launched to Instantly)."""
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")
    if _sequence_started(seq):
        raise ValidationError("Cannot change sequence timing after sequencing has started")

    step = OutreachStep(
        sequence_id=sequence_id,
        step_number=step_in.step_number,
        subject=step_in.subject,
        body=step_in.body,
        delay_value=step_in.delay_value,
        delay_unit=step_in.delay_unit,
        variants=_normalize_variants_payload(step_in.variants),
    )
    step.channel = step_in.channel
    session.add(step)
    await session.commit()
    await session.refresh(step)
    return step


@router.patch("/steps/{step_id}", response_model=OutreachStepRead)
async def update_step(step_id: UUID, updates: OutreachStepUpdate, session: DBSession, _user: CurrentUser):
    """Edit a step's content, delay, or variants."""
    step = await session.get(OutreachStep, step_id)
    if not step:
        raise NotFoundError("Step not found")

    seq = await session.get(OutreachSequence, step.sequence_id)
    if seq and _sequence_started(seq):
        raise ValidationError("Cannot change sequence timing after sequencing has started")

    update_data = updates.model_dump(exclude_none=True)
    for key, val in update_data.items():
        if key == "variants":
            step.variants = _normalize_variants_payload(val)
        elif key == "channel":
            step.channel = val
        else:
            setattr(step, key, val)
    step.updated_at = datetime.utcnow()

    session.add(step)
    await session.commit()
    await session.refresh(step)
    return step


@router.delete("/steps/{step_id}")
async def delete_step(step_id: UUID, session: DBSession, _user: CurrentUser):
    """Remove a step from a sequence (before launch only)."""
    step = await session.get(OutreachStep, step_id)
    if not step:
        raise NotFoundError("Step not found")

    seq = await session.get(OutreachSequence, step.sequence_id)
    if seq and _sequence_started(seq):
        raise ValidationError("Cannot change sequence timing after sequencing has started")

    await session.delete(step)
    await session.commit()
    return {"status": "deleted", "step_id": str(step_id)}


# ── Launch to Instantly ───────────────────────────────────────────────────────

@router.post("/launch/{sequence_id}")
async def launch_sequence(
    sequence_id: UUID,
    session: DBSession,
    _user: CurrentUser,
    sending_account: str = Body(..., embed=True),
    campaign_name: Optional[str] = Body(None, embed=True),
):
    """
    Launch a sequence to Instantly.ai.

    Flow:
    1. Load sequence + steps (falls back to email_1/2/3 if no steps exist)
    2. Create campaign in Instantly with all steps
    3. Activate the campaign
    4. Add the contact as a lead to the campaign
    5. Update sequence with instantly_campaign_id + status
    6. Update contact instantly_status + sequence_status

    sending_account: the email address of the Instantly sending account to use.
    campaign_name: optional override; defaults to "Contact Name — Company"
    """
    # ── Load sequence ──────────────────────────────────────────────────────────
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")

    if _sequence_started(seq):
        raise ValidationError(
            f"Sequence already launched. Instantly campaign: {seq.instantly_campaign_id}"
        )

    # ── Load contact ───────────────────────────────────────────────────────────
    contact = await session.get(Contact, seq.contact_id)
    if not contact:
        raise NotFoundError("Contact not found")

    if not contact.email:
        raise ValidationError("Contact has no email address — cannot launch sequence")

    # ── Load steps (prefer OutreachStep records, fall back to email_1/2/3) ────
    steps_result = await session.execute(
        select(OutreachStep)
        .where(OutreachStep.sequence_id == sequence_id)
        .order_by(OutreachStep.step_number)
    )
    steps = steps_result.scalars().all()

    if not steps:
        # Fallback: build steps from the legacy email_1/2/3 fields
        steps = _steps_from_legacy(seq)

    if not steps:
        raise ValidationError(
            "No email steps found. Generate the sequence first or add steps manually."
        )

    # ── Build payload for Instantly ────────────────────────────────────────────
    from app.models.company import Company
    company = await session.get(Company, seq.company_id) if seq.company_id else None
    company_name = company.name if company else "Company"

    name = campaign_name or f"{contact.first_name} {contact.last_name} — {company_name}"

    email_steps = [step for step in steps if getattr(step, "channel", "email") == "email"]

    if not email_steps:
        raise ValidationError("This sequence has no email steps to launch yet. Add at least one email touch before launching.")

    instantly_steps = [
        {
            "subject": step.subject or (f"Re: {email_steps[0].subject}" if i > 0 else "Hello"),
            "body": step.body,
            "delay_value": step.delay_value,
            # Omit delay_unit — Instantly defaults to days; including it can
            # cause validation errors if the value doesn't match their allowlist
            "variants": _normalize_variants_payload(step.variants).get("variants") or [],
        }
        for i, step in enumerate(email_steps)
    ]

    # ── Call Instantly API ─────────────────────────────────────────────────────
    client = InstantlyClient()

    try:
        campaign = await client.create_campaign(
            name=name,
            sending_accounts=[sending_account],
            steps=instantly_steps,
        )
    except InstantlyError as e:
        raise ValidationError(f"Instantly campaign creation failed: {e.detail}")

    if not campaign:
        raise ValidationError("Instantly API returned no campaign (check INSTANTLY_API_KEY)")

    campaign_id = campaign.get("id") or campaign.get("campaign_id")

    # Activate the campaign so it starts sending
    try:
        await client.activate_campaign(campaign_id)
    except InstantlyError as e:
        # Non-fatal — campaign exists, activation can be retried
        import logging
        logging.getLogger(__name__).warning("Campaign activation failed: %s", e)

    # ── Add contact as lead ────────────────────────────────────────────────────
    try:
        await client.add_lead(
            campaign_id=campaign_id,
            email=contact.email,
            first_name=contact.first_name or "",
            last_name=contact.last_name or "",
            company_name=company_name,
            job_title=contact.title or "",
            linkedin_url=contact.linkedin_url or "",
            custom_variables={
                "persona": seq.persona or "",
                "conversation_starter": contact.conversation_starter or "",
            },
        )
    except InstantlyError as e:
        raise ValidationError(f"Failed to add lead to Instantly campaign: {e.detail}")

    # ── Persist campaign ID back to CRM ───────────────────────────────────────
    now = datetime.utcnow()

    seq.instantly_campaign_id = campaign_id
    seq.instantly_campaign_status = "active"
    seq.status = "launched"
    seq.launched_at = now
    seq.updated_at = now
    session.add(seq)

    contact.instantly_campaign_id = campaign_id
    contact.instantly_status = "pushed"
    contact.sequence_status = "queued_instantly"
    contact.updated_at = now
    session.add(contact)

    # Register our webhook if not already registered
    if settings.INSTANTLY_WEBHOOK_URL:
        try:
            await client.ensure_webhook(
                url=settings.INSTANTLY_WEBHOOK_URL,
                event_types=INSTANTLY_WEBHOOK_EVENTS,
            )
        except Exception:
            pass  # Webhook registration failure is non-fatal

    await session.commit()

    return {
        "status": "launched",
        "sequence_id": str(sequence_id),
        "instantly_campaign_id": campaign_id,
        "contact_email": contact.email,
        "steps_count": len(email_steps),
        "campaign_name": name,
    }


# ── Bulk company launch ────────────────────────────────────────────────────────

@router.post("/launch-company/{company_id}")
async def launch_company_campaign(
    company_id: UUID,
    session: DBSession,
    _user: CurrentUser,
    sending_account: str = Body(..., embed=True),
    campaign_name: Optional[str] = Body(None, embed=True),
):
    """
    Create an Instantly campaign and add ALL sequenced contacts for a company.

    Flow:
    1. Find all unlaunched sequences for the company
    2. Build campaign steps from the first sequence (template)
    3. Create + activate campaign in Instantly
    4. Bulk-add all contacts as leads
    5. Update all sequences and contacts with campaign ID and statuses
    6. Register webhooks

    This is the multi-prospect equivalent of the per-sequence /launch endpoint.
    The first sequence's steps serve as the campaign template — all prospects
    get the same email cadence.
    """
    from app.models.company import Company

    company = await session.get(Company, company_id)
    if not company:
        raise NotFoundError("Company not found")

    # Find all unlaunched sequences for this company that have contacts with emails
    seq_result = await session.execute(
        select(OutreachSequence, Contact)
        .join(Contact, OutreachSequence.contact_id == Contact.id)
        .where(
            OutreachSequence.company_id == company_id,
            Contact.email.isnot(None),
            ~OutreachSequence.instantly_campaign_id.isnot(None),
            ~OutreachSequence.launched_at.isnot(None),
            OutreachSequence.status.not_in(["launched", "sent", "replied", "completed", "meeting_booked"]),
        )
        .order_by(OutreachSequence.created_at)
    )
    rows = seq_result.all()
    if not rows:
        raise ValidationError("No unlaunched sequences with valid email contacts found for this company")

    # Use the first sequence's steps as the campaign template
    template_seq = rows[0][0]
    steps_result = await session.execute(
        select(OutreachStep)
        .where(OutreachStep.sequence_id == template_seq.id)
        .order_by(OutreachStep.step_number)
    )
    steps = steps_result.scalars().all()
    if not steps:
        steps = _steps_from_legacy(template_seq)
    if not steps:
        raise ValidationError("Template sequence has no email steps. Add steps before launching.")

    # Filter to email-only steps
    email_steps = [s for s in steps if getattr(s, "channel", "email") == "email"]
    if not email_steps:
        raise ValidationError("Template sequence has no email steps.")

    instantly_steps = [
        {
            "subject": step.subject or (f"Re: {email_steps[0].subject}" if i > 0 else "Hello"),
            "body": step.body,
            "delay_value": step.delay_value,
            "variants": _normalize_variants_payload(step.variants).get("variants") or [],
        }
        for i, step in enumerate(email_steps)
    ]

    name = campaign_name or f"{company.name} — {len(rows)} prospects"

    # ── Create & activate campaign ────────────────────────────────────────────
    client = InstantlyClient()
    try:
        campaign = await client.create_campaign(
            name=name,
            sending_accounts=[sending_account],
            steps=instantly_steps,
        )
    except InstantlyError as e:
        raise ValidationError(f"Instantly campaign creation failed: {e.detail}")
    if not campaign:
        raise ValidationError("Instantly API returned no campaign (check INSTANTLY_API_KEY)")

    campaign_id = campaign.get("id") or campaign.get("campaign_id")

    try:
        await client.activate_campaign(campaign_id)
    except InstantlyError as e:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("Campaign activation failed (non-fatal): %s", e)

    # ── Add all contacts as leads ─────────────────────────────────────────────
    now = datetime.utcnow()
    lead_payloads = []
    results = []
    for seq, contact in rows:
        lead_payloads.append({
            "email": contact.email,
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "company_name": company.name,
            "job_title": contact.title or "",
            "linkedin_url": contact.linkedin_url or "",
        })
        # Touch CRM records even before the bulk API call —
        # if the bulk call fails we'd rather have over-optimistic status
        # than lose the records to a transient 5xx from Instantly.
        seq.instantly_campaign_id = campaign_id
        seq.instantly_campaign_status = "active"
        seq.status = "launched"
        seq.launched_at = now
        seq.updated_at = now
        session.add(seq)
        contact.instantly_campaign_id = campaign_id
        contact.instantly_status = "pushed"
        contact.sequence_status = "queued_instantly"
        contact.updated_at = now
        session.add(contact)

    try:
        await client.add_leads_bulk(campaign_id=campaign_id, leads=lead_payloads)
        results = [
            {"contact_id": str(contact.id), "email": contact.email, "status": "pushed"}
            for _seq, contact in rows
        ]
    except InstantlyError as e:
        results = [
            {"contact_id": str(contact.id), "email": contact.email, "status": "failed", "error": str(e.detail)[:200]}
            for _seq, contact in rows
        ]

    # ── Register webhooks ─────────────────────────────────────────────────────
    if settings.INSTANTLY_WEBHOOK_URL:
        try:
            await client.ensure_webhook(
                url=settings.INSTANTLY_WEBHOOK_URL,
                event_types=INSTANTLY_WEBHOOK_EVENTS,
            )
        except Exception:
            pass

    await session.commit()

    return {
        "status": "launched",
        "company_id": str(company_id),
        "company_name": company.name,
        "instantly_campaign_id": campaign_id,
        "campaign_name": name,
        "total_sequences": len(rows),
        "pushed": sum(1 for r in results if r["status"] == "pushed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }


@router.post("/launch-contacts")
async def launch_contacts_campaign(
    session: DBSession,
    _user: CurrentUser,
    contact_ids: list[UUID] = Body(..., embed=True),
    sending_account: str = Body(..., embed=True),
    template_sequence_id: Optional[UUID] = Body(None, embed=True),
    campaign_name: Optional[str] = Body(None, embed=True),
):
    """
    Bulk-launch an Instantly campaign for a hand-picked set of contacts.

    Unlike /launch-company/{company_id}, this endpoint is contact-driven so a
    rep can select prospects across multiple companies from AccountSourcing
    or the Contacts page and push them into one sequence.

    For any selected contact without an existing OutreachSequence we
    generate one inline (using app.services.outreach_generator). The
    template_sequence_id (if provided) sets the email cadence used by the
    Instantly campaign; otherwise the first generated/found sequence with
    email steps acts as the template.
    """
    if not contact_ids:
        raise ValidationError("contact_ids is required")

    contacts_result = await session.execute(
        select(Contact).where(Contact.id.in_(contact_ids))
    )
    contacts = list(contacts_result.scalars().all())
    if not contacts:
        raise ValidationError("No contacts found for the provided ids")

    contacts_with_email = [c for c in contacts if c.email]
    if not contacts_with_email:
        raise ValidationError("None of the selected contacts have an email address")

    # ── Ensure each contact has a sequence (generate if missing) ──────────────
    seq_by_contact: dict[UUID, OutreachSequence] = {}
    existing_result = await session.execute(
        select(OutreachSequence).where(
            OutreachSequence.contact_id.in_([c.id for c in contacts_with_email])
        )
    )
    for seq in existing_result.scalars().all():
        # Skip sequences that have already been launched — re-launching the
        # same contact into a new campaign would create duplicate Instantly
        # leads and double-count engagement.
        if _sequence_started(seq):
            continue
        seq_by_contact[seq.contact_id] = seq

    generated_count = 0
    for contact in contacts_with_email:
        if contact.id in seq_by_contact:
            continue
        generated = await generate_sequence(contact.id, session)
        if generated and not _sequence_started(generated):
            seq_by_contact[contact.id] = generated
            generated_count += 1

    pairs = [(seq_by_contact[c.id], c) for c in contacts_with_email if c.id in seq_by_contact]
    if not pairs:
        raise ValidationError("All selected contacts already have launched sequences")

    # ── Pick the campaign template ─────────────────────────────────────────────
    template_seq: Optional[OutreachSequence] = None
    if template_sequence_id:
        template_seq = await session.get(OutreachSequence, template_sequence_id)
        if not template_seq:
            raise ValidationError("template_sequence_id not found")
    else:
        template_seq = pairs[0][0]

    steps_result = await session.execute(
        select(OutreachStep)
        .where(OutreachStep.sequence_id == template_seq.id)
        .order_by(OutreachStep.step_number)
    )
    steps = list(steps_result.scalars().all()) or _steps_from_legacy(template_seq)
    email_steps = [s for s in steps if getattr(s, "channel", "email") == "email"]
    if not email_steps:
        raise ValidationError("Template sequence has no email steps. Add steps before launching.")

    instantly_steps = [
        {
            "subject": step.subject or (f"Re: {email_steps[0].subject}" if i > 0 else "Hello"),
            "body": step.body,
            "delay_value": step.delay_value,
            "variants": _normalize_variants_payload(step.variants).get("variants") or [],
        }
        for i, step in enumerate(email_steps)
    ]

    name = campaign_name or f"Bulk · {len(pairs)} prospects"

    # ── Create + activate Instantly campaign ──────────────────────────────────
    client = InstantlyClient()
    try:
        campaign = await client.create_campaign(
            name=name,
            sending_accounts=[sending_account],
            steps=instantly_steps,
        )
    except InstantlyError as e:
        raise ValidationError(f"Instantly campaign creation failed: {e.detail}")
    if not campaign:
        raise ValidationError("Instantly API returned no campaign (check INSTANTLY_API_KEY)")

    campaign_id = campaign.get("id") or campaign.get("campaign_id")
    try:
        await client.activate_campaign(campaign_id)
    except InstantlyError as e:
        import logging
        logging.getLogger(__name__).warning("Campaign activation failed (non-fatal): %s", e)

    # ── Push leads + update CRM ───────────────────────────────────────────────
    now = datetime.utcnow()
    lead_payloads = []
    for seq, contact in pairs:
        lead_payloads.append({
            "email": contact.email,
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "company_name": getattr(contact, "company_name", "") or "",
            "job_title": contact.title or "",
            "linkedin_url": contact.linkedin_url or "",
        })
        seq.instantly_campaign_id = campaign_id
        seq.instantly_campaign_status = "active"
        seq.status = "launched"
        seq.launched_at = now
        seq.updated_at = now
        session.add(seq)
        contact.instantly_campaign_id = campaign_id
        contact.instantly_status = "pushed"
        contact.sequence_status = "queued_instantly"
        contact.updated_at = now
        session.add(contact)

    results: list[dict] = []
    try:
        await client.add_leads_bulk(campaign_id=campaign_id, leads=lead_payloads)
        results = [
            {"contact_id": str(c.id), "email": c.email, "status": "pushed"}
            for _seq, c in pairs
        ]
    except InstantlyError as e:
        results = [
            {"contact_id": str(c.id), "email": c.email, "status": "failed", "error": str(e.detail)[:200]}
            for _seq, c in pairs
        ]

    if settings.INSTANTLY_WEBHOOK_URL:
        try:
            await client.ensure_webhook(
                url=settings.INSTANTLY_WEBHOOK_URL,
                event_types=INSTANTLY_WEBHOOK_EVENTS,
            )
        except Exception:
            pass

    await session.commit()

    return {
        "status": "launched",
        "instantly_campaign_id": campaign_id,
        "campaign_name": name,
        "selected_contacts": len(contact_ids),
        "launched_pairs": len(pairs),
        "skipped_no_email": len(contacts) - len(contacts_with_email),
        "skipped_already_launched": len(contacts_with_email) - len(pairs),
        "sequences_generated": generated_count,
        "pushed": sum(1 for r in results if r["status"] == "pushed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }


@router.get("/launch-status/{sequence_id}")
async def get_launch_status(sequence_id: UUID, session: DBSession, _user: CurrentUser):
    """Fetch live campaign stats from Instantly for a launched sequence."""
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")

    if not seq.instantly_campaign_id:
        return {"status": "not_launched"}

    client = InstantlyClient()
    try:
        campaign = await client.get_campaign(seq.instantly_campaign_id)
    except InstantlyError as e:
        raise ValidationError(f"Failed to fetch campaign from Instantly: {e.detail}")

    return {
        "sequence_id": str(sequence_id),
        "instantly_campaign_id": seq.instantly_campaign_id,
        "campaign": campaign,
    }


@router.post("/pause/{sequence_id}")
async def pause_sequence(sequence_id: UUID, session: DBSession, _user: CurrentUser):
    """Pause an active Instantly campaign."""
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")
    if not seq.instantly_campaign_id:
        raise ValidationError("Sequence is not linked to an Instantly campaign")

    client = InstantlyClient()
    try:
        await client.pause_campaign(seq.instantly_campaign_id)
    except InstantlyError as e:
        raise ValidationError(f"Failed to pause campaign: {e.detail}")

    seq.instantly_campaign_status = "paused"
    seq.updated_at = datetime.utcnow()
    session.add(seq)
    await session.commit()

    return {"status": "paused", "sequence_id": str(sequence_id), "campaign_id": seq.instantly_campaign_id}


@router.post("/resume/{sequence_id}")
async def resume_sequence(sequence_id: UUID, session: DBSession, _user: CurrentUser):
    """Resume a paused Instantly campaign."""
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")
    if not seq.instantly_campaign_id:
        raise ValidationError("Sequence is not linked to an Instantly campaign")

    client = InstantlyClient()
    try:
        await client.activate_campaign(seq.instantly_campaign_id)
    except InstantlyError as e:
        raise ValidationError(f"Failed to resume campaign: {e.detail}")

    seq.instantly_campaign_status = "active"
    seq.updated_at = datetime.utcnow()
    session.add(seq)
    await session.commit()

    return {"status": "resumed", "sequence_id": str(sequence_id), "campaign_id": seq.instantly_campaign_id}


# ── Replies ───────────────────────────────────────────────────────────────────

@router.get("/replies/{sequence_id}")
async def get_replies(sequence_id: UUID, session: DBSession, _user: CurrentUser):
    """Fetch reply emails from Instantly Unibox for a launched sequence."""
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")

    if not seq.instantly_campaign_id:
        return {"replies": []}

    contact = await session.get(Contact, seq.contact_id)

    client = InstantlyClient()
    replies = await client.get_reply_thread(
        lead_email=contact.email if contact else "",
        campaign_id=seq.instantly_campaign_id,
    )

    return {"sequence_id": str(sequence_id), "replies": replies}


# ── Campaign sync ───────────────────────────────────────────────────────────────

@router.post("/sync-campaign/{sequence_id}")
async def sync_campaign_from_instantly(
    sequence_id: UUID,
    session: DBSession,
    _user: CurrentUser,
):
    """
    Pull live campaign stats and lead statuses from Instantly and sync to CRM.
    
    Use this to:
    - Import an already-running Instantly campaign (link it to a CRM sequence)
    - Re-sync after webhook delivery issues
    - Bulk-update contact statuses from the Instantly campaign
    
    Syncs: lead status, interest status, open/click counts, and campaign analytics.
    Creates Activity records for milestones not yet captured via webhooks.
    """
    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")

    if not seq.instantly_campaign_id:
        raise ValidationError(
            "Sequence has no Instantly campaign. Launch it first or set instantly_campaign_id."
        )

    client = InstantlyClient()
    campaign_id = seq.instantly_campaign_id
    now = datetime.utcnow()

    # ── Pull campaign analytics ────────────────────────────────────────────────
    analytics = None
    try:
        analytics_list = await client.get_campaign_analytics(campaign_id=campaign_id)
        if analytics_list:
            analytics = analytics_list[0] if isinstance(analytics_list, list) else analytics_list
    except InstantlyError as e:
        # Non-fatal — continue syncing leads even if analytics endpoint fails
        import logging
        logging.getLogger(__name__).warning("Campaign analytics fetch failed: %s", e)

    if analytics:
        status_map = {0: "draft", 1: "active", 2: "paused", 3: "completed"}
        campaign_status = status_map.get(analytics.get("campaign_status"), "unknown")
        seq.instantly_campaign_status = campaign_status
        seq.updated_at = now
        session.add(seq)

    # ── Pull leads and sync status per-contact ─────────────────────────────────
    contact = await session.get(Contact, seq.contact_id)
    synced_count = 0
    skipped_count = 0

    if contact and contact.email:
        try:
            leads_result = await client.list_leads(
                campaign_id=campaign_id,
                search=contact.email,
                limit=5,
            )
            if leads_result:
                lead_items = leads_result.get("items") or []
                for lead in lead_items:
                    lead_email = (lead.get("email") or "").lower().strip()
                    if lead_email != (contact.email or "").lower().strip():
                        continue

                    synced_count += 1
                    lead_status = lead.get("status")
                    interest = lead.get("lt_interest_status")

                    # Map Instantly lead status -> CRM status
                    if lead_status == -1:
                        contact.sequence_status = "bounced"
                        contact.instantly_status = "bounced"
                        contact.email_verified = False
                    elif lead_status == -2:
                        contact.sequence_status = "unsubscribed"
                        contact.instantly_status = "unsubscribed"
                    elif interest == 2:
                        contact.sequence_status = "meeting_booked"
                        contact.instantly_status = "meeting_booked"
                    elif interest == 1:
                        contact.sequence_status = "interested"
                        contact.instantly_status = "interested"
                    # interest == -1 ("not interested") deliberately NOT mapped:
                    # Instantly stamps it on auto-replies/OOO/imports with no real
                    # reply (phantom negatives). Genuine negatives come via the
                    # human-set lead_not_interested webhook. (Matches instantly_sync.)
                    elif lead_status == 1:
                        contact.instantly_status = "active"

                    # Sync open/click counts
                    if lead.get("email_open_count", 0) > (contact.email_open_count or 0):
                        contact.email_open_count = lead["email_open_count"]
                        if lead.get("timestamp_last_open"):
                            contact.email_last_opened_at = datetime.fromisoformat(
                                lead["timestamp_last_open"].replace("Z", "+00:00")
                            ).replace(tzinfo=None)
                    if lead.get("email_click_count", 0) > (contact.email_click_count or 0):
                        contact.email_click_count = lead["email_click_count"]

                    contact.updated_at = now
                    session.add(contact)

                    # Create email_sent activity if lead was contacted but we have no sent event
                    if lead.get("timestamp_last_contact") and lead_status in {1, 3}:
                        last_contact = lead["timestamp_last_contact"]
                        ext_id = f"sync:{campaign_id}:{lead_email}:last_contact"
                        existing = (
                            await session.execute(
                                select(Activity.id).where(
                                    Activity.external_source_id == ext_id,
                                    Activity.source == "instantly",
                                ).limit(1)
                            )
                        ).scalar_one_or_none()
                        if not existing:
                            session.add(Activity(
                                contact_id=contact.id,
                                type="email",
                                source="instantly",
                                medium="email",
                                content=f"Email sent to {lead_email} (synced from Instantly campaign)",
                                email_subject=f"Campaign: {analytics.get('campaign_name', '')}" if analytics else None,
                                email_to=lead_email,
                                event_metadata={"synced_from": "instantly_campaign_sync", "lead": lead},
                                external_source="instantly",
                                external_source_id=ext_id,
                            ))
        except InstantlyError as e:
            raise ValidationError(f"Failed to sync leads from Instantly: {e.detail}")

    await session.commit()

    return {
        "sequence_id": str(sequence_id),
        "campaign_id": campaign_id,
        "campaign_status": seq.instantly_campaign_status,
        "analytics": analytics,
        "leads_synced": synced_count,
        "leads_skipped": skipped_count,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _steps_from_legacy(seq: OutreachSequence) -> list:
    """
    Convert the legacy email_1/2/3 fields into a list of step-like dicts
    so they can be pushed to Instantly even if OutreachStep records don't exist yet.
    Returns simple namespace objects with the needed attributes.
    """
    from types import SimpleNamespace

    steps = []
    pairs = [
        (seq.subject_1, seq.email_1, 0),
        (seq.subject_2, seq.email_2, 3),
        (seq.subject_3, seq.email_3, 7),
    ]
    for i, (subject, body, delay) in enumerate(pairs):
        if body:
            steps.append(SimpleNamespace(
                channel="email",
                subject=subject,
                body=body,
                delay_value=delay,
                delay_unit="Days",
                variants=None,
            ))
    return steps
