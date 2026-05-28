"""
Contact repository.

Key addition over the base: list_with_company_name() runs a LEFT JOIN against
companies so the frontend gets company_name in a single API call instead of two.
"""
import re
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select
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


def _parse_multi_values(values: Optional[list[str]]) -> list[str]:
    """Normalize FastAPI repeatable params plus frontend comma-joined values."""
    parsed: list[str] = []
    for value in values or []:
        parsed.extend(_parse_multi_query(value))
    return parsed


def _parse_uuid_values(value: str | None) -> list[UUID]:
    parsed: list[UUID] = []
    for item in _parse_multi_query(value):
        try:
            parsed.append(UUID(item))
        except ValueError:
            continue
    return parsed


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9@.+'-]+", value.lower())
        if token
    ]


class ContactRepository(BaseRepository[Contact]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Contact, session)

    async def list_with_company_name(
        self,
        company_id: Optional[UUID] = None,
        q: Optional[str] = None,
        q_field: Optional[str] = None,
        q_match: Optional[str] = None,
        persona: Optional[str] = None,
        sequence_status: Optional[str] = None,
        call_disposition: Optional[str] = None,
        email_state: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        ae_id: Optional[str] = None,
        sdr_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        scope_any_match: bool = False,
        prospect_only: bool = False,
        timezone: Optional[str] = None,
        call_outcome_color: Optional[list[str]] = None,
        email_outcome_color: Optional[list[str]] = None,
        call_attempts_bucket: Optional[list[str]] = None,
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
        # Correlated subquery: number of activity rows of type='call' for this
        # contact. Drives the new prospect-page progress dots (one yellow dot
        # per attempt) and the `call_attempts_bucket` filter below.
        call_attempt_count_subq = (
            select(func.count(Activity.id))
            .where(Activity.contact_id == Contact.id)
            .where(Activity.type == "call")
            .correlate(Contact)
            .scalar_subquery()
        )
        base_stmt = (
            select(
                Contact,
                Company.name.label("company_name"),
                ae_user.name.label("assigned_to_name"),
                sdr_user.name.label("sdr_name"),
                call_attempt_count_subq.label("call_attempt_count"),
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
        scope_field = (q_field or "").strip().lower() or None
        match_mode = (q_match or "").strip().lower() or "contains"
        if match_mode not in {"exact", "contains"}:
            match_mode = "contains"
        search_rank = None
        if normalized_q and scope_field and scope_field != "all":
            # Scoped search: match the term inside a single column instead of
            # the multi-field blob below. Used by the in-UI column dropdown
            # on the prospects page.
            #
            # Bulk mode: if the term contains commas or newlines we treat each
            # piece as a separate value and OR them together. That way reps
            # can paste a list of company names / domains / emails and filter
            # the whole set in one go. Single-value queries fall through to
            # the same path with a list of one — same code, no special case.
            raw_terms = [t.strip() for t in re.split(r"[,\n]+", normalized_q) if t.strip()]
            full_name = func.lower(func.trim(func.concat(func.coalesce(Contact.first_name, ""), " ", func.coalesce(Contact.last_name, ""))))

            def _term_filter(term: str):
                lowered = term.lower()
                escaped = _like_escape(lowered)
                digits = re.sub(r"\D+", "", term)
                if scope_field == "phone":
                    # Phone match always strips non-digits; "exact" means the
                    # cell's digits equal the term's digits.
                    phone_col = func.regexp_replace(func.coalesce(Contact.phone, ""), r"[^0-9]", "", "g")
                    if not digits:
                        return None
                    if match_mode == "exact":
                        return phone_col == digits
                    return phone_col.like(f"%{digits}%")
                # Exact-match for "name" widens to first / last / full — a
                # rep pasting "Marcus, Mei" expects to hit those first names,
                # not just rows where the whole "Marcus Lindberg" string
                # equals "Marcus".
                if scope_field == "name":
                    first_c = func.lower(func.coalesce(Contact.first_name, ""))
                    last_c = func.lower(func.coalesce(Contact.last_name, ""))
                    if match_mode == "exact":
                        return or_(first_c == lowered, last_c == lowered, full_name == lowered)
                    return full_name.like(f"%{escaped}%", escape="\\")
                col_map = {
                    "email": func.lower(func.coalesce(Contact.email, "")),
                    "company": func.lower(func.coalesce(Company.name, "")),
                    "title": func.lower(func.coalesce(Contact.title, "")),
                    "linkedin": func.lower(func.coalesce(Contact.linkedin_url, "")),
                }
                col = col_map.get(scope_field)
                if col is None:
                    return None
                if match_mode == "exact":
                    return col == lowered
                return col.like(f"%{escaped}%", escape="\\")

            per_term = [f for f in (_term_filter(t) for t in raw_terms) if f is not None]
            if per_term:
                scoped_filter = or_(*per_term) if len(per_term) > 1 else per_term[0]
                base_stmt = base_stmt.where(scoped_filter)
                count_stmt = count_stmt.where(scoped_filter)
                normalized_q = ""  # consume so the broad search below is skipped
        if normalized_q:
            lowered_q = " ".join(normalized_q.lower().split())
            escaped_q = _like_escape(lowered_q)
            digits_q = re.sub(r"\D+", "", normalized_q)
            tokens = _search_tokens(normalized_q)

            first = func.lower(func.coalesce(Contact.first_name, ""))
            last = func.lower(func.coalesce(Contact.last_name, ""))
            email = func.lower(func.coalesce(Contact.email, ""))
            title = func.lower(func.coalesce(Contact.title, ""))
            company_name = func.lower(func.coalesce(Company.name, ""))
            full_name = func.lower(func.trim(func.concat(func.coalesce(Contact.first_name, ""), " ", func.coalesce(Contact.last_name, ""))))
            reverse_name = func.lower(func.trim(func.concat(func.coalesce(Contact.last_name, ""), " ", func.coalesce(Contact.first_name, ""))))
            phone_digits = func.regexp_replace(func.coalesce(Contact.phone, ""), r"[^0-9]", "", "g")
            search_blob = func.concat_ws(
                " ",
                first,
                last,
                email,
                func.lower(func.coalesce(Contact.phone, "")),
                title,
                company_name,
            )
            exact_name = or_(full_name == lowered_q, reverse_name == lowered_q)
            email_exact = email == lowered_q if "@" in lowered_q else false()
            email_prefix = email.like(f"{escaped_q}%", escape="\\")
            phone_match = phone_digits.like(f"%{digits_q}%") if len(digits_q) >= 4 else false()
            token_filter = and_(
                *[search_blob.like(f"%{_like_escape(token)}%", escape="\\") for token in tokens]
            ) if tokens else false()
            first_last_token_match = (
                and_(
                    first.like(f"{_like_escape(tokens[0])}%", escape="\\"),
                    last.like(f"{_like_escape(tokens[-1])}%", escape="\\"),
                )
                if len(tokens) >= 2
                else false()
            )
            search_filter = or_(
                exact_name,
                email_exact,
                phone_match,
                first_last_token_match,
                email_prefix,
                token_filter,
            )
            search_rank = case(
                (exact_name, 0),
                (email_exact, 1),
                (phone_digits == digits_q, 2) if len(digits_q) >= 4 else (false(), 2),
                (first_last_token_match, 3),
                (full_name.like(f"{escaped_q}%", escape="\\"), 4),
                (email_prefix, 5),
                (company_name.like(f"{escaped_q}%", escape="\\"), 6),
                else_=9,
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

        # Call-outcome color filter. Each color maps to a set of call_disposition
        # values; "yellow" means "attempts exist but no decisive outcome". The
        # frontend prospect-page uses these to render dot colors.
        CALL_COLOR_DISPOSITIONS: dict[str, set[str]] = {
            "green": {
                "demo_scheduled_booked",
                "meeting_confirmed",
            },
            "red": {
                "connected_not_interested",
                "contact_poor_fit",
                "gatekeeper_connected_to_admin",
                "do_not_contact_dnc",
                "invalid_number_wrong_number",
            },
            "blue": {
                "interested_follow_up_required",
                "call_back_later_rescheduled",
            },
        }
        ALL_KNOWN_CALL_DISPOSITIONS = (
            CALL_COLOR_DISPOSITIONS["green"]
            | CALL_COLOR_DISPOSITIONS["red"]
            | CALL_COLOR_DISPOSITIONS["blue"]
        )

        if call_outcome_color:
            colors = [c.strip().lower() for c in _parse_multi_values(call_outcome_color)]
            clauses = []
            for color in colors:
                if color in CALL_COLOR_DISPOSITIONS:
                    clauses.append(
                        Contact.call_disposition.in_(tuple(CALL_COLOR_DISPOSITIONS[color]))
                    )
                elif color == "yellow":
                    clauses.append(
                        and_(
                            call_attempt_count_subq > 0,
                            or_(
                                Contact.call_disposition.is_(None),
                                Contact.call_disposition == "",
                                ~Contact.call_disposition.in_(tuple(ALL_KNOWN_CALL_DISPOSITIONS)),
                            ),
                        )
                    )
            if clauses:
                call_color_filter = or_(*clauses) if len(clauses) > 1 else clauses[0]
                base_stmt = base_stmt.where(call_color_filter)
                count_stmt = count_stmt.where(call_color_filter)

        if email_outcome_color:
            colors = [c.strip().lower() for c in _parse_multi_values(email_outcome_color)]
            email_positive = ("replied", "meeting_booked")
            email_negative = "not_interested"
            terminal_states = ("replied", "meeting_booked", "not_interested")
            clauses = []
            for color in colors:
                if color == "green":
                    clauses.append(Contact.sequence_status.in_(email_positive))
                elif color == "red":
                    clauses.append(Contact.sequence_status == email_negative)
                elif color == "blue":
                    clauses.append(
                        and_(
                            Contact.email_open_count > 0,
                            or_(
                                Contact.sequence_status.is_(None),
                                ~Contact.sequence_status.in_(terminal_states),
                            ),
                        )
                    )
                elif color == "yellow":
                    clauses.append(
                        and_(
                            Contact.sequence_status.in_(("queued_instantly", "sent")),
                            Contact.email_open_count == 0,
                        )
                    )
            if clauses:
                email_color_filter = or_(*clauses) if len(clauses) > 1 else clauses[0]
                base_stmt = base_stmt.where(email_color_filter)
                count_stmt = count_stmt.where(email_color_filter)

        if call_attempts_bucket:
            buckets = [b.strip().lower() for b in _parse_multi_values(call_attempts_bucket)]
            clauses = []
            for bucket in buckets:
                if bucket == "0":
                    clauses.append(call_attempt_count_subq == 0)
                elif bucket == "1":
                    clauses.append(call_attempt_count_subq == 1)
                elif bucket == "2":
                    clauses.append(call_attempt_count_subq == 2)
                elif bucket == "3":
                    clauses.append(call_attempt_count_subq == 3)
                elif bucket == "4plus":
                    clauses.append(call_attempt_count_subq >= 4)
            if clauses:
                attempts_filter = or_(*clauses) if len(clauses) > 1 else clauses[0]
                base_stmt = base_stmt.where(attempts_filter)
                count_stmt = count_stmt.where(attempts_filter)

        # Timezone filter: comma-separated list. Each value is matched
        # case-insensitively against Contact.timezone (e.g. "Asia/Kolkata").
        if timezone:
            tz_values = [tz.strip().lower() for tz in timezone.split(",") if tz.strip()]
            if tz_values:
                tz_filter = func.lower(Contact.timezone).in_(tz_values)
                base_stmt = base_stmt.where(tz_filter)
                count_stmt = count_stmt.where(tz_filter)

        total = (await self.session.execute(count_stmt)).scalar_one()

        # Explicit sort overrides the default search-rank + created_at ordering.
        # When a sort is given we *bypass* the search ranking so paging is
        # deterministic across pages — otherwise a rank-tied alphabetical
        # request would drift between fetches.
        normalized_sort = (sort_by or "").strip().lower() or None
        normalized_dir = (sort_dir or "").strip().lower()
        descending = normalized_dir == "desc"
        sort_expr_map = {
            "name": func.lower(func.coalesce(Contact.first_name, "") + " " + func.coalesce(Contact.last_name, "")),
            "first_name": func.lower(func.coalesce(Contact.first_name, "")),
            "last_name": func.lower(func.coalesce(Contact.last_name, "")),
            "company": func.lower(func.coalesce(Company.name, "")),
            "email": func.lower(func.coalesce(Contact.email, "")),
            "title": func.lower(func.coalesce(Contact.title, "")),
            "created_at": Contact.created_at,
        }
        sort_expr = sort_expr_map.get(normalized_sort) if normalized_sort else None

        if sort_expr is not None:
            primary = sort_expr.desc() if descending else sort_expr.asc()
            order_clauses = (primary, Contact.id.desc())
        else:
            order_clauses = (
                *((search_rank.asc(),) if search_rank is not None else ()),
                Contact.created_at.desc(),
                Contact.id.desc(),
            )

        rows = (
            await self.session.execute(
                base_stmt
                .order_by(*order_clauses)
                .offset(skip)
                .limit(limit)
            )
        ).all()

        result: list[ContactRead] = []
        for contact, company_name, assigned_to_name, sdr_name, call_attempt_count in rows:
            read = ContactRead.model_validate(contact)
            read.company_name = company_name
            read.assigned_to_name = assigned_to_name
            read.sdr_name = sdr_name
            read.call_attempt_count = int(call_attempt_count or 0)
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
