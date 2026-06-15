#!/usr/bin/env python3
"""
Backfill Contact.timezone for rows that don't have one.

Context
-------
Prod had 0 of 1,075 contacts with a timezone because no ingest path ever
populated it. The new `app.services.timezone_infer` helper can derive a
sensible IANA zone from phone country-code + (for +1 numbers) the NANP
area-code, with a company-HQ text fallback. This script runs that helper
across every contact where `timezone IS NULL` and updates matching rows.

Safe to re-run. Only writes rows where inference produces a value AND the
contact still has no timezone (so a rep's manual override is respected).

Usage
-----
Dry-run (default, prints what it would change):

    python scripts/backfill_contact_timezones.py

Apply for real:

    python scripts/backfill_contact_timezones.py --commit

In Kubernetes:

    kubectl --kubeconfig <cfg> -n gtm-prod exec deploy/gtm-backend-deployment \
        -- bash -c "cd /app && PYTHONPATH=/app python scripts/backfill_contact_timezones.py --commit"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.services.timezone_infer import infer_timezone


async def run(commit: bool) -> int:
    inferred_counter: Counter[str] = Counter()
    unresolved = 0
    total = 0
    updated = 0

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Contact).where(Contact.timezone.is_(None))
            )
        ).scalars().all()
        total = len(rows)
        print(f"Scanning {total} contacts without a timezone...")

        # Preload all relevant companies once so we don't do N+1 fetches.
        company_ids = {c.company_id for c in rows if c.company_id}
        companies: dict = {}
        if company_ids:
            co_rows = (
                await session.execute(
                    select(Company).where(Company.id.in_(company_ids))
                )
            ).scalars().all()
            companies = {co.id: co for co in co_rows}

        for contact in rows:
            company = companies.get(contact.company_id) if contact.company_id else None
            zone = infer_timezone(
                phone=contact.phone,
                company_hq=getattr(company, "headquarters", None),
                company_region=getattr(company, "region", None),
                company_name=getattr(company, "name", None),
            )
            if not zone:
                unresolved += 1
                continue
            inferred_counter[zone] += 1
            if commit:
                contact.timezone = zone
                session.add(contact)

        if commit:
            await session.commit()

        updated = sum(inferred_counter.values())

    # Report
    print()
    print("=" * 60)
    print(f"Total contacts without timezone:  {total}")
    print(f"Inferred:                         {updated}")
    print(f"Still unresolved (no signal):     {unresolved}")
    print()
    if inferred_counter:
        print("Top inferred zones:")
        for zone, n in inferred_counter.most_common(15):
            print(f"  {zone:<32} {n}")
    print()
    if commit:
        print(f"Wrote {updated} updates to the database.")
    else:
        print("DRY-RUN — no changes written. Pass --commit to apply.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually update the DB. Without this flag the script is read-only."
    )
    args = parser.parse_args()
    return asyncio.run(run(commit=args.commit))


if __name__ == "__main__":
    sys.exit(main())
