from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlmodel import select

from app.core.dependencies import CurrentUser, DBSession
from app.models.battlecard import Battlecard, BattlecardCreate, BattlecardRead, BattlecardUpdate
from app.repositories.battlecard import BattlecardRepository

router = APIRouter(prefix="/battlecards", tags=["battlecards"])


@router.get("/search", response_model=List[BattlecardRead])
async def search_battlecards(_user: CurrentUser, q: str = Query(..., min_length=2), session: DBSession = None):
    return await BattlecardRepository(session).search(q)


@router.get("/", response_model=List[BattlecardRead])
async def list_battlecards(
    session: DBSession,
    _user: CurrentUser,
    category: Optional[str] = Query(default=None),
):
    repo = BattlecardRepository(session)
    filters = [Battlecard.is_active == True]
    if category:
        filters.append(Battlecard.category == category)
    return await repo.list(*filters, order_by=Battlecard.category)


@router.post("/", response_model=BattlecardRead, status_code=201)
async def create_battlecard(payload: BattlecardCreate, session: DBSession, _user: CurrentUser):
    return await BattlecardRepository(session).create(payload.model_dump())


@router.get("/{battlecard_id}", response_model=BattlecardRead)
async def get_battlecard(battlecard_id: UUID, session: DBSession, _user: CurrentUser):
    return await BattlecardRepository(session).get_or_raise(battlecard_id)


@router.put("/{battlecard_id}", response_model=BattlecardRead)
async def update_battlecard(battlecard_id: UUID, payload: BattlecardUpdate, session: DBSession, _user: CurrentUser):
    repo = BattlecardRepository(session)
    card = await repo.get_or_raise(battlecard_id)
    update_data = payload.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.utcnow()
    return await repo.update(card, update_data)


@router.delete("/{battlecard_id}", status_code=204)
async def delete_battlecard(battlecard_id: UUID, session: DBSession, _user: CurrentUser):
    repo = BattlecardRepository(session)
    card = await repo.get_or_raise(battlecard_id)
    await repo.delete(card)


@router.post("/seed", status_code=201)
async def seed_battlecards(session: DBSession, _user: CurrentUser):
    """Seed default Beacon.li battlecards. Safe to run multiple times."""
    defaults = [
        {
            "category": "objection", "title": "Price too high",
            "trigger": "too expensive, pricing, cost, budget",
            "response": (
                "Understood — let's look at the ROI together. Our customers typically see "
                "a 60–80% reduction in implementation time, which translates to 3–6 months "
                "saved per enterprise deployment. What's your current cost for a manual rollout?"
            ),
            "tags": "pricing,roi,objection",
        },
        {
            "category": "objection", "title": "We already have an implementation partner",
            "trigger": "partner, si, integrator, consultancy, accenture, deloitte",
            "response": (
                "We work alongside SIs, not against them. Beacon.li handles the orchestration "
                "layer — automating the repetitive handoffs, status updates, and config tasks — "
                "so your SI focuses on high-value customisation. Many of our customers use both."
            ),
            "tags": "partner,si,objection",
        },
        {
            "category": "objection", "title": "Security / data concerns",
            "trigger": "security, data, privacy, gdpr, soc2, compliance",
            "response": (
                "Beacon.li is SOC 2 Type II certified. We process orchestration metadata only — "
                "no customer PII leaves your environment. Our architecture uses agent-side "
                "execution with encrypted config tokens. Happy to share our security whitepaper."
            ),
            "tags": "security,compliance,gdpr",
        },
        {
            "category": "competitor", "title": "vs. Rocketlane",
            "trigger": "rocketlane",
            "response": (
                "Rocketlane is a project portal — great for customer-facing project tracking. "
                "Beacon.li is an AI orchestration engine that automates the *execution* of "
                "implementation tasks, not just the visibility. We complement Rocketlane."
            ),
            "competitor": "Rocketlane", "tags": "competitor,rocketlane",
        },
        {
            "category": "competitor", "title": "vs. Arrows",
            "trigger": "arrows",
            "response": (
                "Arrows focuses on customer onboarding checklists. Beacon.li goes deeper — "
                "we automate configuration, integration setup, and cross-system handoffs "
                "that Arrows can't touch. Different layers of the implementation stack."
            ),
            "competitor": "Arrows", "tags": "competitor,arrows",
        },
        {
            "category": "tech_faq", "title": "What integrations do you support?",
            "trigger": "integrations, api, connect, webhook, salesforce, hubspot",
            "response": (
                "We support 80+ native connectors including Salesforce, HubSpot, NetSuite, SAP, "
                "Workday, and Jira. Custom integrations are built using our REST/webhook "
                "framework. Average integration time is 2 days vs. 3–4 weeks for manual builds."
            ),
            "tags": "integrations,api,tech",
        },
        {
            "category": "tech_faq", "title": "How long does implementation take?",
            "trigger": "how long, timeline, time to value, ttv, onboarding time",
            "response": (
                "Typical time-to-value is 4–6 weeks for standard deployments vs. 4–6 months "
                "without Beacon.li. The AI orchestration layer handles environment setup, "
                "data migration templates, and regression testing in parallel."
            ),
            "tags": "timeline,ttv,implementation",
        },
        {
            "category": "use_case", "title": "HRTech SaaS deployment",
            "trigger": "hr, hris, payroll, workday, bamboo, people management",
            "response": (
                "In HRTech, Beacon.li automates the data migration from legacy HRIS, sets up SSO, "
                "configures payroll rules, and runs validation checks — cutting 8-week manual "
                "implementations to under 2 weeks."
            ),
            "tags": "hrtech,use_case,workday",
        },
    ]

    created = 0
    for card_data in defaults:
        existing = (
            await session.execute(
                select(Battlecard).where(Battlecard.title == card_data["title"])
            )
        ).scalar_one_or_none()
        if existing:
            continue
        session.add(Battlecard(**card_data))
        created += 1

    await session.commit()
    return {"seeded": created, "message": f"{created} battlecards created"}
