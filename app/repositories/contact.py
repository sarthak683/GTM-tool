"""
Contact repository.

Key addition over the base: list_with_company_name() runs a LEFT JOIN against
companies so the frontend gets company_name in a single API call instead of two.
"""
import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact, ContactRead
from app.models.outreach import OutreachSequence
from app.models.user import User
from app.repositories.base import BaseRepository
from app.services.contact_tracking import apply_contact_tracking


async def get_or_create_contact_by_email(
    session: AsyncSession,
    email: str,
    defaults: Optional[dict] = None,
) -> tuple[Contact, bool]:
    """Case-insensitive dedup on email. Returns ``(contact, created)``.

    Single funnel for the ~dozen contact-creation sites so none of them can mint
    a duplicate. Safe under the partial unique index on ``lower(email)``: if a
    concurrent writer (or a path that skipped the pre-lookup) already inserted
    the same address, the nested-savepoint insert hits the constraint, we roll
    back ONLY that savepoint (not the caller's transaction) and re-fetch the
    winning row. Callers get the existing contact instead of an IntegrityError.
    """
    normalized = (email or "").strip()
    fields = {k: v for k, v in (defaults or {}).items() if k != "email"}

    if not normalized:
        # No email -> nothing to dedup on; the partial unique index ignores
        # null/empty emails, so just create the row.
        contact = Contact(email=None, **fields)
        session.add(contact)
        await session.flush()
        return contact, True

    key = normalized.lower()

    async def _fetch() -> Optional[Contact]:
        res = await session.execute(
            select(Contact).where(func.lower(Contact.email) == key).limit(1)
        )
        return res.scalar_one_or_none()

    existing = await _fetch()
    if existing is not None:
        return existing, False

    contact = Contact(email=normalized, **fields)
    try:
        async with session.begin_nested():
            session.add(contact)
            await session.flush()
        return contact, True
    except IntegrityError:
        existing = await _fetch()
        if existing is not None:
            return existing, False
        raise

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


# Sentinel the frontend sends to request "no owner" in an ownership filter. It
# can never be a real UUID, so _parse_uuid_values silently drops it; we detect
# it on the raw string instead and translate it to an IS NULL clause.
UNASSIGNED_SENTINEL = "__unassigned__"


def _has_unassigned(value: str | None) -> bool:
    return UNASSIGNED_SENTINEL in _parse_multi_query(value)


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9@.+'-]+", value.lower())
        if token
    ]


def contact_visibility_filter(user_id: UUID):
    """SQLAlchemy predicate enforcing prospect visibility for ONE non-admin user.

    A non-admin may see a contact if they own it in either slot, OR they are the
    AE on the contact's COMPANY (account-scoped: an AE sees every prospect inside
    the accounts they own, including ones an SDR sourced and hasn't handed over
    yet), OR they own a DEAL on the contact's company (an AE running a demo/POC
    sees the prospects at that account even when the company/contacts are still
    held by the sourcing SDR or another company AE), OR it is fully unassigned
    (both slots NULL — an unclaimed lead anyone may pick up). This is the SINGLE
    SOURCE OF TRUTH for the rule; reuse it on EVERY contact-browse surface (the
    prospects list, the account-sourcing company page, global search) so
    visibility can never diverge between surfaces. Mirrors the inline `.in_()`
    form in ``list_with_company_name`` (which supports a multi-id list).
    """
    from app.models.deal import Deal
    return or_(
        Contact.assigned_to_id == user_id,
        Contact.sdr_id == user_id,
        Contact.company_id.in_(
            select(Company.id).where(Company.assigned_to_id == user_id)
        ),
        Contact.company_id.in_(
            select(Deal.company_id).where(
                Deal.assigned_to_id == user_id, Deal.company_id.is_not(None)
            )
        ),
        and_(Contact.assigned_to_id.is_(None), Contact.sdr_id.is_(None)),
    )


async def visible_contact_restriction(session: AsyncSession, user):
    """Return the visibility predicate for `user`, or None if they may see ALL
    prospects (admins + users in the view-all grant list).

    Apply with ``stmt = stmt.where(restriction)`` only when not None. Keeps every
    endpoint's gate consistent with the main list's `can_view_all_prospects` rule.
    """
    from app.services.permissions import can_view_all_prospects

    if await can_view_all_prospects(session, user):
        return None
    return contact_visibility_filter(user.id)


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
        linkedin_status: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        ae_id: Optional[str] = None,
        sdr_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        restrict_to_owner_id: Optional[str] = None,
        scope_any_match: bool = False,
        prospect_only: bool = False,
        timezone: Optional[str] = None,
        call_outcome_color: Optional[list[str]] = None,
        email_outcome_color: Optional[list[str]] = None,
        call_attempts_bucket: Optional[list[str]] = None,
        call_attempt_min: Optional[int] = None,
        call_attempt_max: Optional[int] = None,
        next_followup_after: Optional[datetime] = None,
        next_followup_before: Optional[datetime] = None,
        call_last_after: Optional[datetime] = None,
        call_last_before: Optional[datetime] = None,
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
        # Rep comments are stored as Activity rows (type='comment'). Surface the
        # newest one + a count so the prospect table can show a "Comments" column
        # at a glance; the full history is fetched on demand via GET /activities.
        latest_comment_subq = (
            select(Activity.content)
            .where(Activity.contact_id == Contact.id)
            .where(Activity.type == "comment")
            .order_by(Activity.created_at.desc())
            .limit(1)
            .correlate(Contact)
            .scalar_subquery()
        )
        comment_count_subq = (
            select(func.count(Activity.id))
            .where(Activity.contact_id == Contact.id)
            .where(Activity.type == "comment")
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
                latest_comment_subq.label("latest_comment"),
                comment_count_subq.label("comment_count"),
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

        # LinkedIn-status filter — mirrors call_disposition. Named values are
        # sent / accepted / follow_up / meeting_booked / meeting_rejected;
        # "not_contacted" matches prospects with no LinkedIn touch logged yet
        # (status null/empty/"none").
        linkedin_status_values = _parse_multi_query(linkedin_status)
        if linkedin_status_values:
            include_not_contacted = "not_contacted" in linkedin_status_values
            named_statuses = [v for v in linkedin_status_values if v != "not_contacted"]
            clauses = []
            if named_statuses:
                clauses.append(Contact.linkedin_status.in_(named_statuses))
            if include_not_contacted:
                clauses.append(or_(
                    Contact.linkedin_status.is_(None),
                    Contact.linkedin_status == "",
                    Contact.linkedin_status == "none",
                ))
            linkedin_filter = or_(*clauses) if clauses else None
            if linkedin_filter is not None:
                base_stmt = base_stmt.where(linkedin_filter)
                count_stmt = count_stmt.where(linkedin_filter)

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
        # "Unassigned" selections: each maps to an IS NULL clause on the matching
        # ownership slot(s). Detected on the raw param string (the sentinel is
        # dropped by UUID parsing above).
        ae_unassigned = _has_unassigned(ae_id)
        sdr_unassigned = _has_unassigned(sdr_id)
        owner_unassigned = _has_unassigned(owner_id)

        # Hard server-side visibility gate (NOT user-selectable): when set, the
        # viewer may see prospects they own (either ownership slot), prospects in
        # an account they own as AE (account-scoped — an AE sees everything inside
        # their accounts, incl. SDR-sourced prospects not yet handed over), or
        # unowned prospects (both slots empty). Admins/granted users bypass this
        # by passing restrict_to_owner_id=None. ANDed with every other filter.
        # Keep in lockstep with contact_visibility_filter() above.
        restrict_ids = _parse_uuid_values(restrict_to_owner_id)
        if restrict_ids:
            from app.models.deal import Deal
            visibility_filter = or_(
                Contact.assigned_to_id.in_(restrict_ids),
                Contact.sdr_id.in_(restrict_ids),
                Contact.company_id.in_(
                    select(Company.id).where(Company.assigned_to_id.in_(restrict_ids))
                ),
                # Deal owner (e.g. the AE running the demo/POC) sees the account's
                # prospects even when the company/contacts sit with the sourcing
                # SDR or another company AE. Lockstep with contact_visibility_filter().
                Contact.company_id.in_(
                    select(Deal.company_id).where(
                        Deal.assigned_to_id.in_(restrict_ids), Deal.company_id.is_not(None)
                    )
                ),
                and_(Contact.assigned_to_id.is_(None), Contact.sdr_id.is_(None)),
            )
            base_stmt = base_stmt.where(visibility_filter)
            count_stmt = count_stmt.where(visibility_filter)

        if owner_ids or owner_unassigned:
            owner_clauses = []
            if owner_ids:
                owner_clauses.append(
                    or_(
                        Contact.assigned_to_id.in_(owner_ids),
                        Contact.sdr_id.in_(owner_ids),
                    )
                )
            if owner_unassigned:
                # Owner is empty only when BOTH ownership slots are null.
                owner_clauses.append(
                    and_(Contact.assigned_to_id.is_(None), Contact.sdr_id.is_(None))
                )
            owner_filter = or_(*owner_clauses) if len(owner_clauses) > 1 else owner_clauses[0]
            base_stmt = base_stmt.where(owner_filter)
            count_stmt = count_stmt.where(owner_filter)

        # Per-slot filter terms, each optionally including an "unassigned" (IS
        # NULL) alternative for that slot.
        ae_term = None
        if ae_ids and ae_unassigned:
            ae_term = or_(Contact.assigned_to_id.in_(ae_ids), Contact.assigned_to_id.is_(None))
        elif ae_ids:
            ae_term = Contact.assigned_to_id.in_(ae_ids)
        elif ae_unassigned:
            ae_term = Contact.assigned_to_id.is_(None)

        sdr_term = None
        if sdr_ids and sdr_unassigned:
            sdr_term = or_(Contact.sdr_id.in_(sdr_ids), Contact.sdr_id.is_(None))
        elif sdr_ids:
            sdr_term = Contact.sdr_id.in_(sdr_ids)
        elif sdr_unassigned:
            sdr_term = Contact.sdr_id.is_(None)

        if scope_any_match and (ae_term is not None or sdr_term is not None):
            clauses = [t for t in (ae_term, sdr_term) if t is not None]
            scope_filter = or_(*clauses) if len(clauses) > 1 else clauses[0]
            base_stmt = base_stmt.where(scope_filter)
            count_stmt = count_stmt.where(scope_filter)
        else:
            if ae_term is not None:
                base_stmt = base_stmt.where(ae_term)
                count_stmt = count_stmt.where(ae_term)

            if sdr_term is not None:
                base_stmt = base_stmt.where(sdr_term)
                count_stmt = count_stmt.where(sdr_term)

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
                    # EMAIL negative only. sequence_status='not_interested' is an
                    # OVERLOADED field also written by negative CALL/LinkedIn
                    # dispositions, so a phone "not interested" was wrongly shown
                    # as a negative EMAIL reply. instantly_status='not_interested'
                    # is set ONLY by the genuine email paths (Instantly negative
                    # webhook / reply), so it's the correct email-sourced marker.
                    clauses.append(Contact.instantly_status == email_negative)
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

        # Follow-up count range — inclusive min/max over the same call-attempt
        # subquery the buckets use. Lets a rep ask "called between 2 and 5
        # times" without being limited to fixed buckets. Either bound is
        # optional; an open-ended range (min only / max only) is valid.
        if call_attempt_min is not None:
            base_stmt = base_stmt.where(call_attempt_count_subq >= call_attempt_min)
            count_stmt = count_stmt.where(call_attempt_count_subq >= call_attempt_min)
        if call_attempt_max is not None:
            base_stmt = base_stmt.where(call_attempt_count_subq <= call_attempt_max)
            count_stmt = count_stmt.where(call_attempt_count_subq <= call_attempt_max)

        # Date-range filters over the two follow-up timestamps. `next_followup_at`
        # is the rep-scheduled callback time; `call_last_at` is when the prospect
        # was last called. Stored values are UTC-naive (the contacts PUT strips
        # tzinfo), so we normalize incoming bounds to naive UTC before comparing.
        # A `>=`/`<=` against a column also implicitly excludes NULL rows, which
        # is what we want — "has a follow-up in this window" must have a value.
        def _to_naive(value: Optional[datetime]) -> Optional[datetime]:
            # Frontend sends UTC ISO bounds; drop the tzinfo so the comparison
            # stays in the same UTC wall-clock the columns are stored in.
            if value is not None and value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value

        nf_after = _to_naive(next_followup_after)
        nf_before = _to_naive(next_followup_before)
        if nf_after is not None:
            base_stmt = base_stmt.where(Contact.next_followup_at >= nf_after)
            count_stmt = count_stmt.where(Contact.next_followup_at >= nf_after)
        if nf_before is not None:
            base_stmt = base_stmt.where(Contact.next_followup_at <= nf_before)
            count_stmt = count_stmt.where(Contact.next_followup_at <= nf_before)

        cl_after = _to_naive(call_last_after)
        cl_before = _to_naive(call_last_before)
        if cl_after is not None:
            base_stmt = base_stmt.where(Contact.call_last_at >= cl_after)
            count_stmt = count_stmt.where(Contact.call_last_at >= cl_after)
        if cl_before is not None:
            base_stmt = base_stmt.where(Contact.call_last_at <= cl_before)
            count_stmt = count_stmt.where(Contact.call_last_at <= cl_before)

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
        for contact, company_name, assigned_to_name, sdr_name, call_attempt_count, latest_comment, comment_count in rows:
            read = ContactRead.model_validate(contact)
            read.company_name = company_name
            read.assigned_to_name = assigned_to_name
            read.sdr_name = sdr_name
            read.call_attempt_count = int(call_attempt_count or 0)
            read.latest_comment = latest_comment
            read.comment_count = int(comment_count or 0)
            result.append(read)

        await apply_contact_tracking(self.session, result)
        return result, total

    async def delete_all(self) -> None:
        """Delete all contacts and their dependent records. Admin only.

        Mirrors the dependent cleanup in ``delete_many`` so an all-rows purge
        cannot trip a NO ACTION foreign key. ``reminders.contact_id`` is NO
        ACTION (migration 026), so a bare ``DELETE FROM contacts`` raised
        ForeignKeyViolation whenever any reminder existed. outreach steps,
        deal-stakeholder links and angel mappings are removed here too to keep
        the two delete paths consistent; their FKs already cascade, but the
        explicit deletes guard against future ondelete regressions. Outreach
        steps go before their sequences. call_recordings are removed by their
        ON DELETE CASCADE foreign key (migration 072).
        """
        from sqlalchemy import delete as sa_delete

        from app.models.angel import AngelMapping
        from app.models.deal import DealContact
        from app.models.outreach import OutreachStep
        from app.models.reminder import Reminder

        await self.session.execute(sa_delete(OutreachStep))
        await self.session.execute(sa_delete(OutreachSequence))
        await self.session.execute(sa_delete(DealContact))
        await self.session.execute(sa_delete(Reminder))
        await self.session.execute(sa_delete(AngelMapping))
        await self.session.execute(sa_delete(Activity).where(Activity.contact_id.isnot(None)))
        await self.session.execute(sa_delete(Contact))
        await self.session.commit()

    async def delete_with_cascade(self, contact_id: UUID) -> None:
        """Hard-delete one contact and ALL of its FK dependents.

        Delegates to delete_many so single- and bulk-delete share one correct
        cleanup order. The previous implementation only removed outreach
        sequences + activities, so deleting a prospect that was a deal
        stakeholder / had a reminder / had an angel mapping raised IntegrityError.
        """
        await self.delete_many([contact_id])

    async def delete_many(self, contact_ids: list[UUID]) -> int:
        """Hard-delete the given contacts and their FK dependents.

        Returns the count of contacts that actually existed and were removed.
        Dependent order mirrors the admin purge endpoint: activity links are
        nulled (history is kept), then outreach steps/sequences, deal-stakeholder
        links, reminders, and angel mappings are deleted, then the contacts
        themselves. call_recordings are removed automatically by their
        ON DELETE CASCADE foreign key (migration 072). Processed in chunks so a
        very large selection stays within driver parameter limits.
        """
        from sqlalchemy import delete as sa_delete

        from app.models.angel import AngelMapping
        from app.models.deal import DealContact
        from app.models.outreach import OutreachStep
        from app.models.reminder import Reminder

        # De-duplicate, preserve order, drop falsy ids defensively.
        unique_ids = list(dict.fromkeys(cid for cid in contact_ids if cid))
        if not unique_ids:
            return 0

        deleted_total = 0
        chunk_size = 500
        for start in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[start:start + chunk_size]

            existing = (
                await self.session.execute(
                    select(func.count(Contact.id)).where(Contact.id.in_(chunk))
                )
            ).scalar_one()

            # Keep activity history — just detach it from the deleted prospects.
            await self.session.execute(
                Activity.__table__.update()
                .values(contact_id=None)
                .where(Activity.contact_id.in_(chunk))
            )
            seq_ids_subq = select(OutreachSequence.id).where(
                OutreachSequence.contact_id.in_(chunk)
            )
            await self.session.execute(
                sa_delete(OutreachStep).where(OutreachStep.sequence_id.in_(seq_ids_subq))
            )
            await self.session.execute(
                sa_delete(OutreachSequence).where(OutreachSequence.contact_id.in_(chunk))
            )
            await self.session.execute(
                sa_delete(DealContact).where(DealContact.contact_id.in_(chunk))
            )
            await self.session.execute(
                sa_delete(Reminder).where(Reminder.contact_id.in_(chunk))
            )
            await self.session.execute(
                sa_delete(AngelMapping).where(AngelMapping.contact_id.in_(chunk))
            )
            await self.session.execute(
                sa_delete(Contact).where(Contact.id.in_(chunk))
            )
            deleted_total += int(existing or 0)

        await self.session.commit()
        return deleted_total
