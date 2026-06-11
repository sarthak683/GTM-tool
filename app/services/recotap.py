"""Recotap service — Beacon ↔ Recotap shape mapping + pull/seed orchestration.

Surfaces Recotap account signals (journey stage, score, engagement, intent
sub-scores) in Account Sourcing by joining recotap_accounts to companies on
domain. Includes a deterministic mock seeder because the sandbox scores
asynchronously (fresh pushes read back unscored), so we need data to build/test
the UI against.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.recotap import RecotapClient
from app.models.company import Company
from app.models.deal import Deal
from app.models.recotap import RECOTAP_JOURNEY_STAGES, RecotapAccount, RecotapAccountRead

logger = logging.getLogger(__name__)

_ICP_FIT_LABELS = ["Strong fit", "Good fit", "Moderate fit", "Low fit"]


def normalize_domain(value: Optional[str]) -> str:
    d = (value or "").strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def _stable(seed: str, mod: int) -> int:
    """Deterministic 0..mod-1 from a string (md5, not Python's salted hash) so
    re-seeding is stable across runs."""
    return int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % mod


def _engagement_for(score: int) -> str:
    if score >= 72:
        return "Hot"
    if score >= 45:
        return "Warm"
    return "Cold"


def _to_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


async def _get_or_create_row(session: AsyncSession, domain: str) -> RecotapAccount:
    row = (
        await session.execute(select(RecotapAccount).where(RecotapAccount.domain == domain))
    ).scalar_one_or_none()
    if row is None:
        row = RecotapAccount(domain=domain)
        session.add(row)
    return row


async def pull_into_db(session: AsyncSession) -> dict[str, int]:
    """Pull live Recotap accounts → upsert recotap_accounts by domain.
    Sandbox data is mostly unscored; we keep it and mark source='recotap'."""
    client = RecotapClient()
    if not client.configured():
        return {"pulled": 0, "configured": 0}
    accounts = await client.get_accounts(limit=100)
    companies = (await session.execute(select(Company.id, Company.domain))).all()
    company_by_domain = {normalize_domain(d): cid for cid, d in companies if d}
    pulled = 0
    for a in accounts:
        domain = normalize_domain(a.get("domain"))
        if not domain:
            continue
        row = await _get_or_create_row(session, domain)
        row.rtp_aid = a.get("rtp_aid") or row.rtp_aid
        row.name = a.get("name") or row.name
        row.external_id = a.get("externalId") or row.external_id
        row.journey_stage = a.get("rtp_journey_stage") or None
        row.score = a.get("rtp_account_score")
        # Recotap's payload carries no engagement label — derive Hot/Warm/Cold from
        # the real account score so the UI chip works on pulled (non-seeded) data.
        if row.score is not None:
            row.engagement = _engagement_for(row.score)
        row.advertising_activity_score = a.get("rtp_advertising_activity_score")
        row.website_intent_score = a.get("rtp_website_intent_score")
        row.g2_intent_score = a.get("rtp_g2_intent_score")
        row.bombora_intent_score = a.get("rtp_bombora_intent_score")
        row.last_account_date = _to_dt(a.get("rtp_last_account_date"))
        row.raw = a
        row.source = "recotap"
        row.company_id = company_by_domain.get(domain) or row.company_id
        row.pulled_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        pulled += 1
    await session.commit()
    return {"pulled": pulled, "configured": 1}


async def seed_mock_signals(session: AsyncSession, *, overwrite: bool = False) -> dict[str, int]:
    """Populate recotap_accounts with deterministic mock signals for every sourced
    company, so the UI has journey-stage/score data to work with. Skips rows
    already pulled live (source='recotap') unless overwrite=True."""
    companies = (await session.execute(select(Company))).scalars().all()
    seeded = 0
    for company in companies:
        domain = normalize_domain(company.domain)
        if not domain:
            continue
        row = await _get_or_create_row(session, domain)
        # Preserve rows that carry real pulled data (pull sets pulled_at); only
        # those should be left untouched. Newly-created or seed rows have no
        # pulled_at, so they get (re)populated.
        if row.pulled_at is not None and not overwrite:
            row.company_id = row.company_id or company.id
            continue
        score = 20 + _stable(domain + "score", 80)  # 20-99
        row.name = company.name
        row.company_id = company.id
        row.journey_stage = RECOTAP_JOURNEY_STAGES[_stable(domain + "stage", len(RECOTAP_JOURNEY_STAGES))]
        row.score = score
        row.engagement = _engagement_for(score)
        row.icp_fit = _ICP_FIT_LABELS[_stable(domain + "icp", len(_ICP_FIT_LABELS))]
        row.advertising_activity_score = _stable(domain + "ad", 101)
        row.website_intent_score = _stable(domain + "web", 101)
        row.g2_intent_score = _stable(domain + "g2", 101)
        row.bombora_intent_score = _stable(domain + "bom", 101)
        row.hq_location = company.headquarters or company.region or None
        row.source = "seed"
        row.last_account_date = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        seeded += 1
    await session.commit()
    return {"seeded": seeded}


async def signals_by_domain(session: AsyncSession, domains: list[str]) -> dict[str, RecotapAccountRead]:
    """Return {normalized_domain: RecotapAccountRead} for the given domains —
    used to enrich the Account Sourcing list/detail."""
    norm = {normalize_domain(d) for d in domains if d}
    if not norm:
        return {}
    rows = (
        await session.execute(select(RecotapAccount).where(RecotapAccount.domain.in_(norm)))
    ).scalars().all()
    return {r.domain: RecotapAccountRead.model_validate(r) for r in rows}


# ── Beacon → Recotap: push CRM deal-stage status as account tags ─────────────
# Recotap won't let us set their computed Journey Stage, and custom-field keys
# are rejected unless pre-defined, so CRM status is surfaced as account tags.
_STAGE_ORDER = [
    "qualified_lead", "demo_scheduled", "demo_done",
    "poc_agreed", "poc_wip", "poc_done",
    "commercial_negotiation", "msa_review", "closed_won",
]
_STAGE_RANK = {s: i for i, s in enumerate(_STAGE_ORDER)}
_STAGE_TAG = {
    "closed_won": "Customer",
    "msa_review": "Negotiation",
    "commercial_negotiation": "Negotiation",
    "poc_done": "POC",
    "poc_wip": "POC",
    "poc_agreed": "POC",
    "demo_done": "Demo",
    "demo_scheduled": "Demo",
    "qualified_lead": "Qualified",
}


def crm_status_tag(stages: list[str]) -> Optional[str]:
    """Map a company's deal stages → one CRM-status tag using the most advanced
    stage (closed_won → 'CRM: Customer', poc_* → 'CRM: POC', etc.)."""
    ranked = [(_STAGE_RANK[s], s) for s in stages if s in _STAGE_RANK]
    if not ranked:
        return None
    _, top = max(ranked)
    label = _STAGE_TAG.get(top)
    return f"CRM: {label}" if label else None


async def _push_one(client: RecotapClient, session: AsyncSession, company: Company, domain: str, tags: list[str]) -> dict:
    acct = {"domain": domain, "name": company.name, "externalId": str(company.id), "tags": tags}
    data = await client.push_accounts([acct])
    results = data.get("results") or []
    item = results[0] if results else {}
    status = item.get("status")
    rtp_aid = item.get("rtp_aid")
    # POST is insert-only; on a dup it hands back the PUT path with the rtp_aid.
    if status == "failed" and "already exists" in (item.get("error") or ""):
        m = re.search(r"/accounts/([A-Za-z0-9]+)", item.get("error", ""))
        rtp_aid = m.group(1) if m else None
        if rtp_aid:
            await client.update_account(rtp_aid, {"name": company.name, "tags": tags})
            status = "updated"
    row = await _get_or_create_row(session, domain)
    row.rtp_aid = rtp_aid or row.rtp_aid
    row.tags = tags
    row.external_id = str(company.id)
    row.company_id = company.id
    row.pushed_at = datetime.utcnow()
    row.push_status = status
    row.updated_at = datetime.utcnow()
    return {"domain": domain, "name": company.name, "tag": tags[0] if tags else None, "status": status, "rtp_aid": rtp_aid}


async def push_crm_status(
    session: AsyncSession,
    *,
    limit: Optional[int] = None,
    company_ids: Optional[list] = None,
) -> dict:
    """Push CRM deal-stage status to Recotap as account tags. Only accounts with a
    mapped deal stage are pushed (so the tag reflects Customer/POC/etc.). POST
    creates the account; on 'already exists' it captures the rtp_aid and PUTs the
    tags. `limit`/`company_ids` scope a test run."""
    client = RecotapClient()
    if not client.configured():
        return {"configured": 0, "pushed": 0, "results": []}
    deal_rows = (
        await session.execute(select(Deal.company_id, Deal.stage).where(Deal.company_id.is_not(None)))
    ).all()
    stages_by_company: dict = {}
    for cid, stage in deal_rows:
        stages_by_company.setdefault(cid, []).append(stage)
    q = select(Company)
    if company_ids:
        q = q.where(Company.id.in_(company_ids))
    companies = (await session.execute(q)).scalars().all()
    results = []
    pushed = 0
    for company in companies:
        tag = crm_status_tag(stages_by_company.get(company.id, []))
        if not tag:
            continue
        domain = normalize_domain(company.domain)
        if not domain:
            continue
        try:
            outcome = await _push_one(client, session, company, domain, [tag])
        except Exception as exc:  # one account's network/API failure shouldn't abort the batch
            outcome = {"domain": domain, "name": company.name, "tag": tag, "status": "error", "error": str(exc)[:160]}
        results.append(outcome)
        if outcome.get("status") in ("created", "updated"):
            pushed += 1
        if limit and pushed >= limit:
            break
    await session.commit()
    return {"configured": 1, "pushed": pushed, "results": results}
