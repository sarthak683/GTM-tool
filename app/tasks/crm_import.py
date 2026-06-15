"""
Celery task: run the ClickUp CRM import in the background.

The HTTP endpoint returns immediately with a task ID. The frontend polls
/crm-imports/status/{task_id} to check progress and get the final result.
"""
import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.crm_import.run_clickup_import",
    bind=True,
    max_retries=0,
    time_limit=1800,   # hard kill after 30 min
    soft_time_limit=1500,  # soft warning at 25 min
)
def run_clickup_import(
    self,
    replace_existing: bool = True,
    limit: int = 0,
    cache_dir: str = "tmp/clickup_import_cache",
    skip_comments: bool = False,
    skip_subtasks: bool = False,
) -> dict:
    """Run the full ClickUp CRM import as a background Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            _async_import(
                replace_existing=replace_existing,
                limit=limit,
                cache_dir=cache_dir,
                skip_comments=skip_comments,
                skip_subtasks=skip_subtasks,
            )
        )
        return result
    except Exception as exc:
        logger.exception("ClickUp CRM import task failed: %s", exc)
        raise
    finally:
        loop.close()


async def _async_import(
    replace_existing: bool,
    limit: int,
    cache_dir: str,
    skip_comments: bool,
    skip_subtasks: bool,
) -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    from app.services.clickup_import import import_sales_crm_clickup

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            return await import_sales_crm_clickup(
                session,
                replace_existing=replace_existing,
                limit=limit,
                cache_dir=cache_dir,
                skip_comments=skip_comments,
                skip_subtasks=skip_subtasks,
            )
    finally:
        await engine.dispose()
