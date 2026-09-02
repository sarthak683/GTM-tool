"""
Company repository.

Adds domain-based lookup and a cascade-delete method that respects FK order.
All raw SQL logic that previously lived in app/routes/companies.py lives here.
"""
import re
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, cast as sa_cast, func, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Common corporate suffixes that should not block dedupe.
# Stripped from the END of names during normalization, longest-first so
# "Pvt Ltd" wins over "Ltd" alone.
_NAME_SUFFIXES = (
    "pvt ltd", "private limited", "p ltd",
    "technologies", "technology", "solutions", "systems", "labs",
    "software", "services", "consulting", "group", "holdings",
    "global", "international", "corporation", "incorporated",
    "company", "limited", "corp", "inc", "llc", "ltd", "gmbh", "ag", "sa", "bv",
)
# Common public TLDs that show up when someone pastes a domain into the name field.
_NAME_TLDS = (".com", ".io", ".ai", ".co", ".org", ".net", ".in", ".uk", ".eu", ".de")


def _normalize_company_name(name: str) -> str:
    """Aggressive normalization for dedupe: strip TLDs, suffixes, punctuation."""
    s = (name or "").strip().lower().rstrip(" .,")
    if not s:
        return ""
    # Strip a trailing TLD-as-suffix BEFORE collapsing punctuation, so
    # "zywave.com" -> "zywave" rather than getting eaten as " com".
    for tld in _NAME_TLDS:
        if s.endswith(tld):
            s = s[: -len(tld)]
            break
    # Collapse remaining punctuation so "Pvt. Ltd" matches the "pvt ltd" suffix.
    s = re.sub(r"[.,]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    # Strip suffixes iteratively in case of compounds like "Foo Solutions Inc"
    changed = True
    while changed:
        changed = False
        s = s.rstrip(" .,")
        for suf in _NAME_SUFFIXES:
            # Match suffix optionally followed by punctuation (handles "Inc.", "Pvt.")
            if s.endswith(" " + suf) or s.endswith(" " + suf + "."):
                idx = s.rfind(" " + suf)
                s = s[:idx].rstrip(" .,")
                changed = True
                break
    return re.sub(r"[^a-z0-9]+", "", s)

from app.models.activity import Activity
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.company import Company, INACTIVE_ACCOUNT_STATUSES
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.outreach import OutreachSequence
from app.models.reminder import Reminder
from app.models.task import Task, TaskComment
from app.models.user import User
from app.repositories.base import BaseRepository


def _user_is_admin(user: User) -> bool:
    """Support real User rows and lightweight authenticated-user stand-ins."""
    explicit = getattr(user, "is_admin", None)
    if explicit is not None:
        return bool(explicit)
    return str(getattr(user, "role", "")).lower() == "admin"


def company_visibility_filter(user_id: UUID, is_admin: bool, include_disabled: bool = False):
    """SQLAlchemy predicate enforcing company (account) visibility for ONE user.

    SINGLE SOURCE OF TRUTH for the account-level visibility rule — reuse it on
    EVERY company-browse surface so access can never diverge between endpoints.

    - Admins (``is_admin``) see ALL companies, including disabled ones, so they
      can manage/reverse them. Returns ``true()`` — a no-op in ``.where()``.
    - A non-admin sees a company ONLY if they own it in either slot
      (``assigned_to_id`` = AE, or ``sdr_id`` = SDR). Unassigned accounts are
      NOT visible to non-admins.
    - Disabled accounts (``INACTIVE_ACCOUNT_STATUSES`` — not_a_fit/dnd) are
      hidden from default lists, but a caller may pass ``include_disabled=True``
      when the request EXPLICITLY filters for a disabled status (an owner
      reviewing their parked accounts is legitimate — hiding them outright made
      re-enabling impossible for non-admins). Single-object guards
      (``_can_see_company``) key on ownership only, for the same reason.
    """
    # Soft-deleted accounts are gone from every browse surface for everyone —
    # admins included. They are reachable only through the trash view
    # (GET /api/v1/companies/trash) and restored via
    # POST /api/v1/companies/{id}/restore, which deliberately do NOT go
    # through this filter.
    live = Company.deleted_at.is_(None)
    if is_admin:
        return live
    owns = or_(
        Company.assigned_to_id == user_id,
        Company.sdr_id == user_id,
    )
    if include_disabled:
        return and_(live, owns)
    return and_(
        live,
        owns,
        or_(
            Company.account_status.is_(None),
            Company.account_status.not_in(INACTIVE_ACCOUNT_STATUSES),
        ),
    )


def can_see_company(company: Company, user: User) -> bool:
    """Python mirror of ``company_visibility_filter`` for single-object guards.

    Admins see every company; a non-admin sees a company they own (AE or SDR).
    Ownership is the ONLY gate here — deliberately NOT the disabled-status
    check the list filter applies: an owner must be able to OPEN their parked
    (not_a_fit/dnd) account to review or re-enable it. Lists hide parked
    accounts by default; direct access never dead-ends. Use on single-company
    detail/update routes to 404 a company the caller can't see (so existence
    isn't leaked).
    """
    if company.deleted_at is not None:
        # Soft-deleted: gone from every detail/update route for everyone. The
        # row is reachable only through GET /companies/trash (admin) and
        # GET /companies/{id}/tombstone (which says "deleted" or "merged into
        # X" without exposing the record).
        return False
    if _user_is_admin(user):
        return True
    return company.assigned_to_id == user.id or company.sdr_id == user.id


def _normalize_domain(domain: str) -> str:
    """Lower-cased, whitespace- and scheme-stripped domain key.

    Matches how callers persist domains (e.g. the upload paths lowercase and
    strip ``https://``/``www.``) so the dedup key the unique index is built on
    (``lower(domain)``) lines up with what we compare against here.
    """
    s = (domain or "").strip().lower()
    if not s:
        return ""
    s = s.replace("https://", "").replace("http://", "")
    if s.startswith("www."):
        s = s[4:]
    return s.split("/")[0].strip()


async def get_or_create_company_by_domain(
    session: AsyncSession,
    domain: str,
    defaults: Optional[dict] = None,
) -> tuple[Company, bool]:
    """Case-insensitive dedup on ``domain``. Returns ``(company, created)``.

    Mirrors ``app.repositories.contact.get_or_create_contact_by_email``: the
    single funnel for "create a company keyed by its domain" so no path can mint
    a duplicate-domain row. Safe under the unique index on ``lower(domain)``
    (migration 083): if a concurrent writer (or a path that skipped the
    pre-lookup) already inserted the same domain, the nested-savepoint insert
    hits the constraint, we roll back ONLY that savepoint (not the caller's
    transaction) and re-fetch the winning row. Callers get the existing company
    instead of an IntegrityError.

    Note: companies carry a BEFORE INSERT trigger requiring a
    ``sourcing_batch_id`` — callers that create must pass one via ``defaults``.
    A blank/whitespace domain is rejected (a company must have a real domain
    key); callers without a domain should build the row themselves.
    """
    normalized = _normalize_domain(domain)
    if not normalized:
        raise ValueError("get_or_create_company_by_domain requires a non-empty domain")

    fields = {k: v for k, v in (defaults or {}).items() if k != "domain"}

    async def _fetch() -> Optional[Company]:
        # Alias-aware + live-only, mirroring CompanyRepository.get_by_domain:
        # an alias hit must return the existing account, not mint a duplicate
        # under a domain the account already legitimately owns.
        res = await session.execute(
            select(Company)
            .where(
                Company.deleted_at.is_(None),
                or_(
                    func.lower(Company.domain) == normalized,
                    func.coalesce(
                        Company.additional_domains, sa_cast("[]", JSONB)
                    ).op("@>")(func.jsonb_build_array(normalized)),
                ),
            )
            .order_by((func.lower(Company.domain) == normalized).desc())
            .limit(1)
        )
        return res.scalars().first()

    existing = await _fetch()
    if existing is not None:
        return existing, False

    company = Company(domain=normalized, **fields)
    try:
        async with session.begin_nested():
            session.add(company)
            await session.flush()
        return company, True
    except IntegrityError:
        existing = await _fetch()
        if existing is not None:
            return existing, False
        raise


class CompanyRepository(BaseRepository[Company]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Company, session)

    @staticmethod
    def visible_to(user: User, *, include_disabled: bool = False):
        """Base select for ANY user-facing company query.

        If you reach for a bare select(Company) in an endpoint, that is the
        bug — see unscoped_for_background_job for the deliberate exception.
        """
        return select(Company).where(
            company_visibility_filter(
                user.id,
                _user_is_admin(user),
                include_disabled=include_disabled,
            )
        )

    @staticmethod
    def unscoped_for_background_job(reason: str):
        """Every company, no ownership gate. Background/system work only."""
        if not reason.strip():
            raise ValueError("An unscoped company query requires a reviewable reason")
        return select(Company)

    @staticmethod
    def _slugify_alias(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
        return slug or "account"

    async def generate_unique_email_cc_alias(self, name: str, *, exclude_id: Optional[UUID] = None) -> str:
        """Mirrors DealRepository.generate_unique_email_cc_alias, keyed on Company."""
        base = self._slugify_alias(name)
        candidate = base
        suffix = 2

        while True:
            stmt = select(Company.id).where(Company.email_cc_alias == candidate)
            if exclude_id:
                stmt = stmt.where(Company.id != exclude_id)
            existing = (await self.session.execute(stmt)).first()
            if not existing:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    async def ensure_email_cc_alias(self, company: Company) -> Company:
        """Lazily generate + persist a Zippy ID (email_cc_alias) if missing.

        Called from GET /api/v1/companies/{id} rather than at row creation,
        since almost every company already existed before this field was
        added. A no-op (no extra write) once the alias is set.
        """
        if company.email_cc_alias:
            return company
        company.email_cc_alias = await self.generate_unique_email_cc_alias(company.name or "account")
        return await self.save(company)

    async def get_by_domain(self, domain: str) -> Optional[Company]:
        # lower() on both sides: the unique index is on lower(domain), so an
        # exact-case miss here followed by an insert dies on IntegrityError.
        # Alias-aware: a domain that is another live account's alias
        # (additional_domains) resolves to that account — rebrands keep
        # matching (ceridian.com finds the Dayforce account). Primary-domain
        # match wins over an alias match. Soft-deleted rows never match.
        normalized = (domain or "").strip().lower()
        result = await self.session.execute(
            select(Company)
            .where(
                Company.deleted_at.is_(None),
                or_(
                    func.lower(Company.domain) == normalized,
                    func.coalesce(
                        Company.additional_domains, sa_cast("[]", JSONB)
                    ).op("@>")(func.jsonb_build_array(normalized)),
                ),
            )
            .order_by((func.lower(Company.domain) == normalized).desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_by_name(self, name: str) -> Optional[Company]:
        """Case-insensitive, whitespace-trimmed name lookup."""
        result = await self.session.execute(
            select(Company).where(func.lower(func.trim(Company.name)) == name.strip().lower())
        )
        return result.scalars().first()

    async def get_by_normalized_name(
        self,
        name: str,
        *,
        incoming_domain: Optional[str] = None,
        visible_to: Optional[User] = None,
    ) -> Optional[Company]:
        """
        Looser fallback used by importers to prevent placeholder-domain duplicates.
        Compares names with corporate suffixes / TLDs / punctuation stripped, so
        "zywave.com" matches "zywave", "OpenGov" matches "OpenGov Inc.", etc.
        Only call AFTER get_by_domain and get_by_name miss.

        Two guards keep fuzziness from becoming wrong-account links (both are
        confirmed prod failure modes of the old first-match-wins version):

        - ``incoming_domain``: when the caller KNOWS the record's domain, a
          candidate whose real (non-placeholder) domain differs is a DIFFERENT
          company no matter how similar the normalized names are — skip it.
        - Ambiguity: if more than one distinct company normalizes to the same
          key ("Apex Solutions" / "Apex Systems" / "Apex" all normalize to
          "apex"), there is no safe pick — return None and let the caller
          create/leave-unlinked rather than guess.
        """
        target = _normalize_company_name(name)
        if not target:
            return None
        normalized_incoming = _normalize_domain(incoming_domain or "")
        # Pull a small candidate set by first 3 normalized chars to keep it cheap;
        # then normalize in Python for the equality check.
        prefix = target[:3]
        stmt = (
            self.visible_to(visible_to, include_disabled=True)
            if visible_to is not None
            else self.unscoped_for_background_job(
                "company import dedupe has no requesting user"
            )
        )
        result = await self.session.execute(
            stmt.where(func.lower(Company.name).like(f"{prefix}%"))
        )
        matches: list[Company] = []
        for candidate in result.scalars().all():
            if _normalize_company_name(candidate.name or "") != target:
                continue
            candidate_domain = _normalize_domain(candidate.domain or "")
            if (
                normalized_incoming
                and candidate_domain
                and not candidate_domain.endswith(".unknown")
                and candidate_domain != normalized_incoming
            ):
                continue
            matches.append(candidate)
        if len(matches) == 1:
            return matches[0]
        return None

    async def delete_with_cascade(self, company_id: UUID) -> None:
        """
        Delete a company and every dependent record in FK dependency order:
          1. outreach_sequences (FK on company_id + contact_id)
          2. activities (via contact_ids + deal_ids)
          3. company_stage_milestones
          3. deals
          4. contacts (+ any remaining outreach_sequences on contact_id)
          5. company  (signals cascade via DB ON DELETE CASCADE;
                       meetings use ON DELETE SET NULL so they survive)
        """
        # 1. outreach_sequences owned by this company
        seqs = await self.session.execute(
            select(OutreachSequence).where(OutreachSequence.company_id == company_id)
        )
        for seq in seqs.scalars().all():
            await self.session.delete(seq)

        # collect contacts + deals for cascade
        contacts = (
            await self.session.execute(
                select(Contact).where(Contact.company_id == company_id)
            )
        ).scalars().all()
        contact_ids = [c.id for c in contacts]

        deals = (
            await self.session.execute(
                select(Deal).where(Deal.company_id == company_id)
            )
        ).scalars().all()
        deal_ids = [d.id for d in deals]

        # 2. activities
        if contact_ids or deal_ids:
            acts_stmt = select(Activity)
            if contact_ids and deal_ids:
                acts_stmt = acts_stmt.where(
                    or_(
                        Activity.contact_id.in_(contact_ids),
                        Activity.deal_id.in_(deal_ids),
                    )
                )
            elif contact_ids:
                acts_stmt = acts_stmt.where(Activity.contact_id.in_(contact_ids))
            else:
                acts_stmt = acts_stmt.where(Activity.deal_id.in_(deal_ids))

            for act in (await self.session.execute(acts_stmt)).scalars().all():
                await self.session.delete(act)

        # 3. company stage milestones
        milestones = await self.session.execute(
            select(CompanyStageMilestone).where(CompanyStageMilestone.company_id == company_id)
        )
        for milestone in milestones.scalars().all():
            await self.session.delete(milestone)

        # 3b. reminders — their contact_id/company_id FKs are NO ACTION, so
        # leaving them in place aborts the whole cascade with a
        # ForeignKeyViolation (ContactRepository.delete_many learned this the
        # hard way; this path never did).
        reminder_stmt = select(Reminder).where(Reminder.company_id == company_id)
        if contact_ids:
            reminder_stmt = select(Reminder).where(
                or_(
                    Reminder.company_id == company_id,
                    Reminder.contact_id.in_(contact_ids),
                )
            )
        for reminder in (await self.session.execute(reminder_stmt)).scalars().all():
            await self.session.delete(reminder)

        # 3c. tasks — entity_id is polymorphic (no FK possible), so deletes
        # must clean them explicitly or they orphan: prod accumulated 1,925
        # ghost tasks (13 still open) pointing at deleted deals/contacts.
        task_filters = [and_(Task.entity_type == "company", Task.entity_id == company_id)]
        if contact_ids:
            task_filters.append(and_(Task.entity_type == "contact", Task.entity_id.in_(contact_ids)))
        if deal_ids:
            task_filters.append(and_(Task.entity_type == "deal", Task.entity_id.in_(deal_ids)))
        task_rows = (
            await self.session.execute(select(Task).where(or_(*task_filters)))
        ).scalars().all()
        if task_rows:
            task_row_ids = [t.id for t in task_rows]
            for comment in (
                await self.session.execute(
                    select(TaskComment).where(TaskComment.task_id.in_(task_row_ids))
                )
            ).scalars().all():
                await self.session.delete(comment)
            for task_row in task_rows:
                await self.session.delete(task_row)

        # 4. deals
        for deal in deals:
            await self.session.delete(deal)

        # 5. contacts (+ leftover outreach_sequences on contact_id)
        for contact in contacts:
            extra = (
                await self.session.execute(
                    select(OutreachSequence).where(
                        OutreachSequence.contact_id == contact.id
                    )
                )
            ).scalars().all()
            for seq in extra:
                await self.session.delete(seq)
            await self.session.delete(contact)

        # 6. company
        company = await self.get(company_id)
        if company:
            await self.session.delete(company)

        await self.session.commit()
