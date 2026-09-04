"""Celery's async-task runner must not orphan finalizer-scheduled work.

Production worker logs carried a recurring, untraceable pair:

    Task exception was never retrieved
    future: <Task finished coro=<AsyncClient.aclose()> ...
             exception=RuntimeError('Event loop is closed')>

Cancelling a task runs its finalizers, and a finalizer can schedule MORE work
on the loop — the usual culprit being a vendored httpx client whose ``__del__``
schedules ``aclose()``. A single drain pass closes the loop out from under that
new task. Harmless in itself, but it trains people to ignore worker errors.

Pure-logic tests: no database, Redis, or Celery.
"""
from __future__ import annotations

import asyncio

from app.tasks._runner import run_async_task


def test_returns_the_coroutine_result():
    async def work():
        await asyncio.sleep(0)
        return {"status": "ok", "synced": 3}

    assert run_async_task(work()) == {"status": "ok", "synced": 3}


def test_propagates_an_exception_rather_than_swallowing_it():
    """A task that fails must fail — Celery records SUCCESS otherwise, which is
    this repo's classic 'succeeded while doing nothing' shape."""
    async def boom():
        raise ValueError("task failed")

    try:
        run_async_task(boom())
    except ValueError as exc:
        assert str(exc) == "task failed"
    else:
        raise AssertionError("exception was swallowed")


def test_cancels_a_task_the_coroutine_left_running():
    cancelled = asyncio.Event()

    async def lingering():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def work():
        asyncio.create_task(lingering())
        await asyncio.sleep(0)
        return "done"

    assert run_async_task(work()) == "done"
    assert cancelled.is_set(), "a task left running was not cancelled before the loop closed"


def test_awaits_work_scheduled_by_a_finalizer():
    """The exact production shape: cleanup that only gets scheduled while the
    runner is already shutting down."""
    finalizer_ran = asyncio.Event()

    async def cleanup():
        finalizer_ran.set()

    async def needs_cleanup():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Stands in for httpx's __del__ -> aclose() scheduling.
            asyncio.get_running_loop().create_task(cleanup())
            raise

    async def work():
        asyncio.create_task(needs_cleanup())
        await asyncio.sleep(0)
        return "done"

    assert run_async_task(work()) == "done"
    assert finalizer_ran.is_set(), (
        "work scheduled during shutdown was orphaned — this is what logged "
        "RuntimeError('Event loop is closed') in production"
    )


def test_shuts_down_async_generators():
    closed = asyncio.Event()

    async def gen():
        try:
            yield 1
            yield 2
        finally:
            closed.set()

    async def work():
        agen = gen()
        assert await agen.__anext__() == 1
        return "done"  # generator deliberately left un-exhausted

    assert run_async_task(work()) == "done"
    assert closed.is_set(), "async generator was never finalized"


def test_a_stubborn_finalizer_terminates_instead_of_hanging():
    """A finalizer that reschedules forever must hit the pass limit and exit,
    not spin the worker."""
    async def work():
        async def respawn():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                asyncio.get_running_loop().create_task(respawn())
                raise

        asyncio.create_task(respawn())
        await asyncio.sleep(0)
        return "done"

    # The assertion is simply that this returns at all.
    assert run_async_task(work()) == "done"


def test_each_call_gets_a_fresh_loop():
    """asyncpg binds protocol objects to the first loop that used them, so a
    reused loop breaks the second task run.

    Compare the loop OBJECTS, not their ids: CPython readily hands the freed
    loop's memory address to the next allocation, so an id check passes and
    fails for reasons that have nothing to do with the runner.
    """
    seen = []

    async def capture():
        seen.append(asyncio.get_running_loop())
        return "done"

    assert run_async_task(capture()) == "done"
    first = seen[0]
    assert first.is_closed(), "the runner must close the loop it created"

    # A second run must succeed, which it could not do on the closed loop above.
    assert run_async_task(capture()) == "done"
    assert seen[1] is not first, "the second run reused the first (closed) loop"
    assert seen[1].is_closed()
