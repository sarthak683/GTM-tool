#!/usr/bin/env python3
"""Seed Pipeline deals across stages so localhost has something to render.

Usage:
    docker compose exec backend python scripts/seed_pipeline_deals.py
    docker compose exec backend python scripts/seed_pipeline_deals.py --commit
"""
from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, timedelta, date
from decimal import Decimal
from uuid import uuid4

from sqlmodel import select

from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.deal import Deal

RNG = random.Random(7)

STAGES = [
    "demo_scheduled", "demo_done", "qualified_lead",
    "poc_agreed", "poc_wip", "poc_done",
    "commercial_negotiation", "msa_review", "closed_won",
]

PRIORITIES = ["urgent", "high", "normal", "low"]
HEALTHS = ["green", "yellow", "red"]

DEAL_TEMPLATES = [
    ("Pilot — Sales Ops automation", 45000),
    ("Q2 expansion — Marketing AI", 120000),
    ("Enterprise rollout", 240000),
    ("POC — Finance close acceleration", 30000),
    ("Annual renewal + upsell", 85000),
    ("Net-new logo", 60000),
    ("MSA + first POC", 75000),
    ("Replacement of legacy vendor", 150000),
    ("AI implementation orchestrator", 95000),
    ("Multi-team deployment", 180000),
    ("Land deal — Ops team", 40000),
    ("Strategic partnership", 300000),
]


async def main(commit: bool) -> None:
    async with AsyncSessionLocal() as session:
        companies = (await session.execute(select(Company).limit(60))).scalars().all()
        if not companies:
            print("No companies in DB — run scripts/seed_dev_data.py --commit first.")
            return

        now = datetime.utcnow()
        created = 0
        for i, (name, value) in enumerate(DEAL_TEMPLATES):
            company = RNG.choice(companies)
            stage = STAGES[i % len(STAGES)]
            entered = now - timedelta(days=RNG.randint(1, 28))
            deal_id = uuid4()
            deal = Deal(
                id=deal_id,
                email_cc_alias=f"deal-{deal_id.hex[:8]}",
                name=f"{company.name} — {name}",
                pipeline_type="deal",
                stage=stage,
                priority=RNG.choice(PRIORITIES),
                company_id=company.id,
                value=Decimal(value + RNG.randint(-5000, 5000)),
                close_date_est=date.today() + timedelta(days=RNG.randint(15, 90)),
                health=RNG.choice(HEALTHS),
                health_score=RNG.randint(35, 95),
                stage_entered_at=entered,
                days_in_stage=(now - entered).days,
                last_activity_at=now - timedelta(days=RNG.randint(0, 9)),
                stakeholder_count=RNG.randint(1, 5),
                source=RNG.choice(["outbound", "inbound", "referral", "event"]),
                geography=RNG.choice(["US", "EU", "APAC"]),
                department=RNG.choice(["Sales", "Marketing", "Ops", "Finance"]),
                next_step=RNG.choice([
                    "Schedule technical deep-dive",
                    "Send pricing proposal",
                    "Loop in economic buyer",
                    "Confirm POC success criteria",
                    "Push for MSA signature",
                ]),
                next_step_updated_at=now - timedelta(days=RNG.randint(0, 4)),
            )
            session.add(deal)
            created += 1
            print(f"  + {deal.stage:>26}  ${deal.value:>10}  {deal.name}")

        if commit:
            await session.commit()
            print(f"\nCommitted {created} deals.")
        else:
            print(f"\nDry-run — would create {created} deals. Re-run with --commit.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--commit", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.commit))
