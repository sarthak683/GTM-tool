from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.config import settings
from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import NotFoundError
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.outreach import OutreachSequence, OutreachStep
from app.services.pre_meeting import generate_account_brief

try:  # EmailStr needs the optional `email-validator` package; degrade gracefully.
    from pydantic import EmailStr
except Exception:  # pragma: no cover - missing email-validator at runtime
    EmailStr = str  # type: ignore[assignment,misc]

router = APIRouter(tags=["intelligence"])


class OutreachSendPayload(BaseModel):
    """Validated body for sending one outreach touch."""

    email_number: int
    to_email: EmailStr


@router.get("/intelligence/{company_id}")
async def get_account_brief(company_id: UUID, session: DBSession, current_user: CurrentUser):
    """
    Company-level account planning brief:
    combines saved CRM signals, stakeholder coverage, cached enrichment,
    lightweight website research, and GPT synthesis into a seller-friendly brief.
    """
    result = await generate_account_brief(company_id, session)
    if "error" in result:
        raise NotFoundError(result["error"])
    return result


@router.post("/outreach/send/{sequence_id}")
async def send_outreach_email(
    sequence_id: UUID,
    payload: OutreachSendPayload,
    session: DBSession,
    current_user: CurrentUser,
):
    """
    Send one touch of an outreach sequence via Resend.
    Body: { "email_number": 1|2|3, "to_email": "prospect@company.com" }
    
    Creates an Activity record so the sent email is tracked in sales analytics
    and appears on the prospect and deal timelines.
    """
    from app.clients.resend_client import send_email

    seq = await session.get(OutreachSequence, sequence_id)
    if not seq:
        raise NotFoundError("Sequence not found")

    contact = await session.get(Contact, seq.contact_id)
    email_number = payload.email_number
    to_email = (str(payload.to_email) or "").strip()

    if not to_email:
        if contact and contact.email:
            to_email = contact.email
        else:
            raise HTTPException(
                status_code=400,
                detail="No email address provided and contact has no email on file",
            )

    # Prefer OutreachStep records for the body/subject; fall back to legacy fields
    step_rows = (
        await session.execute(
            select(OutreachStep)
            .where(OutreachStep.sequence_id == sequence_id, OutreachStep.step_number == email_number)
            .limit(1)
        )
    ).scalars().first()

    if step_rows and step_rows.body:
        body = step_rows.body
        subject = step_rows.subject or (f"Re: {contact.first_name}" if contact else "Following up")
    else:
        body, subject = {
            1: (seq.email_1, seq.subject_1),
            2: (seq.email_2, seq.subject_2),
            3: (seq.email_3, seq.subject_3),
        }.get(email_number, (seq.email_1, seq.subject_1))

    if not body:
        raise HTTPException(
            status_code=400,
            detail=f"Email {email_number} has no content. Generate the sequence first.",
        )

    result = await send_email(
        to=to_email,
        subject=subject or "Following up from Beacon.li",
        body=body,
    )

    now = datetime.utcnow()
    resend_id = result.get("id") if isinstance(result, dict) else None

    # Create Activity so this email appears in timelines and analytics
    activity = Activity(
        contact_id=contact.id if contact else None,
        deal_id=None,
        type="email",
        source="manual",
        medium="email",
        content=f"Email sent (step {email_number}): {subject} → {to_email}",
        email_subject=subject,
        email_from=settings.RESEND_FROM_EMAIL,
        email_to=to_email,
        event_metadata={
            "sequence_id": str(sequence_id),
            "email_number": email_number,
            "resend_id": resend_id,
            "sent_at": now.isoformat(),
        },
        external_source="resend",
        external_source_id=resend_id,
    )
    session.add(activity)

    # Update sequence + contact status for pipeline visibility
    seq.status = "sent"
    seq.updated_at = now
    session.add(seq)

    if contact:
        if (contact.sequence_status or "") in {"queued_instantly", "ready", "", None}:
            contact.sequence_status = "sent"
        if (contact.instantly_status or "") in {"", None}:
            contact.instantly_status = "sent"
        contact.updated_at = now
        session.add(contact)

    await session.commit()
    await session.refresh(activity)

    return {
        "sequence_id": str(sequence_id),
        "email_number": email_number,
        "to": to_email,
        "subject": subject,
        "resend_id": resend_id,
        "status": result.get("status"),
        "activity_id": str(activity.id),
        "activity_created": True,
    }
