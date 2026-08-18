"""Scheduled Beacon ↔ Recotap sync.

Until this task existed the integration had no schedule at all. The pull ran only
when somebody clicked "Sync Recotap" on Account Sourcing, and the account push
ran only when somebody POSTed to it by hand — in production its last run was
2026-06-26, so Recotap's picture of our CRM was 52 days stale while the Buying
Journey funnel presented itself as live ABM intent. Numbers that are only as
fresh as the last person who thought to press a button are wrong by construction.

Order matters and is the same as the manual refresh endpoint:

1. ``pull_into_db`` — Recotap's account signals in (incremental via lastSync).
2. ``sync_crm_journey`` — derive each account's stage from its most advanced live
   deal into ``crm_journey_stage``. Must follow the pull, which rewrites the
   sibling ``journey_stage`` column.
3. ``push_crm_status`` — Beacon → Recotap account tags / custom field.
4. ``push_deals`` — Beacon → Recotap deals, changed ones only.

Every step is reported with its own counters, and a failure in one does not
abort the rest: a Recotap outage during the push should not also cost us the
pull. Steps that could not run report ``status: "error"`` with the reason rather
than a zero count, because a zero that means "did nothing" and a zero that means
"nothing to do" are the difference between a healthy job and a silent one.
"""
from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)


async def _sync(full: bool) -> dict[str, Any]:
    # task_session(), never the module-level AsyncSessionLocal: the shared engine
    # binds its asyncpg protocols to the first event loop that touches it, and
    # the prefork worker builds a new loop per invocation, so every other run
    # would die with "Task got Future attached to a different loop".
    from app.database import task_session
    from app.clients.recotap import RecotapClient
    from app.services.recotap import (
        pull_into_db,
        push_crm_status,
        register_deal_stages,
        sync_crm_journey,
    )
    from app.services.recotap_activities import push_activities
    from app.services.recotap_deals import push_deals

    if not RecotapClient().configured():
        # No API key — inert by design (sandbox/dev). Say so explicitly instead
        # of returning zeros that look like a successful no-op run.
        return {"status": "skipped", "reason": "recotap_not_configured"}

    out: dict[str, Any] = {"status": "ok"}
    # A fresh session per step so one step's failed transaction cannot poison
    # the next — an exception leaves the session in a rolled-back state that
    # every later statement on it would raise on.
    for key, fn in (
        ("pull", lambda s: pull_into_db(s, incremental=not full)),
        ("crm_journey", sync_crm_journey),
        # Before the account push, because push_crm_status resolves the CRM-stage
        # custom field and we want the stage taxonomy registered alongside it.
        # Returns "already_registered" on every run after the first — Recotap
        # 409s the whole request once the pipeline exists.
        ("deal_stages", register_deal_stages),
        ("push_accounts", push_crm_status),
        ("push_deals", push_deals),
        # Calls + emails, so Recotap can read intent against actual rep effort.
        ("push_activities", lambda s: push_activities(s, dry_run=False)),
    ):
        try:
            async with task_session() as session:
                out[key] = await fn(session)
        except Exception as exc:
            logger.exception("recotap sync: step %s failed", key)
            out[key] = {"status": "error", "error": str(exc)[:300]}
            out["status"] = "partial"
    return out


@celery_app.task(name="app.tasks.recotap.sync_recotap")
def sync_recotap(full: bool = False) -> dict:
    """Nightly Recotap sync: pull signals, derive CRM stages, push accounts and deals.

    ``full=True`` forces a complete re-pull instead of the incremental lastSync
    window. Recotap's lastSync omits deletions, so a periodic full pass is the
    only way a removed account ever leaves our copy — the weekly entry in
    beat_schedule exists for exactly that.
    """
    try:
        return _run_async_task(_sync(full))
    except Exception as exc:
        logger.exception("Recotap sync failed")
        return {"status": "error", "error": str(exc)[:300]}
