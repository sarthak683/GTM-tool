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
from app.config import settings
from app.models.company import Company
from app.models.deal import Deal
from app.models.recotap import RECOTAP_JOURNEY_STAGES, RecotapAccount, RecotapAccountRead
from app.models.settings import WorkspaceSettings

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


# Domains we must never push to Recotap. Many CRM accounts (esp. ClickUp imports)
# carry placeholder domains like "acme.unknown" or bare numeric IDs like
# "98364117736" — they have no real DNS name. Since POST /accounts is insert-only,
# pushing one would CREATE a junk account in Recotap's tenant. Guard the push so
# only a syntactically real public domain is ever sent.
_PLACEHOLDER_TLDS = {"unknown", "local", "invalid", "test", "example", "internal", "none", "null", "localhost"}
_REAL_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def is_pushable_domain(value: Optional[str]) -> bool:
    """True only for a syntactically valid public domain — guards Beacon → Recotap
    so placeholder/import-artifact domains never create junk accounts in Recotap."""
    d = normalize_domain(value)
    if not d or "." not in d:
        return False
    if d.rsplit(".", 1)[-1] in _PLACEHOLDER_TLDS:
        return False
    return bool(_REAL_DOMAIN_RE.match(d))


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


async def pull_into_db(session: AsyncSession, *, incremental: bool = True) -> dict[str, int]:
    """Pull live Recotap accounts → upsert recotap_accounts by domain.

    Incremental by default: we send Recotap's last ``syncTimestamp`` as ``lastSync``
    so only accounts changed since the previous pull come back, and we persist the
    new marker in workspace_settings.sync_schedule_settings["recotap_last_sync_at"].
    The first-ever pull (no stored marker) or ``incremental=False`` fetches
    everything. Sandbox data is mostly unscored; we keep it and mark source='recotap'.
    """
    client = RecotapClient()
    if not client.configured():
        return {"pulled": 0, "configured": 0}
    settings_row = (
        await session.execute(select(WorkspaceSettings).where(WorkspaceSettings.id == 1))
    ).scalar_one_or_none()
    last_sync = None
    if incremental and settings_row is not None and isinstance(settings_row.sync_schedule_settings, dict):
        last_sync = settings_row.sync_schedule_settings.get("recotap_last_sync_at")
    accounts = await client.get_accounts(limit=100, last_sync=last_sync)
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
    # Persist Recotap's "as of" marker for the next incremental pull. Reassign the
    # dict (not in-place) so SQLAlchemy detects the JSON change.
    if settings_row is not None and client.last_sync_timestamp:
        sched = dict(settings_row.sync_schedule_settings or {})
        sched["recotap_last_sync_at"] = client.last_sync_timestamp
        settings_row.sync_schedule_settings = sched
        session.add(settings_row)
    await session.commit()
    return {
        "pulled": pulled,
        "configured": 1,
        "incremental": bool(last_sync),
        "synced_through": client.last_sync_timestamp,
    }


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
        if (row.pulled_at is not None or row.source == "crm") and not overwrite:
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


def crm_stage_value(stages: list[str]) -> Optional[str]:
    """Bare CRM-stage label for the Recotap custom field (e.g. 'POC', 'Customer'),
    using the most-advanced deal stage — the structured-field counterpart of
    crm_status_tag (which returns the 'CRM: POC' tag string)."""
    ranked = [(_STAGE_RANK[s], s) for s in stages if s in _STAGE_RANK]
    if not ranked:
        return None
    _, top = max(ranked)
    return _STAGE_TAG.get(top)


async def _push_one(
    client: RecotapClient,
    session: AsyncSession,
    company: Company,
    domain: str,
    *,
    tag: Optional[str],
    stage_value: Optional[str],
    dry_run: bool = False,
) -> dict:
    """Upsert one account into Recotap. POST is create-or-update on Recotap's side
    (confirmed 2026-06), so we send once and read the per-item status — no
    error-string parsing / separate PUT. When RECOTAP_CRM_STAGE_FIELD_KEY is set we
    send the stage as a structured custom field; otherwise we fall back to the
    legacy 'CRM: ...' tag. dry_run builds the payload without calling Recotap."""
    field_key = (settings.RECOTAP_CRM_STAGE_FIELD_KEY or "").strip()
    acct: dict = {"domain": domain, "name": company.name, "externalId": str(company.id)}
    if field_key and stage_value:
        acct["customFields"] = {field_key: stage_value}
    else:
        acct["tags"] = [tag] if tag else []

    if dry_run:
        return {"domain": domain, "name": company.name, "status": "dry_run", "payload": acct}

    segment_id = (settings.RECOTAP_PUSH_SEGMENT_ID or "").strip() or None
    data = await client.push_accounts([acct], segment_id=segment_id)
    item = (data.get("results") or [{}])[0]
    status = item.get("status")          # created | updated (upsert) | failed
    rtp_aid = item.get("rtp_aid")
    if status not in ("created", "updated"):
        # Upsert means a duplicate is no longer a failure; if something else fails
        # we just record it (no error-string parsing) so the batch isn't aborted.
        logger.warning("recotap push: status=%s domain=%s error=%s",
                       status, domain, str(item.get("error"))[:200])

    row = await _get_or_create_row(session, domain)
    row.rtp_aid = rtp_aid or row.rtp_aid
    if "tags" in acct:
        row.tags = acct["tags"]
    row.external_id = str(company.id)
    row.company_id = company.id
    row.pushed_at = datetime.utcnow()
    row.push_status = status
    row.updated_at = datetime.utcnow()
    return {"domain": domain, "name": company.name,
            "stage": stage_value or tag, "status": status, "rtp_aid": rtp_aid}


async def push_crm_status(
    session: AsyncSession,
    *,
    limit: Optional[int] = None,
    company_ids: Optional[list] = None,
    dry_run: bool = False,
) -> dict:
    """Push CRM deal-stage status to Recotap for every company with a mapped stage.
    The stage is sent as a custom field when RECOTAP_CRM_STAGE_FIELD_KEY is set,
    else as the legacy 'CRM: ...' tag, via an upsert (POST create-or-update).
    `limit`/`company_ids` scope a test run; `dry_run=True` returns the payloads it
    WOULD send WITHOUT calling Recotap (safe to run anywhere, key not required)."""
    client = RecotapClient()
    if not dry_run and not client.configured():
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
    skipped_invalid = 0
    for company in companies:
        stage_list = stages_by_company.get(company.id, [])
        tag = crm_status_tag(stage_list)
        stage_value = crm_stage_value(stage_list)
        if not tag and not stage_value:
            continue
        domain = normalize_domain(company.domain)
        if not is_pushable_domain(domain):
            # Placeholder/import-artifact domain (e.g. "*.unknown", numeric IDs) —
            # never push it; it would create a junk account in Recotap's tenant.
            skipped_invalid += 1
            continue
        try:
            outcome = await _push_one(
                client, session, company, domain,
                tag=tag, stage_value=stage_value, dry_run=dry_run,
            )
        except Exception as exc:  # one account's network/API failure shouldn't abort the batch
            outcome = {"domain": domain, "name": company.name, "status": "error", "error": str(exc)[:160]}
        results.append(outcome)
        if outcome.get("status") in ("created", "updated", "dry_run"):
            pushed += 1
        if limit and pushed >= limit:
            break
    if not dry_run:
        await session.commit()
    return {
        "configured": int(client.configured()),
        "pushed": pushed,
        "skipped_invalid_domain": skipped_invalid,
        "dry_run": dry_run,
        "field_key": (settings.RECOTAP_CRM_STAGE_FIELD_KEY or "").strip() or None,
        "results": results,
    }


# ── CRM deal stage → Recotap journey stage (for Account Sourcing display) ─────
# Recotap's own journey_stage is intent-derived (ads/web/G2/Bombora) and, on
# prod, empty. Once a deal exists the CRM knows the real position, so we DERIVE a
# journey stage from the deal's most-advanced stage and prefer it over Recotap's.
# Confirmed mapping (2026-06): demo_* / qualified_lead → Aware; poc_* →
# Consideration; negotiation / workshop / msa_review → Opportunity; won → Customer.
_CRM_JOURNEY_BY_STAGE = {
    "demo_scheduled": "Aware", "demo_done": "Aware", "qualified_lead": "Aware",
    "poc_agreed": "Consideration", "poc_wip": "Consideration", "poc_done": "Consideration",
    "commercial_negotiation": "Opportunity", "workshop": "Opportunity", "msa_review": "Opportunity",
    "closed_won": "Customer",
}
# Canonical pipeline order (low → high) so we pick the MOST advanced live stage.
_CRM_STAGE_RANK = {
    s: i for i, s in enumerate([
        "reprospect", "demo_scheduled", "demo_done", "qualified_lead", "poc_agreed",
        "poc_wip", "poc_done", "commercial_negotiation", "workshop", "msa_review", "closed_won",
    ])
}


def crm_journey_stage(stages: list[str]) -> Optional[str]:
    """Map a company's deal stages → a Recotap journey stage using the most
    advanced stage. None when nothing maps (no deal, or only terminal/holding
    stages like closed_lost / not_a_fit / churned / on_hold / cold / nurture)."""
    ranked = [(_CRM_STAGE_RANK[s], s) for s in stages if s in _CRM_STAGE_RANK]
    if not ranked:
        return None
    _, top = max(ranked)
    return _CRM_JOURNEY_BY_STAGE.get(top)


async def sync_crm_journey(session: AsyncSession) -> dict[str, int]:
    """Write each company's deal-derived journey stage onto its recotap_accounts
    row (preferred over Recotap's intent stage; marks source='crm'). Creates a
    row by domain when none exists, so the Buying Journey band reflects real deal
    progress even where Recotap has no data. Clears a stale CRM-derived stage
    when a company no longer has a mappable deal."""
    deal_rows = (
        await session.execute(select(Deal.company_id, Deal.stage).where(Deal.company_id.is_not(None)))
    ).all()
    stages_by_company: dict = {}
    for cid, stage in deal_rows:
        stages_by_company.setdefault(cid, []).append(str(stage or "").strip().lower())
    companies = (await session.execute(select(Company.id, Company.domain, Company.name))).all()
    set_count = 0
    cleared = 0
    for cid, domain_raw, name in companies:
        domain = normalize_domain(domain_raw)
        if not domain:
            continue
        js = crm_journey_stage(stages_by_company.get(cid, []))
        if js is None:
            # Reset only previously CRM-derived rows; leave Recotap/seed rows be.
            existing = (
                await session.execute(select(RecotapAccount).where(RecotapAccount.domain == domain))
            ).scalar_one_or_none()
            if existing is not None and existing.source == "crm":
                existing.journey_stage = None
                existing.updated_at = datetime.utcnow()
                cleared += 1
            continue
        row = await _get_or_create_row(session, domain)
        row.journey_stage = js
        row.source = "crm"
        row.company_id = row.company_id or cid
        row.name = row.name or name
        row.updated_at = datetime.utcnow()
        set_count += 1
    await session.commit()
    return {"crm_journey_set": set_count, "crm_journey_cleared": cleared}
