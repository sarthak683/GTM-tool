#!/usr/bin/env python3
"""
Seed three demo lifecycles so the Sequence-Lifecycle drawer has rich data
to render (subjects, bodies, opens / clicks / replies, calls with notes
and durations, AI summaries, actors).

Picks three deterministic seeded prospects and paints each with a distinct
lifecycle status so the drawer's render branches are all exercised:

  Prospect 1 → in_progress  (2 emails sent, opens + clicks, no reply yet,
                             1 call logged with AI summary)
  Prospect 2 → replied      (1 email sent, opened, full reply body)
  Prospect 3 → booked       (email sent, opened, call → demo booked)

Idempotent: re-running deletes the previous demo sequence + activity rows
for the same three contacts (identified by enrichment_data.demo_seed = 1)
before re-inserting. Safe to run repeatedly.

Usage
-----
    docker compose exec -T -w /app backend python -m scripts.seed_demo_lifecycles
    # add --commit to persist (dry-run by default)
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import JSONB

from app.database import AsyncSessionLocal
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.outreach import OutreachSequence, OutreachStep


# Generation_context marker so we can identify (and replace) demo data
# without touching real outreach.
DEMO_MARKER = {"source": "demo_seed_v1"}


async def _pick_demo_contacts(session) -> list[Contact]:
    """Pick 3 seeded prospects deterministically. We sort by id so reruns
    hit the same prospects — important because the script is idempotent."""
    rows = (
        await session.execute(
            select(Contact)
            .where(
                Contact.enrichment_data.cast(JSONB).contains({"source": "prospect_csv_upload"})
            )
            .order_by(Contact.id.asc())
            .limit(3)
        )
    ).scalars().all()
    if len(rows) < 3:
        raise RuntimeError(
            f"Need at least 3 seeded prospects with source=prospect_csv_upload; "
            f"found {len(rows)}. Run scripts/seed_dev_data.py --commit first."
        )
    return list(rows)


async def _purge_existing(session, contact_id: UUID) -> None:
    """Delete prior demo sequence + activities for this contact so re-runs
    don't pile up duplicates."""
    # Delete demo OutreachSequence rows (marker in generation_context).
    existing = (
        await session.execute(
            select(OutreachSequence).where(OutreachSequence.contact_id == contact_id)
        )
    ).scalars().all()
    for seq in existing:
        gen_ctx = seq.generation_context or {}
        if isinstance(gen_ctx, dict) and gen_ctx.get("source") == "demo_seed_v1":
            # Delete the step rows hung off this sequence too.
            await session.execute(delete(OutreachStep).where(OutreachStep.sequence_id == seq.id))
            await session.delete(seq)
    # Delete demo Activities (we tagged them in event_metadata).
    await session.execute(
        delete(Activity).where(
            Activity.contact_id == contact_id,
            Activity.event_metadata.cast(JSONB).contains({"demo_seed": 1}),
        )
    )


def _build_sequence_plan(launched_at: datetime, scenario: str) -> dict:
    """The 4-step plan the lifecycle reconciler reads from contact.enrichment_data.
    Stored as a list of {day_offset, channel, objective, subject}."""
    return {
        "steps": [
            {
                "day_offset": 0,
                "channel": "email",
                "objective": "Opener — ROI lead with industry-specific stat",
                "subject": "5 hours per AP analyst per week",
            },
            {
                "day_offset": 3,
                "channel": "email",
                "objective": "Bump — social proof, similar company",
                "subject": "How BlackLine cut close cycles by 38%",
            },
            {
                "day_offset": 5,
                "channel": "call",
                "objective": "Live call — qualifier + book demo",
            },
            {
                "day_offset": 7,
                "channel": "email",
                "objective": "Break-up — last touch",
                "subject": "Closing the loop on Beacon",
            },
        ]
    }


def _email_body(prospect_name: str, company: str, step: int) -> str:
    """Realistic-ish multi-line outbound body. Three variants so the drawer
    can demo different lengths."""
    if step == 1:
        return (
            f"Hi {prospect_name},\n\n"
            f"Saw {company} just promoted a new VP of Finance — congrats on the momentum.\n\n"
            "When AP teams roughly your size move to AI-led approvals, we typically see\n"
            "5–7 hours back per analyst per week within the first 60 days. Worth 20 min\n"
            "to compare notes on what the path could look like for you?\n\n"
            "— Sarthak\n"
            "Beacon"
        )
    if step == 2:
        return (
            f"Hi {prospect_name},\n\n"
            f"Quick follow-up: BlackLine ran the same automation play and cut their\n"
            "close cycle from 6.2 days to 3.8 days in a quarter. Happy to walk you\n"
            f"through the exact rollout — would Thursday or Friday next week work for {company}?\n\n"
            "— Sarthak"
        )
    return (
        f"Hi {prospect_name},\n\n"
        "I'll stop reaching out after this one. If automation in AP isn't a 2026\n"
        "priority for the team, no worries — just hit reply with a \"not now\" and I'll\n"
        "circle back next year.\n\n"
        "— Sarthak"
    )


async def _seed_one(session, contact: Contact, scenario: str, demo_user_id: Optional[UUID]) -> dict:
    """Paint a full lifecycle on one contact according to `scenario`."""
    company_name = "their company"  # We don't load the Company here; safe fallback.
    first_name = contact.first_name or "there"

    now = datetime.utcnow()
    launched_at = now - timedelta(days=8)
    plan = _build_sequence_plan(launched_at, scenario)

    # Persist the plan onto the contact so the lifecycle reconciler picks it up.
    ed = dict(contact.enrichment_data) if isinstance(contact.enrichment_data, dict) else {}
    ed["sequence_plan"] = plan
    contact.enrichment_data = ed

    # Move sequence_status to match scenario so the lifecycle rollup is right.
    if scenario == "replied":
        contact.sequence_status = "replied"
    elif scenario == "booked":
        contact.sequence_status = "meeting_booked"
    else:
        contact.sequence_status = "sent"

    # Create the OutreachSequence
    seq = OutreachSequence(
        contact_id=contact.id,
        company_id=contact.company_id,
        status="launched",
        email_1="Subject + opener email body lives here",
        subject_1="5 hours per AP analyst per week",
        instantly_campaign_id="demo-campaign-001",
        instantly_campaign_status="active",
        generation_context=DEMO_MARKER,
        generated_at=launched_at - timedelta(hours=2),
        launched_at=launched_at,
    )
    session.add(seq)
    await session.flush()

    # Step 1 — Email sent at launch
    act_sent_1 = Activity(
        type="email",
        source="instantly",
        medium="email",
        content=_email_body(first_name, company_name, 1),
        contact_id=contact.id,
        email_subject="5 hours per AP analyst per week",
        email_from="sarthak@beacon.li",
        email_to=contact.email or "",
        ai_summary="Outbound opener referencing a recent VP Finance promotion; offers 20-min comparison call. Tone: warm, low-pressure.",
        created_at=launched_at + timedelta(minutes=5),
        event_metadata={"event_type": "email_sent", "demo_seed": 1},
        created_by_id=demo_user_id,
    )
    session.add(act_sent_1)

    # Opened — same day, 3h later
    session.add(Activity(
        type="email",
        source="instantly",
        medium="email",
        contact_id=contact.id,
        email_subject="5 hours per AP analyst per week",
        content="",
        created_at=launched_at + timedelta(hours=3),
        event_metadata={"event_type": "email_opened", "demo_seed": 1},
    ))

    if scenario == "in_progress":
        # Clicked one of the links in the opener
        session.add(Activity(
            type="email", source="instantly", medium="email", contact_id=contact.id,
            email_subject="5 hours per AP analyst per week",
            content="https://beacon.li/case-study/blackline",
            created_at=launched_at + timedelta(hours=4),
            event_metadata={"event_type": "email_link_clicked", "demo_seed": 1},
        ))
        # Step 2 — bump email sent day 3
        session.add(Activity(
            type="email", source="instantly", medium="email", contact_id=contact.id,
            content=_email_body(first_name, company_name, 2),
            email_subject="How BlackLine cut close cycles by 38%",
            email_from="sarthak@beacon.li",
            email_to=contact.email or "",
            ai_summary="Bump with BlackLine case study + ask for Thursday/Friday meeting.",
            created_at=launched_at + timedelta(days=3, minutes=10),
            event_metadata={"event_type": "email_sent", "demo_seed": 1},
            created_by_id=demo_user_id,
        ))
        # Opened day 3 + 90 minutes
        session.add(Activity(
            type="email", source="instantly", medium="email", contact_id=contact.id,
            email_subject="How BlackLine cut close cycles by 38%",
            content="",
            created_at=launched_at + timedelta(days=3, hours=1, minutes=30),
            event_metadata={"event_type": "email_opened", "demo_seed": 1},
        ))
        # Call on day 5 — connected but no decision
        session.add(Activity(
            type="call",
            source="manual",
            medium="call",
            contact_id=contact.id,
            content=(
                "Caught Mei live. Initial reaction: they're already piloting Coupa for AP "
                "but mentioned the close cycle is the bigger pain. Pushed back on price "
                "anchor; agreed to a 20-min walkthrough next Wednesday at 10am PT. "
                "Champion candidate. Mentioned the CFO is the actual budget owner."
            ),
            ai_summary=(
                "Champion lead. Open to a 20-min walkthrough on Wed 10am PT. Need to "
                "loop in CFO for budget. Competitor: Coupa (AP pilot). Pain: close cycle."
            ),
            call_outcome="connected",
            call_duration=412,  # 6m 52s
            aircall_user_name="Sarthak Aitha",
            created_at=launched_at + timedelta(days=5, hours=2),
            event_metadata={"event_type": "call_logged", "demo_seed": 1},
            created_by_id=demo_user_id,
        ))

    elif scenario == "replied":
        # Replied later same day
        session.add(Activity(
            type="email",
            source="instantly",
            medium="email",
            contact_id=contact.id,
            email_subject="Re: 5 hours per AP analyst per week",
            email_from=contact.email or "prospect@example.com",
            email_to="sarthak@beacon.li",
            content=(
                "Hi Sarthak,\n\n"
                "Thanks for reaching out — your timing's better than you know. We just "
                "kicked off a 2026 planning cycle and AP automation made the shortlist.\n\n"
                "I'd be happy to take a 20-min look. Can you propose 3 times next week "
                "(Mon–Wed afternoon Pacific)? I'll loop in our Controller as well.\n\n"
                "Best,\n"
                "Mei"
            ),
            ai_summary=(
                "Positive reply. Prospect just kicked off 2026 planning; AP automation "
                "is on the shortlist. Wants 3 time options Mon–Wed PM Pacific. Will loop "
                "in Controller. Strong buy signal."
            ),
            created_at=launched_at + timedelta(hours=8),
            event_metadata={"event_type": "reply_received", "demo_seed": 1},
        ))

    elif scenario == "booked":
        # Call on day 4 — demo booked
        session.add(Activity(
            type="call",
            source="manual",
            medium="call",
            contact_id=contact.id,
            content=(
                "Connected on second try. Walked through the AP automation deck; he liked "
                "the ROI calculator and the integration story. Booked a deeper dive for "
                "next Tuesday with their Controller + IT lead. Sending the calendar invite "
                "+ the case study PDF as the follow-up."
            ),
            ai_summary=(
                "Demo booked for next Tuesday with Controller + IT lead. Liked ROI calc + "
                "integration story. Send calendar invite + BlackLine case study."
            ),
            call_outcome="connected",
            call_duration=937,  # 15m 37s
            recording_url="https://aircall.io/recordings/demo-call-942",
            aircall_user_name="Sarthak Aitha",
            created_at=launched_at + timedelta(days=4, hours=3),
            event_metadata={"event_type": "call_logged", "demo_seed": 1},
            created_by_id=demo_user_id,
        ))

    return {
        "contact_id": str(contact.id),
        "name": f"{contact.first_name} {contact.last_name}".strip(),
        "scenario": scenario,
    }


async def seed(commit: bool) -> dict:
    async with AsyncSessionLocal() as session:
        contacts = await _pick_demo_contacts(session)

        # Look up the first available user as the demo "logged by" actor.
        from app.models.user import User
        user_row = (
            await session.execute(select(User).order_by(User.created_at.asc()).limit(1))
        ).scalars().first()
        demo_user_id = user_row.id if user_row else None

        for c in contacts:
            await _purge_existing(session, c.id)

        scenarios = ["in_progress", "replied", "booked"]
        results = []
        for c, scenario in zip(contacts, scenarios):
            results.append(await _seed_one(session, c, scenario, demo_user_id))

        if commit:
            await session.commit()
        else:
            await session.rollback()

        return {
            "committed": int(commit),
            "demo_user": user_row.email if user_row else None,
            "prospects": results,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Persist. Without this, dry-run.")
    args = parser.parse_args()

    result = asyncio.run(seed(commit=args.commit))
    mode = "WROTE" if args.commit else "DRY RUN"
    print(f"[seed_demo_lifecycles] {mode}")
    print(f"  demo logged-by user: {result['demo_user']}")
    for p in result["prospects"]:
        print(f"  · {p['scenario']:>12s}  {p['name']:<28s}  {p['contact_id']}")
    if not args.commit:
        print("Re-run with --commit to persist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
