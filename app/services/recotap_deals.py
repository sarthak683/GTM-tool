"""Beacon → Recotap deal push (``POST /api/v1/deals``).

Until now Beacon only pushed *accounts*, encoding the CRM position as a tag
("CRM: POC") because Recotap won't let us set its computed journey stage. That
carried one bit of information per account. Recotap's deals endpoint takes the
whole deal — amount, stage, pipeline, owner, dates, and the accounts it belongs
to — which is what their revenue attribution actually consumes, so the tag is no
longer the only channel.

Two things make this cheap to run nightly:

* ``externalDealId`` is the Beacon deal UUID and Recotap upserts on it, so a
  re-push is an update, never a duplicate. No cursor, no reconciliation.
* Each deal's payload is hashed and stored in ``recotap_deal_pushes``. A run
  sends only what changed, so a normal night pushes a handful of deals out of
  ~690 rather than seven full batches.

Contract: docs/RECOTAP_API.md. HTTP 200 comes back even when every item in the
batch failed, so ``results[]`` is the only source of truth about what landed.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.recotap import RecotapClient
from app.config import settings
from app.models.company import Company
from app.models.deal import Deal
from app.models.recotap import RecotapDealPush
from app.models.user import User
from app.services.deal_stages import get_configured_deal_stages
from app.services.recotap import is_pushable_domain, normalize_domain

logger = logging.getLogger(__name__)


def _iso(value: Any) -> Optional[str]:
    """ISO 8601 with an explicit UTC offset, which is what Recotap documents.

    Every datetime in this database is naive UTC (see models.meeting.to_naive_utc),
    so a bare .isoformat() would hand Recotap a timestamp with no zone and let
    them assume whatever their server runs on. Dates are widened to midnight UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    # datetime.date (close_date_est) — no time component to preserve.
    try:
        return f"{value.isoformat()}T00:00:00Z"
    except AttributeError:
        return None


def build_deal_payload(
    deal: Deal,
    *,
    company: Optional[Company],
    owner: Optional[User],
    stage_labels: dict[str, str],
    closed_stage_ids: set[str],
    currency: str,
) -> dict[str, Any]:
    """One Beacon deal → one Recotap deal object.

    Only ``externalDealId`` and ``name`` are required on their side; every
    optional key is omitted rather than sent as null, so a deal with no owner
    doesn't overwrite an owner Recotap already has.
    """
    payload: dict[str, Any] = {
        # The Beacon UUID. Stable across renames and stage moves, which is what
        # an upsert key has to be — deal names are edited freely.
        "externalDealId": str(deal.id),
        "name": deal.name or f"Deal {deal.id}",
    }

    if deal.value is not None:
        # Decimal is not JSON-serialisable and Recotap types amount as a number.
        payload["amount"] = float(deal.value)

    if deal.stage:
        payload["stageId"] = deal.stage
        # Fall back to the raw id when a stage was created in Settings after the
        # label map was read — better than dropping the label entirely.
        payload["stageLabel"] = stage_labels.get(deal.stage, deal.stage)

    if deal.pipeline_type:
        payload["pipelineId"] = deal.pipeline_type
        payload["pipelineLabel"] = (
            "Deal Pipeline" if deal.pipeline_type == "deal" else deal.pipeline_type.replace("_", " ").title()
        )

    start = _iso(deal.created_at)
    if start:
        payload["startDate"] = start

    # Recotap takes one closedDate for "expected/actual". For a deal sitting in a
    # closed stage the actual close is when it entered that stage; for a live
    # deal it's the rep's estimate. Sending the estimate for a won deal would
    # date the revenue wrong.
    if deal.stage in closed_stage_ids:
        closed = _iso(deal.stage_entered_at) or _iso(deal.close_date_est)
    else:
        closed = _iso(deal.close_date_est)
    if closed:
        payload["closedDate"] = closed

    if owner is not None:
        if owner.name:
            payload["ownerName"] = owner.name
        # Recotap validates the format and fails the item on a bad address, so
        # only send something that at least looks like an email.
        if owner.email and "@" in owner.email:
            payload["ownerEmail"] = owner.email
        payload["ownerId"] = str(owner.id)

    if currency:
        payload["dealCurrencyCode"] = currency

    if company is not None:
        account: dict[str, Any] = {"externalId": str(company.id)}
        if company.name:
            account["name"] = company.name
        domain = normalize_domain(company.domain)
        # Recotap matches the account by domain. A placeholder like
        # "vistex.unknown" can never match, and sending it risks attaching the
        # deal to junk; omitting it leaves the deal unlinked, which their docs
        # call out as the defined behaviour and is the honest outcome.
        if is_pushable_domain(domain):
            account["domain"] = domain
        payload["associatedAccounts"] = [account]

    return payload


def payload_hash(payload: dict[str, Any]) -> str:
    """Stable hash of the exact body we would send. sort_keys so a dict-ordering
    change never looks like a content change; md5 because this is a change
    detector, not a security boundary."""
    return hashlib.md5(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def build_deal_payloads(
    session: AsyncSession,
    *,
    deal_ids: Optional[list[UUID]] = None,
    include_deleted: bool = False,
) -> list[tuple[Deal, dict[str, Any]]]:
    """Payloads for every live deal (optionally narrowed to ``deal_ids``).

    Prospect-pipeline rows are excluded: they are not deals, and pushing 400 of
    them would misstate Recotap's pipeline coverage. Soft-deleted deals are
    excluded too — see ``push_deals`` for why they are not resurrected by a
    later run.
    """
    stmt = select(Deal).where(Deal.pipeline_type == "deal")
    if not include_deleted:
        stmt = stmt.where(Deal.deleted_at.is_(None))
    if deal_ids:
        stmt = stmt.where(Deal.id.in_(deal_ids))
    deals = (await session.execute(stmt)).scalars().all()
    if not deals:
        return []

    stages = await get_configured_deal_stages(session)
    stage_labels = {s["id"]: s["label"] for s in stages}
    closed_stage_ids = {s["id"] for s in stages if s["group"] == "closed"}
    currency = (settings.RECOTAP_DEAL_CURRENCY or "").strip().upper()

    company_ids = {d.company_id for d in deals if d.company_id}
    companies: dict[UUID, Company] = {}
    if company_ids:
        companies = {
            c.id: c
            for c in (
                await session.execute(select(Company).where(Company.id.in_(company_ids)))
            ).scalars().all()
        }
    owner_ids = {d.assigned_to_id for d in deals if d.assigned_to_id}
    owners: dict[UUID, User] = {}
    if owner_ids:
        owners = {
            u.id: u
            for u in (
                await session.execute(select(User).where(User.id.in_(owner_ids)))
            ).scalars().all()
        }

    return [
        (
            deal,
            build_deal_payload(
                deal,
                company=companies.get(deal.company_id) if deal.company_id else None,
                owner=owners.get(deal.assigned_to_id) if deal.assigned_to_id else None,
                stage_labels=stage_labels,
                closed_stage_ids=closed_stage_ids,
                currency=currency,
            ),
        )
        for deal in deals
    ]


async def push_deals(
    session: AsyncSession,
    *,
    deal_ids: Optional[list[UUID]] = None,
    force: bool = False,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push changed deals to Recotap and record what was sent.

    ``force`` re-sends every deal even when its hash is unchanged (use after a
    Recotap-side reset). ``dry_run`` builds and returns the payloads without
    calling Recotap or writing push state — safe anywhere, and it does not need
    an API key, so it doubles as the way to inspect the mapping.

    Soft-deleted deals are not sent and their push state is left in place. There
    is no delete on Recotap's deals API, so a deal removed here simply stops
    being updated there; silently re-pushing it as if live would be worse.
    """
    client = RecotapClient()
    if not dry_run and not client.configured():
        return {"configured": 0, "pushed": 0, "skipped_unchanged": 0, "results": []}

    pairs = await build_deal_payloads(session, deal_ids=deal_ids)
    if not pairs:
        return {
            "configured": int(client.configured()), "pushed": 0,
            "skipped_unchanged": 0, "dry_run": dry_run, "results": [],
        }

    existing = {
        row.deal_id: row
        for row in (
            await session.execute(
                select(RecotapDealPush).where(
                    RecotapDealPush.deal_id.in_([d.id for d, _ in pairs])
                )
            )
        ).scalars().all()
    }

    changed: list[tuple[Deal, dict[str, Any], str]] = []
    skipped_unchanged = 0
    for deal, payload in pairs:
        digest = payload_hash(payload)
        prior = existing.get(deal.id)
        # Re-send anything that previously failed: an unchanged payload that
        # never landed is exactly the case a pure hash check would strand.
        unchanged = (
            prior is not None
            and prior.payload_hash == digest
            and prior.status == "upserted"
        )
        if unchanged and not force:
            skipped_unchanged += 1
            continue
        changed.append((deal, payload, digest))
        if limit and len(changed) >= limit:
            break

    if dry_run:
        return {
            "configured": int(client.configured()),
            "dry_run": True,
            "candidates": len(pairs),
            "would_push": len(changed),
            "skipped_unchanged": skipped_unchanged,
            "currency": (settings.RECOTAP_DEAL_CURRENCY or "").strip().upper(),
            "payloads": [payload for _, payload, _ in changed],
        }

    if not changed:
        return {
            "configured": 1, "pushed": 0, "failed": 0,
            "skipped_unchanged": skipped_unchanged, "results": [],
        }

    body = await client.push_deals([payload for _, payload, _ in changed])
    # Recotap echoes externalDealId per item; index on it rather than assuming
    # the response preserves request order across batches.
    status_by_id = {
        str(r.get("externalDealId")): r for r in body.get("results") or []
    }

    now = datetime.utcnow()
    pushed = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for deal, payload, digest in changed:
        item = status_by_id.get(str(deal.id)) or {}
        # A deal absent from the response was neither confirmed nor refused.
        # Record it as failed so the next run retries rather than treating
        # silence as success.
        status = str(item.get("status") or "missing_from_response")
        error = str(item.get("error") or "")[:500] or None
        row = existing.get(deal.id)
        if row is None:
            row = RecotapDealPush(
                deal_id=deal.id,
                external_deal_id=str(deal.id),
                payload_hash=digest,
            )
            session.add(row)
        row.external_deal_id = str(deal.id)
        row.payload_hash = digest
        row.status = status
        row.error = error
        row.pushed_at = now
        row.updated_at = now
        if status == "upserted":
            pushed += 1
        else:
            failed += 1
            logger.warning(
                "recotap push_deals: deal=%s status=%s error=%s", deal.id, status, error
            )
        results.append({
            "deal_id": str(deal.id), "name": deal.name,
            "stage": deal.stage, "status": status, "error": error,
        })

    await session.commit()
    return {
        "configured": 1,
        "candidates": len(pairs),
        "pushed": pushed,
        "failed": failed,
        "skipped_unchanged": skipped_unchanged,
        "summary": body.get("summary") or {},
        "results": results,
    }
