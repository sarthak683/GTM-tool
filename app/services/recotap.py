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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.recotap import RecotapClient
from app.config import settings
from app.models.company import Company
from app.models.deal import Deal
from app.models.recotap import RECOTAP_JOURNEY_STAGES, RecotapAccount, RecotapAccountRead
from app.models.settings import WorkspaceSettings
from app.services.deal_stages import get_configured_deal_stages

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


def _engagement_for(score: Optional[int]) -> Optional[str]:
    """Hot/Warm/Cold from rtp_account_score — or None when there is no signal.

    A score of 0 means Recotap has not scored the account yet, NOT that its
    intent is cold. Returning "Cold" for it put 132 signal-less accounts into the
    Cold chip in prod (418 shown, 286 real) and made the Account Sourcing
    engagement counts overstate measured intent by nearly half. An unscored
    account has no engagement level; the summary reports it under `no_intent`.

    The thresholds assume Recotap's documented 0-100 range. Prod carries a few
    scores above 100, which land in Hot — correct, and deliberately not clamped:
    the value is Recotap's, and rescaling it here would invent a second
    definition of the score.
    """
    if score is None or score <= 0:
        return None
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
        # Assigned unconditionally: when an account's score drops back to 0/None
        # the stale label must clear, not linger from the previous pull.
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
        # Preserve rows that carry real pulled data (pull sets pulled_at) or a
        # real CRM-derived stage; only those should be left untouched. Newly-
        # created or seed rows have neither, so they get (re)populated.
        if (row.pulled_at is not None or row.crm_journey_stage is not None) and not overwrite:
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


def effective_journey_stage_sql():
    """THE SQL definition of the displayed journey stage: the CRM-derived stage
    when present, else Recotap's.

    Shared by the list filter and the funnel counts so a tile can never promise a
    number the filter won't reproduce — the Python twin is
    ``effective_journey_stage`` below, and both must stay COALESCE(crm, recotap).
    ``nullif('')`` because an empty string is a missing stage, not a stage.
    """
    return func.coalesce(
        func.nullif(RecotapAccount.crm_journey_stage, ""),
        func.nullif(RecotapAccount.journey_stage, ""),
    )


def effective_journey_stage(row: RecotapAccount) -> tuple[Optional[str], Optional[str]]:
    """(stage, source) for display — the CRM-derived stage when a live deal gives
    us one, else Recotap's intent stage. A deal is direct evidence of where the
    account actually is, so it wins; the source is returned alongside so the UI
    never implies Recotap said something it didn't."""
    if row.crm_journey_stage:
        return row.crm_journey_stage, "crm"
    if row.journey_stage:
        return row.journey_stage, "recotap"
    return None, None


async def signals_by_domain(session: AsyncSession, domains: list[str]) -> dict[str, RecotapAccountRead]:
    """Return {normalized_domain: RecotapAccountRead} for the given domains —
    used to enrich the Account Sourcing list/detail."""
    norm = {normalize_domain(d) for d in domains if d}
    if not norm:
        return {}
    rows = (
        await session.execute(select(RecotapAccount).where(RecotapAccount.domain.in_(norm)))
    ).scalars().all()
    out: dict[str, RecotapAccountRead] = {}
    for r in rows:
        read = RecotapAccountRead.model_validate(r)
        stage, stage_source = effective_journey_stage(r)
        # `journey_stage` on the read model is the EFFECTIVE stage so existing
        # callers keep rendering something; the unmixed values travel alongside.
        read.journey_stage = stage
        read.journey_stage_source = stage_source
        read.recotap_journey_stage = r.journey_stage
        read.crm_journey_stage = r.crm_journey_stage
        out[r.domain] = read
    return out


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


# The distinct values crm_stage_value() can return — these become the
# singleSelection options when the field is created, so a value outside this set
# would be rejected by Recotap.
CRM_STAGE_FIELD_LABEL = "CRM Stage"
CRM_STAGE_FIELD_OPTIONS = ["Qualified", "Demo", "POC", "Negotiation", "Customer"]
_CRM_STAGE_FIELD_SETTING = "recotap_crm_stage_field_key"


async def resolve_crm_stage_field_key(
    session: AsyncSession, client: RecotapClient, *, create: bool = True
) -> Optional[str]:
    """The Recotap custom-field key that holds a company's CRM stage.

    Why this exists: the stage was being pushed as a free-text tag ("CRM: POC")
    because ``RECOTAP_CRM_STAGE_FIELD_KEY`` was never set, and the code's own
    comment said custom fields are "rejected unless pre-defined". Recotap has an
    endpoint for exactly that pre-definition — we were working around a problem
    the API solves. One structured, selectable field beats a tag nobody can
    filter or group by on their side.

    Resolution order, cheapest first:
      1. the env override, if an operator pinned one;
      2. the key cached in workspace settings from a previous run;
      3. a live lookup of Recotap's field list, matched on label;
      4. create it (409 == someone else just did, so re-read the list).

    Returns None if it cannot be resolved, and the caller falls back to tags —
    losing the structure but never the data.
    """
    override = (settings.RECOTAP_CRM_STAGE_FIELD_KEY or "").strip()
    if override:
        return override

    settings_row = (
        await session.execute(select(WorkspaceSettings).where(WorkspaceSettings.id == 1))
    ).scalar_one_or_none()
    cached = None
    if settings_row is not None and isinstance(settings_row.sync_schedule_settings, dict):
        cached = (settings_row.sync_schedule_settings.get(_CRM_STAGE_FIELD_SETTING) or "").strip() or None
    if cached:
        return cached

    def _match(fields: list[dict]) -> Optional[str]:
        for field in fields:
            if str(field.get("label") or "").strip().lower() == CRM_STAGE_FIELD_LABEL.lower():
                return str(field.get("key") or "").strip() or None
        return None

    try:
        key = _match(await client.list_account_custom_fields())
        if key is None and create:
            created = await client.create_account_custom_field(
                label=CRM_STAGE_FIELD_LABEL,
                label_type="singleSelection",
                options=CRM_STAGE_FIELD_OPTIONS,
                description="Furthest CRM deal stage reached, pushed from Beacon.",
            )
            if created:
                key = str(created.get("key") or "").strip() or None
            else:
                # 409 — it exists after all (created concurrently, or by hand
                # under a label that differs only in case). Re-read rather than
                # guessing the generated key.
                key = _match(await client.list_account_custom_fields())
    except Exception as exc:
        logger.warning("recotap: could not resolve the CRM stage custom field: %s", str(exc)[:200])
        return None

    if key and settings_row is not None:
        # New dict, not in-place — plain JSONB has no mutation tracking.
        sched = dict(settings_row.sync_schedule_settings or {})
        sched[_CRM_STAGE_FIELD_SETTING] = key
        settings_row.sync_schedule_settings = sched
        session.add(settings_row)
        await session.commit()
    return key


async def register_deal_stages(session: AsyncSession) -> dict:
    """Register Beacon's pipeline + stage taxonomy with Recotap (one-time).

    Without this, the ``stageId``/``stageLabel`` we send on every deal are just
    strings on their side. Registering them lets Recotap render our real stage
    names instead of bare slugs.

    Create-only and all-or-nothing: if the pipeline id already exists Recotap
    rejects the entire request with 409 and creates nothing. That is the normal
    outcome on every run after the first, so it is reported as
    ``already_registered`` rather than retried.
    """
    client = RecotapClient()
    if not client.configured():
        return {"status": "skipped", "reason": "recotap_not_configured"}

    stages = await get_configured_deal_stages(session)
    if not stages:
        return {"status": "skipped", "reason": "no_configured_stages"}

    pipelines = [{
        "pipelineId": "deal",
        "pipelineLabel": "Deal Pipeline",
        "stages": [{"stageId": s["id"], "stageLabel": s["label"]} for s in stages],
    }]
    try:
        return await client.push_deal_stages(pipelines)
    except Exception as exc:
        logger.warning("recotap: deal-stage registration failed: %s", str(exc)[:200])
        return {"status": "error", "error": str(exc)[:200]}


async def _push_one(
    client: RecotapClient,
    session: AsyncSession,
    company: Company,
    domain: str,
    *,
    tag: Optional[str],
    stage_value: Optional[str],
    field_key: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Upsert one account into Recotap. POST is create-or-update on Recotap's side
    (confirmed 2026-06), so we send once and read the per-item status — no
    error-string parsing / separate PUT. When RECOTAP_CRM_STAGE_FIELD_KEY is set we
    send the stage as a structured custom field; otherwise we fall back to the
    legacy 'CRM: ...' tag. dry_run builds the payload without calling Recotap."""
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
    # Resolved ONCE per run, not per company: it is two HTTP calls at worst and
    # the answer is cached in workspace settings afterwards. None means we could
    # not resolve it and every account falls back to the legacy tag.
    field_key = None
    if not dry_run:
        field_key = await resolve_crm_stage_field_key(session, client)
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
                tag=tag, stage_value=stage_value, field_key=field_key, dry_run=dry_run,
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
        "field_key": field_key,
        # True when the stage went as a structured custom field rather than the
        # legacy free-text tag — the whole point of resolving the field.
        "structured": bool(field_key),
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
    row, into ``crm_journey_stage``.

    It used to write ``journey_stage`` — the column holding Recotap's own
    intent-derived stage — and flip ``source`` to 'crm'. Recotap's value was
    destroyed on every refresh, so a funnel badged "Powered by Recotap" was
    reporting Beacon's deal stages for 94 of 338 scored accounts in prod, and
    every one of the 22 accounts in the "Customer" tile was CRM-derived (Recotap
    reported no Customers at all). The two stages now live in separate columns
    and are counted separately.

    Still creates a row by domain when none exists, so the Buying Journey band
    reflects deal progress even where Recotap has no account — but only for a
    real domain. Without that guard this path created rows for placeholder
    domains like ``vistex.unknown``, which cannot exist in Recotap; five such
    rows in prod double-counted their company in the funnel, since the same
    company also had a row under its real domain.

    Clears a stale CRM stage when a company no longer has a mappable deal.
    """
    deal_rows = (
        await session.execute(
            select(Deal.company_id, Deal.stage).where(
                Deal.company_id.is_not(None),
                # Prospect-pipeline rows and soft-deleted deals are not deal
                # progress; counting them advanced accounts that have no live deal.
                Deal.pipeline_type == "deal",
                Deal.deleted_at.is_(None),
            )
        )
    ).all()
    stages_by_company: dict = {}
    for cid, stage in deal_rows:
        stages_by_company.setdefault(cid, []).append(str(stage or "").strip().lower())
    companies = (
        await session.execute(
            select(Company.id, Company.domain, Company.name).where(Company.deleted_at.is_(None))
        )
    ).all()
    set_count = 0
    cleared = 0
    skipped_invalid = 0
    for cid, domain_raw, name in companies:
        domain = normalize_domain(domain_raw)
        if not domain:
            continue
        js = crm_journey_stage(stages_by_company.get(cid, []))
        if js is None:
            # Clear a previously-derived CRM stage; never touch journey_stage,
            # which belongs to Recotap.
            existing = (
                await session.execute(select(RecotapAccount).where(RecotapAccount.domain == domain))
            ).scalar_one_or_none()
            if existing is not None and existing.crm_journey_stage is not None:
                existing.crm_journey_stage = None
                existing.updated_at = datetime.utcnow()
                cleared += 1
            continue
        if not is_pushable_domain(domain):
            skipped_invalid += 1
            continue
        row = await _get_or_create_row(session, domain)
        row.crm_journey_stage = js
        row.company_id = row.company_id or cid
        row.name = row.name or name
        row.updated_at = datetime.utcnow()
        set_count += 1
    await session.commit()
    return {
        "crm_journey_set": set_count,
        "crm_journey_cleared": cleared,
        "crm_journey_skipped_invalid_domain": skipped_invalid,
    }
