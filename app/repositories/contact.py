"""
Contact repository.

Key addition over the base: list_with_company_name() runs a LEFT JOIN against
companies so the frontend gets company_name in a single API call instead of two.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact, ContactRead
from app.models.outreach import OutreachSequence
from app.models.user import User
from app.repositories.base import BaseRepository
from app.services.contact_tracking import apply_contact_tracking

FREE_EMAIL_PROVIDERS = frozenset({
    "gmail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "aol.com",
    "protonmail.com",
    "me.com",
    "live.com",
})

ROLE_EMAIL_PATTERNS = (
    "%noreply%@%",
    "%no-reply%@%",
    "%donotreply%@%",
    "%do-not-reply%@%",
    "mailer-daemon@%",
    "postmaster@%",
    "notifications@%",
    "notification@%",
    "calendar@%",
    "invite@%",
    "invites@%",
    "support@%",
    "help@%",
    "billing@%",
    "admin@%",
    "info@%",
    "team@%",
    "updates@%",
    "alerts@%",
)

GENERIC_LAST_NAME_TOKENS = ("contact", "team", "support", "notifications", "notification")


def _parse_multi_query(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_uuid_values(value: str | None) -> list[UUID]:
    parsed: list[UUID] = []
    for item in _parse_multi_query(value):
        try:
            parsed.append(UUID(item))
        except ValueError:
            continue
    return parsed


class ContactRepository(BaseRepository[Contact]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Contact, session)

    async def list_with_company_name(
        self,
        company_id: Optional[UUID] = None,
        q: Optional[str] = None,
        persona: Optional[str] = None,
        sequence_status: Optional[str] = None,
        call_disposition: Optional[str] = None,
        email_state: Optional[str] = None,
        ae_id: Optional[str] = None,
        sdr_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        scope_any_match: bool = False,
        prospect_only: bool = False,
        timezone: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[ContactRead], int]:
        """
        Return contacts with company_name populated via SQL JOIN.

        This replaces the two-call pattern (GET /contacts + GET /companies)
        that the frontend was forced to use when company_name wasn't in the response.
        """
        ae_user = aliased(User)
        sdr_user = aliased(User)
        base_stmt = (
            select(
                Contact,
                Company.name.label("company_name"),
                ae_user.name.label("assigned_to_name"),
                sdr_user.name.label("sdr_name"),
            )
            .outerjoin(Company, Contact.company_id == Company.id)
            .outerjoin(ae_user, Contact.assigned_to_id == ae_user.id)
            .outerjoin(sdr_user, Contact.sdr_id == sdr_user.id)
        )
        count_stmt = select(func.count(Contact.id)).select_from(Contact).outerjoin(
            Company, Contact.company_id == Company.id
        )

        if company_id:
            base_stmt = base_stmt.where(Contact.company_id == company_id)
            count_stmt = count_stmt.where(Contact.company_id == company_id)

        if prospect_only:
            # Always surface contacts that a rep explicitly added through the UI
            # or the prospect CSV import — those are deliberate and must not be
            # hidden by hygiene filters (e.g. a Test@beacon.li dogfood prospect).
            manual_override = or_(
                Contact.enrichment_data.contains({"source": "manual_prospect"}),
                Contact.enrichment_data.contains({"source": "prospect_csv_upload"}),
            )

            email_domain = func.lower(func.split_part(Contact.email, "@", 2))
            normalized_company_domain = func.lower(func.replace(Company.domain, "www.", ""))
            business_domain_mismatch = and_(
                Contact.email.is_not(None),
                Contact.email != "",
                Company.domain.is_not(None),
                Company.domain != "",
                ~Company.domain.ilike("%.unknown"),
                ~email_domain.in_(tuple(FREE_EMAIL_PROVIDERS)),
                email_domain != normalized_company_domain,
            )
            lower_email = func.lower(Contact.email)
            role_mailbox_filter = and_(
                Contact.email.is_not(None),
                or_(*[lower_email.like(pattern) for pattern in ROLE_EMAIL_PATTERNS]),
            )
            placeholder_name_filter = and_(
                func.lower(func.coalesce(Contact.last_name, "")).in_(GENERIC_LAST_NAME_TOKENS),
                or_(Contact.title.is_(None), Contact.title == ""),
                or_(Contact.linkedin_url.is_(None), Contact.linkedin_url == ""),
            )
            # Junk filter: only exclude truly-automated noise (zippy+ test bot,
            # clickup import placeholders, obvious role mailboxes, placeholder
            # names, domain-mismatch enrichment misses). A rep-created contact
            # passes via `manual_override` above.
            junk_filter_combined = and_(
                ~func.lower(func.coalesce(Contact.email, "")).like("zippy+%@beacon.li"),
                ~role_mailbox_filter,
                ~placeholder_name_filter,
                ~Contact.enrichment_data.contains({"source": "clickup_import_placeholder"}),
                ~Contact.enrichment_data.contains({"source": "personal_email_sync"}),
                ~business_domain_mismatch,
            )
            combined_filter = or_(manual_override, junk_filter_combined)
            base_stmt = base_stmt.where(combined_filter)
            count_stmt = count_stmt.where(combined_filter)

        normalized_q = (q or "").strip()
        if normalized_q:
            pattern = f"%{normalized_q}%"
            search_filter = or_(
                Contact.first_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.email.ilike(pattern),
                Contact.title.ilike(pattern),
                Company.name.ilike(pattern),
                func.concat(Contact.first_name, " ", Contact.last_name).ilike(pattern),
            )
            base_stmt = base_stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        persona_values = _parse_multi_query(persona)
        if persona_values:
            include_unknown = "unknown" in persona_values
            named_personas = [value for value in persona_values if value != "unknown"]
            clauses = []
            if named_personas:
                clauses.append(Contact.persona.in_(named_personas))
            if include_unknown:
                clauses.append(or_(Contact.persona.is_(None), Contact.persona == "", Contact.persona == "unknown"))
            persona_filter = or_(*clauses) if clauses else None
        else:
            persona_filter = None

        if persona_filter is not None:
            base_stmt = base_stmt.where(persona_filter)
            count_stmt = count_stmt.where(persona_filter)

        sequence_values = _parse_multi_query(sequence_status)
        if sequence_values:
            sequence_filter = Contact.sequence_status.in_(sequence_values)
            base_stmt = base_stmt.where(sequence_filter)
            count_stmt = count_stmt.where(sequence_filter)

        call_disposition_values = _parse_multi_query(call_disposition)
        if call_disposition_values:
            include_unreviewed = "unreviewed" in call_disposition_values
            named_dispositions = [value for value in call_disposition_values if value != "unreviewed"]
            clauses = []
            if named_dispositions:
                clauses.append(Contact.call_disposition.in_(named_dispositions))
            if include_unreviewed:
                clauses.append(or_(Contact.call_disposition.is_(None), Contact.call_disposition == ""))
            disposition_filter = or_(*clauses) if clauses else None
            if disposition_filter is not None:
                base_stmt = base_stmt.where(disposition_filter)
                count_stmt = count_stmt.where(disposition_filter)

        email_filters = []
        for state in _parse_multi_query(email_state):
            if state == "has_email":
                email_filters.append(and_(Contact.email.is_not(None), Contact.email != ""))
            elif state == "missing_email":
                email_filters.append(or_(Contact.email.is_(None), Contact.email == ""))
            elif state == "verified":
                email_filters.append(Contact.email_verified.is_(True))
            elif state == "unverified":
                email_filters.append(Contact.email_verified.is_(False))
        email_filter = or_(*email_filters) if email_filters else None

        if email_filter is not None:
            base_stmt = base_stmt.where(email_filter)
            count_stmt = count_stmt.where(email_filter)

        ae_ids = _parse_uuid_values(ae_id)
        sdr_ids = _parse_uuid_values(sdr_id)
        owner_ids = _parse_uuid_values(owner_id)

        if owner_ids:
            owner_filter = or_(
                Contact.assigned_to_id.in_(owner_ids),
                Contact.sdr_id.in_(owner_ids),
            )
            base_stmt = base_stmt.where(owner_filter)
            count_stmt = count_stmt.where(owner_filter)

        if scope_any_match and (ae_ids or sdr_ids):
            clauses = []
            if ae_ids:
                clauses.append(Contact.assigned_to_id.in_(ae_ids))
            if sdr_ids:
                clauses.append(Contact.sdr_id.in_(sdr_ids))
            scope_filter = or_(*clauses) if len(clauses) > 1 else clauses[0]
            base_stmt = base_stmt.where(scope_filter)
            count_stmt = count_stmt.where(scope_filter)
        else:
            if ae_ids:
                ae_filter = Contact.assigned_to_id.in_(ae_ids)
                base_stmt = base_stmt.where(ae_filter)
                count_stmt = count_stmt.where(ae_filter)

            if sdr_ids:
                sdr_filter = Contact.sdr_id.in_(sdr_ids)
                base_stmt = base_stmt.where(sdr_filter)
                count_stmt = count_stmt.where(sdr_filter)

        # Timezone filter: comma-separated list. Each value is matched
        # case-insensitively against Contact.timezone (e.g. "Asia/Kolkata").
        if timezone:
            tz_values = [tz.strip().lower() for tz in timezone.split(",") if tz.strip()]
            if tz_values:
                tz_filter = func.lower(Contact.timezone).in_(tz_values)
                base_stmt = base_stmt.where(tz_filter)
                count_stmt = count_stmt.where(tz_filter)

        total = (await self.session.execute(count_stmt)).scalar_one()

        rows = (
            await self.session.execute(
                base_stmt
                .order_by(Contact.created_at.desc(), Contact.id.desc())
                .offset(skip)
                .limit(limit)
            )
        ).all()

        result: list[ContactRead] = []
        for contact, company_name, assigned_to_name, sdr_name in rows:
            read = ContactRead.model_validate(contact)
            read.company_name = company_name
            read.assigned_to_name = assigned_to_name
            read.sdr_name = sdr_name
            result.append(read)

        await apply_contact_tracking(self.session, result)
        return result, total

    async def delete_all(self) -> None:
        """Delete all contacts and their dependent records. Admin only."""
        from sqlalchemy import delete as sa_delete
        await self.session.execute(sa_delete(OutreachSequence))
        await self.session.execute(sa_delete(Activity).where(Activity.contact_id.isnot(None)))
        await self.session.execute(sa_delete(Contact))
        await self.session.commit()

    async def delete_with_cascade(self, contact_id: UUID) -> None:
        """Delete contact + dependent outreach_sequences and activities."""
        for seq in (
            await self.session.execute(
                select(OutreachSequence).where(OutreachSequence.contact_id == contact_id)
            )
        ).scalars().all():
            await self.session.delete(seq)

        for act in (
            await self.session.execute(
                select(Activity).where(Activity.contact_id == contact_id)
            )
        ).scalars().all():
            await self.session.delete(act)

        contact = await self.get(contact_id)
        if contact:
            await self.session.delete(contact)

        await self.session.commit()
