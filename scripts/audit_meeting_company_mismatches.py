#!/usr/bin/env python3
"""
Audit meetings whose title contains a company name that differs from the
currently-linked `company_id`. Read-only — prints a report, does not update.

Why this exists
---------------
Calendar sync's Pass 1 (attendee-email -> contact -> deal -> company) can
misfire when:
  - An employee from a different account is invited to a meeting titled after
    the real prospect (e.g., "Procore X Beacon – Next steps" but an Azentio
    attendee sits on our Azentio deal).
  - A shared contact sits on more than one deal.

This script reproduces the title-match heuristic from `app/services/calendar_sync.py`
(`_title_company_match`) so the list you see here is exactly the set the new
backend guard would have rejected at sync time.

Usage (inside the backend container so DATABASE_URL is set)
-----------------------------------------------------------
    docker compose exec web python scripts/audit_meeting_company_mismatches.py
    docker compose exec web python scripts/audit_meeting_company_mismatches.py --csv out.csv

In Kubernetes
-------------
    kubectl exec -n <ns> deploy/<backend-deploy> -- python scripts/audit_meeting_company_mismatches.py
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.meeting import Meeting


def _normalize_name_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return " ".join(cleaned.split())


def _title_company_match(
    title: str,
    candidates: list[tuple[str, UUID, str]],
) -> Optional[tuple[UUID, str]]:
    """Longest unambiguous whole-token match. None on ambiguity."""
    norm_title = _normalize_name_key(title)
    if not norm_title:
        return None
    padded = f" {norm_title} "
    matches: list[tuple[str, UUID, str]] = []
    for norm_name, cid, display in candidates:
        if len(norm_name) < 4:
            continue
        if f" {norm_name} " in padded:
            matches.append((norm_name, cid, display))
    if not matches:
        return None
    matches.sort(key=lambda m: len(m[0]), reverse=True)
    longest_len = len(matches[0][0])
    top = [m for m in matches if len(m[0]) == longest_len]
    top_ids = {cid for _, cid, _ in top}
    if len(top_ids) != 1:
        return None
    return top[0][1], top[0][2]


async def run(csv_path: Optional[str]) -> int:
    async with AsyncSessionLocal() as session:
        companies = (await session.execute(
            select(Company.id, Company.name)
        )).all()
        candidates = [
            (_normalize_name_key(c.name), c.id, c.name)
            for c in companies
            if c.name
        ]
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        name_by_id = {c.id: c.name for c in companies}

        meetings = (await session.execute(
            select(
                Meeting.id,
                Meeting.title,
                Meeting.company_id,
                Meeting.deal_id,
                Meeting.scheduled_at,
                Meeting.external_source,
                Meeting.manually_linked,
            ).where(Meeting.company_id.is_not(None))
        )).all()

        rows: list[dict] = []
        for m in meetings:
            hit = _title_company_match(m.title or "", candidates)
            if not hit:
                continue
            title_cid, title_name = hit
            if title_cid == m.company_id:
                continue
            rows.append({
                "meeting_id": str(m.id),
                "title": m.title,
                "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else "",
                "linked_company_id": str(m.company_id),
                "linked_company_name": name_by_id.get(m.company_id, "?"),
                "title_suggests_company_id": str(title_cid),
                "title_suggests_company_name": title_name,
                "external_source": m.external_source or "",
                "manually_linked": bool(m.manually_linked),
            })

    if not rows:
        print("No title/company mismatches found. Nothing to fix.")
        return 0

    print(f"Found {len(rows)} meeting(s) where the title names a different company than the link:\n")
    for r in rows:
        lock = " [manually_linked]" if r["manually_linked"] else ""
        print(
            f"- {r['scheduled_at'][:16]}  {r['title'][:70]!r:<72}{lock}\n"
            f"    linked:        {r['linked_company_name']}  ({r['linked_company_id']})\n"
            f"    title suggests: {r['title_suggests_company_name']}  ({r['title_suggests_company_id']})\n"
            f"    source: {r['external_source']}  meeting_id: {r['meeting_id']}\n"
        )

    if csv_path:
        fieldnames = list(rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {csv_path}")

    print(
        "\nNo rows were modified. To fix individual meetings use the 'Unlink' "
        "banner in the Pre-Meeting Assistance page or the Re-link panel on the "
        "meeting detail page. Both set manually_linked=true so future calendar "
        "syncs cannot re-attach the wrong company."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="Optional path to also write results as CSV.")
    args = parser.parse_args()
    return asyncio.run(run(args.csv))


if __name__ == "__main__":
    sys.exit(main())
