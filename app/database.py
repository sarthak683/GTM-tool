from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSON, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from app.config import settings
from app.services.text_sanitize import sanitize_json_value, sanitize_text

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def task_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Per-Celery-task session backed by a *fresh* AsyncEngine with NullPool.

    Why this exists
    ---------------
    The module-level `engine`/`AsyncSessionLocal` above are bound — at the
    asyncpg protocol layer — to whichever event loop happens to be active
    the first time they get used. Celery's prefork worker spawns a fresh
    asyncio loop per task invocation (`_run_async_task` helpers), so by the
    second task the engine's cached connection protocols belong to a dead
    loop and the next DB call raises:

        RuntimeError: Task got Future attached to a different loop

    Calling `engine.dispose()` after each task only releases pool slots; it
    does *not* drop the underlying protocol objects in time, so the failure
    keeps reappearing on every other invocation (we've seen the exact
    success/fail/success/fail cadence in prod worker logs).

    The robust fix is to skip the shared engine entirely from Celery and
    spin up a one-shot engine per task with `poolclass=NullPool` so no
    connection ever survives across loops. The cost is one extra TCP +
    auth handshake per task invocation — for a 15-minute beat schedule
    that's negligible, and it makes the worker provably loop-safe.
    """
    local_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,                # tasks shouldn't echo, regardless of dev/prod
        poolclass=NullPool,        # never reuse connections across tasks
        pool_pre_ping=False,       # irrelevant with NullPool
    )
    local_factory = sessionmaker(local_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with local_factory() as session:
            yield session
    finally:
        # `dispose()` must run *before* the loop closes, otherwise the
        # asyncpg pool's background tasks (e.g. terminate-handler) get
        # orphaned. Wrap in try/except so a dispose error never masks a
        # real exception from the task body.
        try:
            await local_engine.dispose()
        except Exception:  # pragma: no cover - defensive
            pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def _sanitize_on_flush(sess, flush_context, instances):
    """Strip characters asyncpg can't store (NUL bytes, lone surrogates) before flush.

    Runs on every add/dirty model instance regardless of the caller, so scraper
    output, AI output, and user input all land clean. Walks JSON/JSONB columns
    recursively and top-level string columns shallowly.
    """
    for obj in list(sess.new) + list(sess.dirty):
        mapper = getattr(obj, "__mapper__", None)
        if mapper is None:
            continue
        for column in mapper.columns:
            value = getattr(obj, column.key, None)
            if value is None:
                continue
            col_type = column.type
            if isinstance(col_type, (JSON, JSONB)):
                cleaned = sanitize_json_value(value)
                if cleaned is not value:
                    setattr(obj, column.key, cleaned)
            elif isinstance(value, str):
                cleaned = sanitize_text(value)
                if cleaned != value:
                    setattr(obj, column.key, cleaned)


# Register on the base Session class so it fires for both sync sessions and the
# sync Session that AsyncSession delegates to under the hood.
event.listen(Session, "before_flush", _sanitize_on_flush)
