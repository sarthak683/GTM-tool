#!/usr/bin/env python3
"""
Seed dev data — populate Account Sourcing + Prospects with synthetic accounts
and contacts so the UI has something to render.

Creates:
  - 1 SourcingBatch ("Dev seed - <timestamp>") so every company shows up on
    the Account Sourcing tab (the list filters on `sourcing_batch_id IS NOT NULL`).
  - ~30 companies with varied icp_tier / disposition / outreach_lane / region.
  - ~120 contacts distributed unevenly across those companies so the new
    Advanced Filter (prospects > N, between A and B, etc.) has obvious
    variety to filter against — some accounts have 0 prospects, some 1, some
    10+.

Usage
-----
    docker compose exec web python scripts/seed_dev_data.py

Add --commit to actually write (dry-run by default — prints what it would do):
    docker compose exec web python scripts/seed_dev_data.py --commit

Re-run safety: each invocation creates a *new* batch and *new* companies. It
does not deduplicate against earlier seeds, so calling it twice doubles the
data. That's intentional — easier to reason about than partial upserts.
"""
from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, timedelta
from uuid import uuid4

from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.models.sourcing_batch import SourcingBatch


# Deterministic randomness so reruns produce the same shape — easier to
# verify "did the seed work?" without it being a moving target.
RNG = random.Random(42)


COMPANY_SEEDS: list[tuple[str, str, str, str]] = [
    # (name, domain, industry, region)
    ("Northwind Robotics", "northwindrobotics.com", "Industrial Automation", "US"),
    ("Lumen Analytics", "lumenanalytics.io", "Data Infrastructure", "US"),
    ("Helio Energy", "helioenergy.com", "Renewable Energy", "EU"),
    ("Trailhead Logistics", "trailheadlogistics.co", "Supply Chain", "US"),
    ("Quartz Biotech", "quartzbio.com", "Life Sciences", "US"),
    ("Atlas FinOps", "atlasfinops.io", "Cloud Cost Management", "US"),
    ("Beacon Health", "beaconhealth.app", "Healthcare", "US"),
    ("Sapling HR", "saplinghr.com", "HR Tech", "EU"),
    ("Crestwave Insurance", "crestwave.co", "Insurance", "US"),
    ("Vector Mobility", "vectormobility.io", "Mobility", "EU"),
    ("Pinecone Labs", "pineconelabs.dev", "Developer Tools", "US"),
    ("Marigold Retail", "marigoldretail.com", "Retail", "US"),
    ("Granite Banking", "granitebanking.com", "FinServ", "US"),
    ("Nimbus Comms", "nimbuscomms.io", "Telecom", "EU"),
    ("Talus Manufacturing", "talusmfg.com", "Manufacturing", "US"),
    ("Skyforge Aerospace", "skyforge.aero", "Aerospace", "US"),
    ("Mosaic Education", "mosaicedu.org", "EdTech", "US"),
    ("Coral Reef Realty", "coralreefrealty.com", "Real Estate", "US"),
    ("Glacier Foods", "glacierfoods.com", "CPG / Food", "US"),
    ("Bramble Media", "bramblemedia.tv", "Media / AdTech", "US"),
    ("Pivot Legal", "pivotlegal.io", "LegalTech", "US"),
    ("Driftwood Travel", "driftwoodtravel.com", "Travel", "EU"),
    ("Cinder Gaming", "cindergaming.gg", "Games", "US"),
    ("Lattice Architects", "latticearchitects.com", "AEC", "US"),
    ("Spruce Construction", "spruceconstruction.co", "Construction", "US"),
    ("Otter Lending", "otterlending.com", "Lending", "US"),
    ("Birch Marketplace", "birchmarketplace.com", "B2B Marketplace", "EU"),
    ("Tundra Climate", "tundraclimate.org", "Climate Tech", "EU"),
    ("Citrine Auto", "citrineauto.com", "Automotive", "US"),
    ("Wolfram Cyber", "wolframcyber.com", "Cybersecurity", "US"),
]

ICP_TIERS = ["hot", "warm", "warm", "cool", "cool"]  # weighted: more warm/cool than hot
DISPOSITIONS = [None, None, "working", "interested", "not_interested", "qualified"]
OUTREACH_LANES = [None, "founder_led", "outbound", "warm_intro", "events"]
PRIORITY_TAGS = [None, "P0", "P1", "P1", "P2"]
FUNDING_STAGES = [None, "Seed", "Series A", "Series B", "Series C", "Public", "PE-backed"]

# Curated first/last name pools — readable, multicultural, won't trip the
# prospect-hygiene filter (no machine-generated 28-char tokens, no role
# mailboxes, no .unknown domains).
FIRST_NAMES = [
    "Avery", "Priya", "Jonas", "Mei", "Diego", "Ines", "Olu", "Yara", "Kenji", "Lila",
    "Marcus", "Sofia", "Eitan", "Anika", "Theo", "Naomi", "Owen", "Ravi", "Chen", "Hugo",
    "Maya", "Tariq", "Elena", "Caleb", "Aditi",
]
LAST_NAMES = [
    "Okafor", "Patel", "Lindberg", "Tanaka", "Alvarez", "Sousa", "Adeyemi", "Haddad",
    "Watanabe", "Brennan", "Reyes", "Khan", "Cohen", "Iyer", "Mueller", "Davies",
    "Park", "Rodrigues", "Singh", "Becker", "Wright", "Cardoso",
]
TITLES = [
    "Director of Engineering",
    "VP Product",
    "Head of Revenue Operations",
    "Chief Information Officer",
    "Director, Sales Enablement",
    "Senior Manager, Customer Success",
    "Head of Platform",
    "Director of Marketing Operations",
    "VP of Digital Transformation",
    "Director, IT",
]
SENIORITIES = ["director", "vp", "head", "manager", "c_level"]
PERSONAS = [None, "champion", "economic_buyer", "technical_evaluator"]

# Per-company prospect count buckets: roughly 30 accounts × the multiplier
# below = ~120 contacts and a good spread for the operator+value filter.
#   4 accounts × 0   prospects
#   6 accounts × 1   prospect
#  10 accounts × 2–3 prospects
#   6 accounts × 5–7 prospects
#   4 accounts × 10–14 prospects
PROSPECT_BUCKETS: list[tuple[int, int, int]] = [
    (4, 0, 0),
    (6, 1, 1),
    (10, 2, 3),
    (6, 5, 7),
    (4, 10, 14),
]


def _assign_prospect_counts() -> list[int]:
    counts: list[int] = []
    for n_accounts, lo, hi in PROSPECT_BUCKETS:
        for _ in range(n_accounts):
            counts.append(RNG.randint(lo, hi))
    # Shuffle so the bucket order isn't visible in the UI sort.
    RNG.shuffle(counts)
    return counts


def _make_company(idx: int, batch_id, prospect_count: int) -> Company:
    name, domain, industry, region = COMPANY_SEEDS[idx]
    created_at = datetime.utcnow() - timedelta(days=RNG.randint(0, 60), hours=RNG.randint(0, 23))
    icp_tier = RNG.choice(ICP_TIERS)
    # Hot accounts ought to look richer — give them a thesis + verdict + a
    # higher icp_score so they cluster near the top of the priority view.
    icp_score = {"hot": RNG.randint(75, 95), "warm": RNG.randint(55, 74), "cool": RNG.randint(20, 54)}[icp_tier]
    enriched = RNG.random() > 0.25
    return Company(
        name=name,
        domain=domain,
        industry=industry,
        region=region,
        headquarters={"US": "San Francisco, CA", "EU": "Berlin, Germany"}.get(region) or "Remote",
        employee_count=RNG.choice([45, 120, 230, 480, 900, 1800, 4200]),
        arr_estimate=float(RNG.choice([5_000_000, 12_000_000, 28_000_000, 60_000_000, 140_000_000])),
        funding_stage=RNG.choice(FUNDING_STAGES),
        has_dap=RNG.random() > 0.6,
        icp_tier=icp_tier,
        icp_score=icp_score,
        disposition=RNG.choice(DISPOSITIONS),
        recommended_outreach_lane=RNG.choice(OUTREACH_LANES),
        priority_tag=RNG.choice(PRIORITY_TAGS),
        sourcing_batch_id=batch_id,
        enriched_at=created_at if enriched else None,
        enrichment_sources={"created_from": "seed_dev_data", "seed_version": 1} if enriched else None,
        description=f"{name} operates in {industry}. Seeded for local dev — prospect target count: {prospect_count}.",
        account_thesis=(
            f"{name} is exploring AI-led automation in {industry}. Match on team size and "
            "ownership stage suggests an outbound-led motion."
        ) if icp_tier == "hot" else None,
        why_now="Recent leadership hire signals platform investment." if icp_tier == "hot" else None,
        outreach_plan={"contact_count": prospect_count, "seeded": True},
        created_at=created_at,
        updated_at=created_at,
    )


def _make_contact(company: Company, idx_in_company: int) -> Contact:
    first = RNG.choice(FIRST_NAMES)
    last = RNG.choice(LAST_NAMES)
    # Email must be on the company domain — otherwise the prospect_only
    # filter on the Contacts page will hide the row as a domain mismatch.
    email = f"{first.lower()}.{last.lower()}{idx_in_company}@{company.domain}"
    return Contact(
        first_name=first,
        last_name=last,
        email=email,
        email_verified=RNG.random() > 0.3,
        title=RNG.choice(TITLES),
        seniority=RNG.choice(SENIORITIES),
        persona=RNG.choice(PERSONAS),
        company_id=company.id,
        # The Contacts page's prospect_only filter requires either a manual
        # source tag or a passing junk filter. Tagging as a CSV upload makes
        # these contacts unambiguously "real" prospects.
        enrichment_data={"source": "prospect_csv_upload", "seed_version": 1},
        email_open_count=RNG.randint(0, 8),
        email_click_count=RNG.randint(0, 3),
        timezone=RNG.choice(["America/Los_Angeles", "America/New_York", "Europe/Berlin", "Asia/Kolkata"]),
        created_at=datetime.utcnow() - timedelta(days=RNG.randint(0, 30)),
        updated_at=datetime.utcnow(),
    )


async def seed(commit: bool) -> dict[str, int]:
    counts = _assign_prospect_counts()
    if len(counts) != len(COMPANY_SEEDS):
        raise RuntimeError(
            f"Prospect bucket totals ({len(counts)}) must match company count ({len(COMPANY_SEEDS)})"
        )

    async with AsyncSessionLocal() as session:
        batch_id = uuid4()
        batch = SourcingBatch(
            id=batch_id,
            filename=f"Dev seed - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
            status="completed",
            total_rows=len(COMPANY_SEEDS),
            processed_rows=len(COMPANY_SEEDS),
            created_companies=len(COMPANY_SEEDS),
            skipped_rows=0,
            failed_rows=0,
            meta={"upload_mode": "dev_seed", "seed_version": 1},
        )
        session.add(batch)
        # Flush so the FK target row exists before any company references it.
        await session.flush()

        companies: list[Company] = []
        contacts: list[Contact] = []
        for idx, prospect_count in enumerate(counts):
            company = _make_company(idx, batch_id, prospect_count)
            companies.append(company)
            session.add(company)
            # Need company.id populated before we can attach contacts;
            # flush per-company so the FK is available.
            await session.flush()
            for j in range(prospect_count):
                contact = _make_contact(company, j + 1)
                contacts.append(contact)
                session.add(contact)

        if commit:
            await session.commit()
        else:
            await session.rollback()

        return {
            "batch": 1,
            "companies": len(companies),
            "contacts": len(contacts),
            "committed": int(commit),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Actually persist the seed data. Without this, the script does a dry-run.")
    args = parser.parse_args()

    result = asyncio.run(seed(commit=args.commit))
    mode = "WROTE" if args.commit else "DRY RUN (no --commit)"
    print(f"[seed_dev_data] {mode}")
    for key, value in result.items():
        print(f"  {key}: {value}")
    if not args.commit:
        print("Re-run with --commit to persist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
