"""Bulk account reassignment from an uploaded CSV/XLSX.

Selecting rows in the UI works for a handful of accounts. Re-cutting territory
across a few hundred (the LFG migration was 221) previously meant running a
one-off script against prod, which is neither reviewable nor repeatable. This
module turns that into an upload with a mandatory dry run.

Three rules exist because the earlier script-based migrations got them wrong:

1. **A blank cell means "leave this slot alone", never "unassign".** A previous
   bulk migration treated sheet blanks as clears and silently unassigned 23
   accounts, cascading to their contacts. Clearing a slot now requires the
   literal word ``unassign``.
2. **Rep names resolve strictly** — exact email, or an exact full name that
   matches exactly one active user. No first-name or fuzzy matching, which once
   put 90 of 251 accounts under the wrong SDR.
3. **Every apply is preceded by a dry run over the same file**, and the caller
   sees per-row outcomes before anything is written.

The actual mutation reuses the same helpers as the row-selection bulk endpoint
(`sync_company_sdr_assignment_to_contacts` for SDR, matching contact cascade for
AE), so an uploaded reassignment and a clicked one behave identically — including
the SDR watermark that resets outreach counters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.models.contact import Contact
from app.models.user import User
from app.services.account_sourcing import append_company_activity_log
from app.services.sdr_reassignment import sync_company_sdr_assignment_to_contacts

UNASSIGN_TOKENS = {"unassign", "unassigned", "none", "remove", "clear"}

# Header aliases. Uploaded sheets come from ops spreadsheets, not a fixed
# template, so we accept the spellings people actually use.
ACCOUNT_KEYS = ("account", "account_name", "company", "company_name", "name")
DOMAIN_KEYS = ("domain", "website", "company_domain", "url", "company_website")
AE_KEYS = ("ae", "ae_name", "account_executive", "assigned_ae", "owner", "assigned_rep")
AE_EMAIL_KEYS = ("ae_email", "account_executive_email", "assigned_ae_email", "owner_email")
SDR_KEYS = ("sdr", "sdr_name", "assigned_sdr")
SDR_EMAIL_KEYS = ("sdr_email", "assigned_sdr_email")

RowStatus = Literal["ok", "no_change", "not_found", "ambiguous", "unknown_rep", "no_identifier"]


def normalize_domain(value: str | None) -> str:
    """Reduce a domain or URL to a bare, comparable host."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    for prefix in ("https://", "http://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    raw = raw.split("/", 1)[0].split("?", 1)[0].strip()
    if raw.startswith("www."):
        raw = raw[4:]
    return raw.strip(". ")


def normalize_name(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _first_present(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


@dataclass
class SlotChange:
    """One rep slot (AE or SDR) as the file asks for it."""

    requested: bool = False          # the cell had content
    unassign: bool = False           # the cell said "unassign"
    user: Optional[User] = None      # resolved target
    raw: str = ""


@dataclass
class PlannedRow:
    row_number: int
    identifier: str
    status: RowStatus
    message: str = ""
    company_id: Optional[UUID] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    current_ae: Optional[str] = None
    current_sdr: Optional[str] = None
    new_ae: Optional[str] = None
    new_sdr: Optional[str] = None
    ae: SlotChange = field(default_factory=SlotChange)
    sdr: SlotChange = field(default_factory=SlotChange)
    # Whether the slot actually MOVES, not merely whether the cell was filled.
    # A file that restates the current owner must not render as a change.
    ae_moves: bool = False
    sdr_moves: bool = False

    @property
    def applies(self) -> bool:
        return self.status == "ok"


class RepResolver:
    """Strict uploaded-cell → active user resolution.

    Email wins. Otherwise an exact full-name match, and ONLY when it is
    unambiguous. Nothing else — a first-name fallback previously assigned
    accounts to an unrelated rep who shared a first name.
    """

    def __init__(self, users: list[User]):
        self._by_email: dict[str, User] = {}
        self._by_name: dict[str, list[User]] = {}
        for user in users:
            email = (user.email or "").strip().lower()
            if email:
                self._by_email[email] = user
            name = normalize_name(user.name)
            if name:
                self._by_name.setdefault(name, []).append(user)

    def resolve(self, cell: str) -> tuple[Optional[User], Optional[str]]:
        """Return (user, error). Both None means the cell was empty."""
        value = (cell or "").strip()
        if not value:
            return None, None
        if "@" in value:
            found = self._by_email.get(value.lower())
            if found:
                return found, None
            return None, f"No active user with email {value}"
        matches = self._by_name.get(normalize_name(value)) or []
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, f"{value} matches {len(matches)} active users — use their email instead"
        return None, f"No active user named {value} — use their exact full name or email"


class CompanyIndex:
    """Domain-first, name-second account matching."""

    def __init__(self, companies: list[Company]):
        self._by_domain: dict[str, list[Company]] = {}
        self._by_name: dict[str, list[Company]] = {}
        for company in companies:
            domain = normalize_domain(company.domain)
            if domain:
                self._by_domain.setdefault(domain, []).append(company)
            name = normalize_name(company.name)
            if name:
                self._by_name.setdefault(name, []).append(company)

    def match(self, domain: str, name: str) -> tuple[Optional[Company], Optional[str]]:
        """Domain is authoritative; names are only used when unambiguous."""
        key = normalize_domain(domain)
        if key:
            hits = self._by_domain.get(key) or []
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                return None, f"{key} matches {len(hits)} accounts"
        key = normalize_name(name)
        if key:
            hits = self._by_name.get(key) or []
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                return None, f"{name} matches {len(hits)} accounts — add a domain column"
        return None, None


def _slot_from_cell(cell: str, resolver: RepResolver) -> tuple[SlotChange, Optional[str]]:
    value = (cell or "").strip()
    if not value:
        return SlotChange(), None
    if value.lower() in UNASSIGN_TOKENS:
        return SlotChange(requested=True, unassign=True, raw=value), None
    user, error = resolver.resolve(value)
    if error:
        return SlotChange(requested=True, raw=value), error
    return SlotChange(requested=True, user=user, raw=value), None


async def plan_assignment_upload(
    session: AsyncSession, rows: list[dict[str, str]]
) -> list[PlannedRow]:
    """Resolve every uploaded row against the DB without writing anything."""
    users = (
        await session.execute(select(User).where(User.is_active == True))  # noqa: E712
    ).scalars().all()
    companies = (await session.execute(select(Company))).scalars().all()
    resolver = RepResolver(list(users))
    index = CompanyIndex(list(companies))
    user_names = {u.id: u.name for u in users}

    planned: list[PlannedRow] = []
    for offset, row in enumerate(rows):
        # Position among the rows the parser kept, NOT the spreadsheet line. The
        # parser drops blank rows before we see them, so a sheet-line number
        # would silently point at the wrong row once a file has a gap in it.
        # The account name is the reliable identifier and is always returned.
        number = offset + 1
        domain_cell = _first_present(row, DOMAIN_KEYS)
        name_cell = _first_present(row, ACCOUNT_KEYS)
        identifier = name_cell or domain_cell

        if not identifier:
            planned.append(
                PlannedRow(
                    row_number=number,
                    identifier="",
                    status="no_identifier",
                    message="Row has no account name or domain",
                )
            )
            continue

        company, match_error = index.match(domain_cell, name_cell)
        if not company:
            planned.append(
                PlannedRow(
                    row_number=number,
                    identifier=identifier,
                    status="ambiguous" if match_error else "not_found",
                    message=match_error or "No account in the CRM matches this name or domain",
                )
            )
            continue

        ae_cell = _first_present(row, AE_EMAIL_KEYS) or _first_present(row, AE_KEYS)
        sdr_cell = _first_present(row, SDR_EMAIL_KEYS) or _first_present(row, SDR_KEYS)
        ae_slot, ae_error = _slot_from_cell(ae_cell, resolver)
        sdr_slot, sdr_error = _slot_from_cell(sdr_cell, resolver)

        entry = PlannedRow(
            row_number=number,
            identifier=identifier,
            status="ok",
            company_id=company.id,
            company_name=company.name,
            company_domain=company.domain,
            current_ae=user_names.get(company.assigned_to_id) if company.assigned_to_id else None,
            current_sdr=user_names.get(company.sdr_id) if company.sdr_id else None,
            ae=ae_slot,
            sdr=sdr_slot,
        )

        if ae_error or sdr_error:
            entry.status = "unknown_rep"
            entry.message = "; ".join(m for m in (ae_error, sdr_error) if m)
            planned.append(entry)
            continue

        # A blank cell leaves the slot untouched — it is never a clear.
        entry.new_ae = (
            None if ae_slot.unassign else (ae_slot.user.name if ae_slot.user else entry.current_ae)
        )
        entry.new_sdr = (
            None if sdr_slot.unassign else (sdr_slot.user.name if sdr_slot.user else entry.current_sdr)
        )

        entry.ae_moves = ae_moves = ae_slot.requested and (
            (None if ae_slot.unassign else ae_slot.user.id) != company.assigned_to_id
        )
        entry.sdr_moves = sdr_moves = sdr_slot.requested and (
            (None if sdr_slot.unassign else sdr_slot.user.id) != company.sdr_id
        )
        if not (ae_moves or sdr_moves):
            entry.status = "no_change"
            entry.message = (
                "Already assigned this way"
                if (ae_slot.requested or sdr_slot.requested)
                else "No AE or SDR column filled in"
            )
        planned.append(entry)

    return planned


async def apply_assignment_plan(
    session: AsyncSession, planned: list[PlannedRow], *, actor: User
) -> dict[str, int]:
    """Write the rows the plan marked applicable. Caller commits."""
    now = datetime.utcnow()
    ae_changed = 0
    sdr_changed = 0
    # A set, not a counter: when a row moves BOTH the AE and the SDR, the two
    # cascades walk the same contacts and a running total double-counts them.
    touched_contact_ids: set[UUID] = set()

    for entry in planned:
        if not entry.applies or entry.company_id is None:
            continue
        company = (
            await session.execute(select(Company).where(Company.id == entry.company_id))
        ).scalar_one_or_none()
        if company is None:
            continue
        row_ae_changed = False
        row_sdr_changed = False

        if entry.ae.requested:
            previous_ae_id = company.assigned_to_id
            target = None if entry.ae.unassign else entry.ae.user
            if (target.id if target else None) != previous_ae_id:
                company.assigned_to_id = target.id if target else None
                company.assigned_rep = target.name if target else None
                company.assigned_rep_name = target.name if target else None
                company.assigned_rep_email = target.email if target else None
                # Same cascade rule the row-selection endpoint uses: only carry
                # contacts that were following the account's previous AE, so a
                # deliberate per-contact override is never overwritten.
                contacts = (
                    await session.execute(select(Contact).where(Contact.company_id == company.id))
                ).scalars().all()
                for contact in contacts:
                    if contact.assigned_to_id not in (None, previous_ae_id):
                        continue
                    contact.assigned_to_id = company.assigned_to_id
                    contact.assigned_rep_email = company.assigned_rep_email
                    contact.updated_at = now
                    session.add(contact)
                    touched_contact_ids.add(contact.id)
                ae_changed += 1
                row_ae_changed = True

        if entry.sdr.requested:
            previous_sdr_id = company.sdr_id
            target = None if entry.sdr.unassign else entry.sdr.user
            if (target.id if target else None) != previous_sdr_id:
                company.sdr_id = target.id if target else None
                company.sdr_name = target.name if target else None
                company.sdr_email = target.email if target else None
                # Carries the reassignment watermark that resets outreach
                # counters. Reused rather than reimplemented so an uploaded
                # reassignment resets exactly what a clicked one resets.
                # `moved` is the contacts that actually changed hands (deliberate
                # per-contact overrides are reported in kept_divergent).
                cascade = await sync_company_sdr_assignment_to_contacts(
                    session, company, previous_sdr_id
                )
                touched_contact_ids.update(contact.id for contact in cascade.moved)
                sdr_changed += 1
                row_sdr_changed = True

        if row_ae_changed or row_sdr_changed:
            moved = []
            if row_ae_changed:
                moved.append(f"AE → {entry.new_ae or 'Unassigned'}")
            if row_sdr_changed:
                moved.append(f"SDR → {entry.new_sdr or 'Unassigned'}")
            append_company_activity_log(
                company,
                action="bulk_reassignment_upload",
                actor_name=actor.name,
                actor_email=actor.email,
                message="; ".join(moved),
                metadata={"source": "assignment_upload", "row_number": entry.row_number},
            )

        company.updated_at = now
        session.add(company)

    return {
        "ae_changed": ae_changed,
        "sdr_changed": sdr_changed,
        "contacts_touched": len(touched_contact_ids),
    }


def summarize(planned: list[PlannedRow]) -> dict[str, int]:
    counts = {
        "total": len(planned),
        "will_change": 0,
        "no_change": 0,
        "not_found": 0,
        "ambiguous": 0,
        "unknown_rep": 0,
        "no_identifier": 0,
    }
    for entry in planned:
        if entry.status == "ok":
            counts["will_change"] += 1
        else:
            counts[entry.status] += 1
    return counts
