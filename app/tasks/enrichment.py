"""
Celery task: enrich a company asynchronously.

Each task invocation creates its own SQLAlchemy engine + disposes it
before the event loop closes — this avoids asyncpg's "event loop is closed"
error on Windows when the global engine's connection pool outlives the loop.
"""
import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable, TypeVar
from uuid import UUID

from app.celery_app import celery_app

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _run_async(coro: Awaitable[T]) -> T:
    """Run an async coroutine on a fresh event loop for Celery sync tasks."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="app.tasks.enrichment.enrich_company_task",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def enrich_company_task(self, company_id: str) -> dict:
    """Enrich a company by ID. Retries once on failure.

    The legacy orchestrator (enrich_company) has no per-source cache, so every
    retry re-hits the full paid Apollo/Hunter/BuiltWith/News waterfall. Capped
    at a single retry (was 3) so a transient failure can re-bill the waterfall
    at most twice instead of up to four times.
    """
    try:
        _run_async(_async_enrich(UUID(company_id)))
        return {"status": "completed", "company_id": company_id}
    except Exception as exc:
        logger.error(f"Enrichment task failed for {company_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_enrich(company_id: UUID) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    from app.services.enrichment_orchestrator import enrich_company_by_id

    # Fresh engine per task — avoids stale connection pool across event loops
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SessionLocal() as session:
            company = await enrich_company_by_id(company_id, session)
            if company:
                logger.info(
                    f"Enrichment complete: {company.domain} "
                    f"— ICP {company.icp_score} ({company.icp_tier})"
                )
    finally:
        await engine.dispose()


# ── Account Sourcing Tasks ─────────────────────────────────────────────────────

def _make_session():
    """Create a fresh engine + session for a Celery task (avoids stale pools)."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.config import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, SessionLocal


async def _run_with_fresh_session(handler: Callable) -> None:
    """Run an async handler with a short-lived engine/session pair."""
    engine, SessionLocal = _make_session()
    try:
        async with SessionLocal() as session:
            await handler(session)
    finally:
        await engine.dispose()


@celery_app.task(
    name="app.tasks.enrichment.enrich_batch_task",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def enrich_batch_task(self, batch_id: str) -> dict:
    """Process all companies in a sourcing batch through tiered enrichment."""
    try:
        # Celery tasks are synchronous entrypoints, so each task runs an isolated
        # event loop to execute async pipeline code in the worker process.
        _run_async(_async_enrich_batch(UUID(batch_id)))
        return {"status": "completed", "batch_id": batch_id}
    except Exception as exc:
        logger.error(f"Batch enrichment task failed for {batch_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.enrichment.process_sourcing_upload_task",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
)
def process_sourcing_upload_task(self, batch_id: str, rows: list[dict], admin_payload: dict) -> dict:
    """Import uploaded workbook rows into account sourcing in the worker."""
    try:
        _run_async(_async_process_sourcing_upload(UUID(batch_id), rows, admin_payload))
        return {"status": "completed", "batch_id": batch_id}
    except Exception as exc:
        logger.error(f"Sourcing upload task failed for {batch_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_process_sourcing_upload(batch_id: UUID, rows: list[dict], admin_payload: dict) -> None:
    from app.models.sourcing_batch import SourcingBatch
    from app.api.v1.endpoints.account_sourcing import _process_uploaded_rows, _queue_batch_enrichment

    engine, SessionLocal = _make_session()
    try:
        async with SessionLocal() as session:
            upload_batch = await session.get(SourcingBatch, batch_id)
            if not upload_batch:
                return
            await _process_uploaded_rows(session, upload_batch, rows, admin_payload)
            final_batch = await session.get(SourcingBatch, batch_id)
            if not final_batch:
                return
            if not bool((final_batch.meta or {}).get("requires_confirmation")):
                await _queue_batch_enrichment(session, final_batch)
    finally:
        await engine.dispose()


async def _async_enrich_batch(batch_id: UUID) -> None:
    from sqlmodel import select

    from app.models.company import Company
    from app.models.sourcing_batch import SourcingBatch
    from app.services.account_sourcing import enrich_company_tiered

    engine, SessionLocal = _make_session()
    try:
        async with SessionLocal() as session:
            batch = await session.get(SourcingBatch, batch_id)
            if not batch:
                return

            batch.status = "processing"
            meta = dict(batch.meta or {})
            meta["current_stage"] = "research_running"
            meta["progress_message"] = "Research started"
            batch.meta = meta
            batch.updated_at = datetime.utcnow()
            session.add(batch)
            await session.commit()

            result = await session.execute(
                select(Company.id).where(Company.sourcing_batch_id == batch_id)
            )
            company_ids = list(result.scalars().all())

        # Keep concurrency bounded so the worker does not overwhelm external APIs.
        semaphore = asyncio.Semaphore(3)
        progress_lock = asyncio.Lock()
        processed = 0
        failed = 0

        async def enrich_one(company_id: UUID) -> None:
            nonlocal processed, failed
            error_payload = None

            async with semaphore:
                try:
                    async with SessionLocal() as company_session:
                        await enrich_company_tiered(company_id, company_session, force_paid_refresh=False)
                except Exception as exc:
                    logger.error(f"Batch enrichment failed for {company_id}: {exc}")
                    error_payload = {"company_id": str(company_id), "error": str(exc)}

                async with progress_lock:
                    # Serialize progress updates so counters and error_log writes
                    # stay consistent for the polling UI.
                    processed += 1
                    if error_payload:
                        failed += 1
                    async with SessionLocal() as progress_session:
                        progress_batch = await progress_session.get(SourcingBatch, batch_id)
                        if not progress_batch:
                            return
                        progress_batch.processed_rows = processed
                        progress_batch.failed_rows = failed
                        progress_meta = dict(progress_batch.meta or {})
                        progress_meta["current_stage"] = "research_running"
                        progress_meta["progress_message"] = f"Processed {processed} of {len(company_ids)} accounts"
                        progress_batch.meta = progress_meta
                        if error_payload:
                            existing_errors = list(progress_batch.error_log or [])
                            existing_errors.append(error_payload)
                            progress_batch.error_log = existing_errors
                        progress_batch.updated_at = datetime.utcnow()
                        progress_session.add(progress_batch)
                        await progress_session.commit()

        await asyncio.gather(*(enrich_one(company_id) for company_id in company_ids))

        async with SessionLocal() as session:
            batch = await session.get(SourcingBatch, batch_id)
            if batch:
                batch.failed_rows = failed
                batch.status = "failed" if failed == len(company_ids) and company_ids else "completed"
                meta = dict(batch.meta or {})
                meta["current_stage"] = "completed" if batch.status == "completed" else "failed"
                meta["progress_message"] = (
                    "Research complete" if batch.status == "completed" else "Research failed"
                )
                batch.meta = meta
                batch.updated_at = datetime.utcnow()
                session.add(batch)
                await session.commit()
                logger.info(f"Batch {batch_id} complete: {batch.processed_rows}/{batch.total_rows}")
    finally:
        await engine.dispose()


@celery_app.task(
    name="app.tasks.enrichment.re_enrich_company_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def re_enrich_company_task(self, company_id: str) -> dict:
    """Re-enrich a single company through the tiered pipeline."""
    try:
        _run_async(_async_re_enrich_company(UUID(company_id)))
        return {"status": "completed", "company_id": company_id}
    except Exception as exc:
        logger.error(f"Re-enrich task failed for {company_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_re_enrich_company(company_id: UUID) -> None:
    from app.services.account_sourcing import re_enrich_company
    await _run_with_fresh_session(lambda session: re_enrich_company(company_id, session))


@celery_app.task(
    name="app.tasks.enrichment.re_enrich_contact_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def re_enrich_contact_task(self, contact_id: str) -> dict:
    """Re-enrich a single contact."""
    try:
        _run_async(_async_re_enrich_contact(UUID(contact_id)))
        return {"status": "completed", "contact_id": contact_id}
    except Exception as exc:
        logger.error(f"Contact re-enrich failed for {contact_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_re_enrich_contact(contact_id: UUID) -> None:
    from app.services.account_sourcing import re_enrich_contact_service
    await _run_with_fresh_session(lambda session: re_enrich_contact_service(contact_id, session))


# ── ICP Intelligence Pipeline Tasks ──────────────────────────────────────────

@celery_app.task(
    name="app.tasks.enrichment.icp_research_batch_task",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def icp_research_batch_task(self, batch_id: str) -> dict:
    """
    Run the full ICP intelligence pipeline for all companies in a batch.
    This replaces the standard enrichment for 'minimal' uploads (company names only).
    Each company goes through: web research → Apollo → Claude ICP analysis.
    """
    try:
        _run_async(_async_icp_research_batch(UUID(batch_id)))
        return {"status": "completed", "batch_id": batch_id}
    except Exception as exc:
        logger.error(f"ICP research batch task failed for {batch_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_icp_research_batch(batch_id: UUID) -> None:
    from sqlmodel import select
    from app.models.company import Company
    from app.models.sourcing_batch import SourcingBatch
    from app.services.icp_intelligence import research_company_and_update

    engine, SessionLocal = _make_session()
    try:
        async with SessionLocal() as session:
            batch = await session.get(SourcingBatch, batch_id)
            if not batch:
                return

            batch.status = "processing"
            meta = dict(batch.meta or {})
            meta["current_stage"] = "research_running"
            meta["progress_message"] = "Research started"
            batch.meta = meta
            batch.updated_at = datetime.utcnow()
            session.add(batch)
            await session.commit()

            result = await session.execute(
                select(Company.id).where(Company.sourcing_batch_id == batch_id)
            )
            company_ids = list(result.scalars().all())

        # Process companies with concurrency limit (2 at a time — each does
        # many web requests + a Claude API call, so keep it conservative)
        semaphore = asyncio.Semaphore(2)
        progress_lock = asyncio.Lock()
        processed = 0
        failed = 0

        async def research_one(company_id: UUID) -> None:
            nonlocal processed, failed
            error_payload = None

            async with semaphore:
                try:
                    async with SessionLocal() as company_session:
                        # force=False: this batch task is max_retries=1 and
                        # restarts from row 0, so the freshness guard inside
                        # research_company_and_update lets a retry skip the
                        # companies that already succeeded (fresh icp_analysis)
                        # and only re-bill the ones that previously failed.
                        await research_company_and_update(
                            company_id, company_session, force=False
                        )
                except Exception as exc:
                    import traceback
                    logger.error(f"ICP research failed for {company_id}: {exc}\n{traceback.format_exc()}")
                    error_payload = {"company_id": str(company_id), "error": str(exc)}

                async with progress_lock:
                    # Persist progress after each company so batch status endpoints
                    # can reflect work in near real time.
                    processed += 1
                    if error_payload:
                        failed += 1
                    async with SessionLocal() as progress_session:
                        progress_batch = await progress_session.get(SourcingBatch, batch_id)
                        if not progress_batch:
                            return
                        progress_batch.processed_rows = processed
                        progress_batch.failed_rows = failed
                        progress_meta = dict(progress_batch.meta or {})
                        progress_meta["current_stage"] = "research_running"
                        progress_meta["progress_message"] = f"Processed {processed} of {len(company_ids)} accounts"
                        progress_batch.meta = progress_meta
                        if error_payload:
                            existing_errors = list(progress_batch.error_log or [])
                            existing_errors.append(error_payload)
                            progress_batch.error_log = existing_errors
                        progress_batch.updated_at = datetime.utcnow()
                        progress_session.add(progress_batch)
                        await progress_session.commit()

        await asyncio.gather(*(research_one(cid) for cid in company_ids))

        async with SessionLocal() as session:
            batch = await session.get(SourcingBatch, batch_id)
            if batch:
                batch.failed_rows = failed
                batch.status = "failed" if failed == len(company_ids) and company_ids else "completed"
                meta = dict(batch.meta or {})
                meta["current_stage"] = "completed" if batch.status == "completed" else "failed"
                meta["progress_message"] = "Research complete" if batch.status == "completed" else "Research failed"
                batch.meta = meta
                batch.updated_at = datetime.utcnow()
                session.add(batch)
                await session.commit()
                logger.info(
                    f"ICP research batch {batch_id} complete: "
                    f"{batch.processed_rows}/{batch.total_rows} "
                    f"({failed} failed)"
                )
    finally:
        await engine.dispose()


@celery_app.task(
    name="app.tasks.enrichment.icp_research_free_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def icp_research_free_task(self, company_id: str) -> dict:
    """Run ICP research using only free data sources (no Apollo/Hunter credits)."""
    try:
        _run_async(_async_icp_research_free(UUID(company_id)))
        return {"status": "completed", "company_id": company_id}
    except Exception as exc:
        logger.error(f"Free ICP research task failed for {company_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_icp_research_free(company_id: UUID) -> None:
    from app.services.icp_intelligence import research_company_and_update_free
    await _run_with_fresh_session(lambda session: research_company_and_update_free(company_id, session))


@celery_app.task(
    name="app.tasks.enrichment.icp_research_single_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def icp_research_single_task(self, company_id: str) -> dict:
    """Run ICP intelligence pipeline for a single company."""
    try:
        _run_async(_async_icp_research_single(UUID(company_id)))
        return {"status": "completed", "company_id": company_id}
    except Exception as exc:
        logger.error(f"ICP research task failed for {company_id}: {exc}")
        raise self.retry(exc=exc)


async def _async_icp_research_single(company_id: UUID) -> None:
    # This single-company task backs the manual re-enrich / ICP-research /
    # refresh buttons, so force a re-run past the freshness guard. The batch
    # path (_async_icp_research_batch) deliberately leaves force defaulting to
    # False so a retry skips already-enriched companies.
    from app.services.icp_intelligence import research_company_and_update
    await _run_with_fresh_session(
        lambda session: research_company_and_update(company_id, session, force=True)
    )
