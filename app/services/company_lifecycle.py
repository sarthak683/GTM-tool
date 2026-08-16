"""Company lifecycle operations: soft-delete and merge.

Both exist to replace what used to be manual SQL (or a destructive cascade):

- ``soft_delete_company``: the account leaves every current-state surface via
  ``deleted_at``; nothing referencing it is destroyed, so historical metrics
  never rewrite. Its live deals are soft-deleted with it and their open system
  tasks dismissed (a task pointing at an invisible deal is a ghost nag).

- ``merge_companies``: the IRIS/Dayforce-class fix. Everything the loser owns
  moves to the winner, the loser's domain (plus its aliases) become winner
  alias domains — so every domain matcher keeps resolving old addresses to the
  surviving account — and the loser is soft-deleted with an audit trail on
  both rows.

Callers commit; both functions only stage changes on the session.
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
    loser.updated_at = now
    session.add(loser)

    return moved


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
