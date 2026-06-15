#!/usr/bin/env python3
"""
Delete contacts that fail the (hardened) prospect hygiene rules.

Why this exists
---------------
A prior version of `prospect_hygiene.py` accepted records whose email was a
bulk-email / tracking / unsubscribe-wrapper URL (e.g. anything under
customer.io, list-manage.com, sendgrid.net, etc.), or whose last_name was
a machine-generated token of 28+ unbroken alphanumerics, or whose email
field contained our own enrichment warning strings (⚠ not available —
inferred: ...). Those records polluted the prod contacts table.

This script re-runs the *current* hygiene filter against every Contact and
deletes the ones it now rejects. Orphaned Company rows (created only to
host an invalid contact) get deleted too.

Usage (dry-run — prints what it would delete, changes nothing)
--------------------------------------------------------------
    python scripts/cleanup_invalid_prospects.py

With actual deletion:
    python scripts/cleanup_invalid_prospects.py --commit

In a Kubernetes pod:
    kubectl exec -n gtm-prod deploy/gtm-backend-deployment -- \\
        python scripts/cleanup_invalid_prospects.py --commit
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Iterable

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.services.prospect_hygiene import (
    invalid_prospect_reason,
    is_valid_prospect_candidate,
)


def _should_delete(contact: Contact) -> str | None:
    """Return a reason string if this contact should be deleted, else None."""
    return invalid_prospect_reason(
        first_name=contact.first_name,
        last_name=contact.last_name,
        email=contact.email,
        title=contact.title,
        linkedin_url=contact.linkedin_url,
    ) if not is_valid_prospect_candidate(
        first_name=contact.first_name,
        last_name=contact.last_name,
        email=contact.email,
        title=contact.title,
        linkedin_url=contact.linkedin_url,
    ) else None


async def _delete_orphaned_companies(
    session: AsyncSession, company_ids: Iterable[str], commit: bool
) -> int:
    """Delete companies that no longer have any contacts after the purge."""
    deleted = 0
    for cid in company_ids:
        remaining = (
            await session.execute(
                select(Contact.id).where(Contact.company_id == cid).limit(1)
            )
        ).first()
        if remaining:
            continue  # still has other contacts, keep
        company = await session.get(Company, cid)
        if not company:
            continue
        # Only prune *placeholder* / *.unknown* / infrastructure-domain
        # companies. A real company whose single contact we just purged
        # shouldn't disappear — the account may still be valuable.
        domain = (company.domain or "").lower()
        name = (company.name or "").lower()
        placeholder_signals = (
            domain.endswith(".unknown"),
            "customer.io" in domain,
            "unsubscribe" in domain or "unsubscribe" in name,
            "list-manage" in domain,
            "sendgrid" in domain,
            "mailchimp" in domain,
            "substack.com" == domain,
        )
        if not any(placeholder_signals):
            continue
        print(f"  orphan company → {company.name!r} ({company.domain})")
        if commit:
            await session.delete(company)
        deleted += 1
    return deleted


async def run(commit: bool) -> int:
    bad: list[tuple[Contact, str]] = []
    company_ids_touched: set[str] = set()

    async with AsyncSessionLocal() as session:
        contacts = (
            await session.execute(select(Contact).limit(10_000))
        ).scalars().all()
        print(f"Scanning {len(contacts)} contacts...")

        for c in contacts:
            reason = _should_delete(c)
            if reason:
                bad.append((c, reason))
                if c.company_id:
                    company_ids_touched.add(str(c.company_id))

        if not bad:
            print("No invalid contacts found. Nothing to do.")
            return 0

        print()
        print(f"Found {len(bad)} invalid contacts:")
        for c, reason in bad:
            name = f"{c.first_name or ''} {c.last_name or ''}".strip() or "(no name)"
            email = (c.email or "")[:72]
            print(f"  {c.id}  {name[:40]:<40}  {email:<72}  → {reason}")

        if commit:
            print()
            print("Deleting...")
            for c, _ in bad:
                await session.delete(c)
            await session.commit()
            print(f"Deleted {len(bad)} contacts.")

            orphans_deleted = await _delete_orphaned_companies(
                session, company_ids_touched, commit=True
            )
            await session.commit()
            print(f"Deleted {orphans_deleted} orphan company row(s).")
        else:
            print()
            print("DRY RUN — nothing was deleted. Re-run with --commit to apply.")
            orphans = await _delete_orphaned_companies(
                session, company_ids_touched, commit=False
            )
            if orphans:
                print(f"(would also prune {orphans} orphan company row(s))")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually delete. Without this flag the script is dry-run only.",
    )
    args = parser.parse_args()
    return asyncio.run(run(commit=args.commit))


if __name__ == "__main__":
    sys.exit(main())
