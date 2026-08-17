"""Company lifecycle operations: soft-delete, restore, and merge.

Both exist to replace what used to be manual SQL (or a destructive cascade):

- ``soft_delete_company``: the account leaves every current-state surface via
  ``deleted_at``; nothing referencing it is destroyed, so historical metrics
  never rewrite. Its live deals are soft-deleted with it and their open system
  tasks dismissed (a task pointing at an invisible deal is a ghost nag).

- ``restore_company``: the way back out. Soft-delete used to be a one-way door
  (the only undo was a manual ``UPDATE ... SET deleted_at = NULL``); this
  reverses the parts that are safely reversible and documents, in its own
  docstring, exactly what stays put.

- ``merge_companies``: the IRIS/Dayforce-class fix. Everything the loser owns
  moves to the winner, the loser's domain (plus its aliases) become winner
  alias domains — so every domain matcher keeps resolving old addresses to the
  surviving account — the loser is soft-deleted with an audit trail on both
  rows, and ``merged_into_id`` records where it went.

Callers commit; these functions only stage changes on the session.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.task import Task


def normalized_alias_domains(company: Company) -> list[str]:
    """The company's alias domains as a clean lowercase list."""
    raw = company.additional_domains if isinstance(company.additional_domains, list) else []
    out: list[str] = []
    for item in raw:
        domain = str(item or "").strip().lower()
        if domain and domain not in out:
            out.append(domain)
    return out


def company_domain_family(company: Company) -> set[str]:
    """Primary + alias domains, normalized — the full set of domains that
    legitimately belong to this account."""
    family = set(normalized_alias_domains(company))
    primary = (company.domain or "").strip().lower()
    if primary:
        family.add(primary)
    return family


async def validate_alias_domains(
    session: AsyncSession, company: Company, raw_domains: list[str] | None
) -> list[str]:
    """Normalize an alias-domain list and enforce cross-account uniqueness.

    Rules: lowercase/trim, drop empties + duplicates + the company's own
    primary domain + placeholder ``*.unknown`` values; every remaining domain
    must not be another LIVE company's primary domain or alias. Raises
    ValueError naming the collision so the API can 422 with a useful message.
    """
    from app.repositories.company import _normalize_domain

    normalized: list[str] = []
    primary = _normalize_domain(company.domain or "")
    for raw in raw_domains or []:
        domain = _normalize_domain(str(raw or ""))
        if not domain or domain == primary or domain.endswith(".unknown"):
            continue
        if domain not in normalized:
            normalized.append(domain)

    for domain in normalized:
        from sqlalchemy import func, cast
        from sqlalchemy.dialects.postgresql import JSONB

        collision = (
            await session.execute(
                select(Company.name)
                .where(
                    Company.id != company.id,
                    Company.deleted_at.is_(None),
                    or_(
                        func.lower(Company.domain) == domain,
                        # jsonb_build_array binds the domain as a parameter —
                        # never interpolate user input into a JSON literal.
                        func.coalesce(Company.additional_domains, cast("[]", JSONB)).op("@>")(
                            func.jsonb_build_array(domain)
                        ),
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if collision:
            raise ValueError(
                f"Domain '{domain}' already belongs to account '{collision}'. "
                "Merge the accounts instead of aliasing across them."
            )
    return normalized


async def soft_delete_company(session: AsyncSession, company: Company) -> dict:
    """Stamp the company (and its live deals) deleted; dismiss their open tasks.

    Contacts keep their rows AND their company_id — the link is audit trail
    ("this person was at that account"), and the prospect list already hides
    contacts of deleted accounts unconditionally. Open tasks pointing at the
    company or its deals are dismissed, not deleted (comment history stays).
    """
    now = datetime.utcnow()
    company.deleted_at = now
    company.updated_at = now
    session.add(company)

    deal_ids = [
        row
        for row in (
            await session.execute(
                select(Deal.id).where(Deal.company_id == company.id, Deal.deleted_at.is_(None))
            )
        ).scalars().all()
    ]
    if deal_ids:
        await session.execute(
            sa_update(Deal)
            .where(Deal.id.in_(deal_ids))
            .values(deleted_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )

    task_filter = or_(
        (Task.entity_type == "company") & (Task.entity_id == company.id),
        (Task.entity_type == "deal") & (Task.entity_id.in_(deal_ids)) if deal_ids else False,
    )
    dismissed = await session.execute(
        sa_update(Task)
        .where(task_filter, Task.status == "open")
        .values(status="dismissed", updated_at=now)
        .execution_options(synchronize_session=False)
    )

    return {"deals_soft_deleted": len(deal_ids), "tasks_dismissed": int(dismissed.rowcount or 0)}


# Winner fields that inherit from the loser when the winner's slot is EMPTY.
# name/domain are deliberately absent: the winner keeps its identity, and the
# loser's domain becomes an alias instead.
_MERGE_FILL_FIELDS = (
    "industry", "vertical", "employee_count", "arr_estimate", "funding_stage",
    "region", "headquarters", "description", "account_thesis", "why_now",
    "beacon_angle", "recommended_outreach_lane", "ownership_stage",
    "priority_tag", "pe_investors", "vc_investors", "strategic_investors",
    "icp_score", "icp_tier", "assigned_to_id", "assigned_rep",
    "assigned_rep_email", "assigned_rep_name", "sdr_id", "sdr_email",
    "sdr_name", "account_status", "outreach_status", "disposition",
)

# Tables carrying a plain company_id column that simply re-points to the
# winner. (Activities and deal_stage_history follow their contact/deal rows
# automatically; tasks are polymorphic and handled separately.)
_COMPANY_ID_TABLES = (
    "contacts", "deals", "meetings", "outreach_sequences",
    "company_stage_milestones", "reminders", "recotap_accounts", "signals",
)


async def merge_companies(session: AsyncSession, winner: Company, loser: Company) -> dict:
    """Move everything from ``loser`` to ``winner``; loser becomes a
    soft-deleted alias of the winner.

    Rules:
      - every company_id reference re-points to the winner (contacts, deals —
        including already-soft-deleted deals, so their history stays reachable
        under the surviving account — meetings, sequences, milestones,
        reminders, recotap rows, signals; polymorphic company-tasks re-point
        too);
      - the loser's domain and aliases join the winner's alias list (matching
        and the mismatch badge keep working for old addresses);
      - empty winner fields inherit the loser's values (never overwrite);
      - the loser is soft-deleted, freeing its domain slot in the partial
        unique index while keeping the row for audit.

    Caller validates permissions and non-identity and commits.
    """
    if winner.id == loser.id:
        raise ValueError("Cannot merge a company into itself")
    if winner.deleted_at is not None:
        raise ValueError("Cannot merge into a deleted company")
    if loser.deleted_at is not None:
        raise ValueError("Company is already deleted/merged")

    now = datetime.utcnow()
    moved: dict[str, int] = {}

    for table in _COMPANY_ID_TABLES:
        result = await session.execute(
            sa_update(_TABLE_MODELS[table])
            .where(_TABLE_MODELS[table].company_id == loser.id)
            .values(company_id=winner.id)
            .execution_options(synchronize_session=False)
        )
        moved[table] = int(result.rowcount or 0)

    tasks_moved = await session.execute(
        sa_update(Task)
        .where(Task.entity_type == "company", Task.entity_id == loser.id)
        .values(entity_id=winner.id, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    moved["tasks"] = int(tasks_moved.rowcount or 0)

    # Domain family: loser primary + loser aliases -> winner aliases.
    winner_family = company_domain_family(winner)
    new_aliases = normalized_alias_domains(winner)
    for domain in sorted(company_domain_family(loser)):
        if domain and domain not in winner_family and not domain.endswith(".unknown"):
            new_aliases.append(domain)
            winner_family.add(domain)
    winner.additional_domains = new_aliases or None

    for field in _MERGE_FILL_FIELDS:
        if getattr(winner, field, None) in (None, "") and getattr(loser, field, None) not in (None, ""):
            setattr(winner, field, getattr(loser, field))

    winner.updated_at = now
    session.add(winner)

    loser.deleted_at = now
    # Back-pointer so a link to the merged-away account can say WHERE it went
    # (GET /companies/{id}/tombstone) instead of 404-ing into silence.
    loser.merged_into_id = winner.id
    loser.updated_at = now
    session.add(loser)

    return moved


async def restore_company(session: AsyncSession, company: Company) -> dict:
    """Undo ``soft_delete_company`` as far as it is safely undoable.

    WHAT COMES BACK
      - The company row itself (``deleted_at`` -> NULL), which is most of the
        restore: contacts kept their rows AND their ``company_id`` through the
        delete, and prospect/reminder visibility is QUERY-LEVEL gating on the
        parent account (see ``ContactRepository.active_account_contact_filter``
        and ``company_visibility_filter``). No per-contact state was rewritten,
        so clearing ``deleted_at`` un-hides every prospect for free.
      - The deals that this delete took down with it. ``soft_delete_company``
        stamps company and deals with the SAME ``datetime.utcnow()`` value, so
        ``Deal.deleted_at == company.deleted_at`` identifies exactly the deals
        that died WITH the account — and leaves a deal an admin deleted on its
        own, before or after, still deleted. Microsecond precision makes a
        false match effectively impossible.

    WHAT DOES NOT COME BACK (deliberate)
      - Tasks dismissed by the delete. They stay dismissed. The rows are intact
        and their comment history is readable, but re-opening them would
        resurrect nags whose due dates are now long past — noise, not recovery.
        Re-open individually from the task center if one still matters.
      - ``merged_into_id`` is cleared: restoring a merged loser makes it a live
        standalone account again, so the "this is now X" pointer would be a
        lie. NOTE the data is NOT un-merged — the rows that moved to the winner
        stay on the winner, and the loser's domains stay in the winner's alias
        list. Restoring a merge loser gives you back an EMPTY shell account,
        which is why the API surfaces the merge target in the confirm step.
        Its old domain also stays on the winner's alias list, so the restored
        account and the winner both claim it — legal (the unique index only
        covers PRIMARY domains) but ambiguous, and primary beats alias in
        ``get_by_domain``, so new matches route to the restored shell. Drop the
        alias from the winner if that is not what you wanted.
      - Instantly campaigns: none were touched. Company soft-delete never
        called ``apply_account_disable_effects`` (that is the not_a_fit/dnd
        park path), so there is no external outreach state to un-pause.

    Caller validates admin + that the row is actually soft-deleted, and commits.
    """
    deleted_at = company.deleted_at
    now = datetime.utcnow()

    company.deleted_at = None
    company.merged_into_id = None
    company.updated_at = now
    session.add(company)

    deals_restored = 0
    if deleted_at is not None:
        result = await session.execute(
            sa_update(Deal)
            .where(Deal.company_id == company.id, Deal.deleted_at == deleted_at)
            .values(deleted_at=None, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        deals_restored = int(result.rowcount or 0)

    return {"deals_restored": deals_restored, "tasks_reopened": 0}


async def live_company_with_domain(
    session: AsyncSession, domain: str, exclude_id
) -> str | None:
    """Name of the LIVE company already holding ``domain`` as its primary, if any.

    The ``lower(domain)`` unique index is partial on ``deleted_at IS NULL``
    (migration 114) precisely so a deleted account's domain can be re-used —
    which means a restore can collide with whatever took the slot. Callers use
    this to 409 with a readable message instead of letting the flush die on an
    IntegrityError nobody can decode.
    """
    from sqlalchemy import func

    normalized = (domain or "").strip().lower()
    if not normalized:
        return None
    return (
        await session.execute(
            select(Company.name)
            .where(
                Company.id != exclude_id,
                Company.deleted_at.is_(None),
                func.lower(Company.domain) == normalized,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


# Deferred model imports for the plain-company_id re-point loop. Kept in one
# map so adding a future company-scoped table is a one-line change here (and a
# miss shows up as a loud KeyError, not silent data left behind).
from app.models.company_stage_milestone import CompanyStageMilestone  # noqa: E402
from app.models.meeting import Meeting  # noqa: E402
from app.models.outreach import OutreachSequence  # noqa: E402
from app.models.recotap import RecotapAccount  # noqa: E402
from app.models.reminder import Reminder  # noqa: E402
from app.models.signal import Signal  # noqa: E402

_TABLE_MODELS = {
    "contacts": Contact,
    "deals": Deal,
    "meetings": Meeting,
    "outreach_sequences": OutreachSequence,
    "company_stage_milestones": CompanyStageMilestone,
    "reminders": Reminder,
    "recotap_accounts": RecotapAccount,
    "signals": Signal,
}
