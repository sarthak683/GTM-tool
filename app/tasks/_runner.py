"""One definition of how a Celery task runs a coroutine.

Celery workers are synchronous, so every async task in this package has to
drive its own event loop. That helper had been copy-pasted into ten modules
byte-for-byte; three of the copies even carried docstrings pointing at each
other ("same orderly-shutdown helper used by pre_meeting_brief / tldv_sync"),
which is the tell that it wanted to be one function.

The orderly shutdown is the part worth protecting. Without the
``shutdown_asyncgens`` call and the pending-task cancel, a task that returns
while a generator or child task is still live leaves the loop to be closed
underneath it, which surfaces later as "Event loop is closed" or
"coroutine was never awaited" noise that is very hard to trace back here.

Note this creates a FRESH loop each call and never touches the module-level
engine. Celery tasks must also use ``task_session()`` rather than
``AsyncSessionLocal`` for the same reason: asyncpg binds its protocol objects
to whichever loop first used them, so a shared engine breaks on the second
task run.

Four variants in this package deliberately skip the orderly shutdown
(``enrichment._run_async``, ``job_health_signals._run``,
``transcribe_call._run_reaper``, and the inline loop in
``cadence_scheduler``). They are left alone on purpose — switching them would
change shutdown behavior, which is a separate change needing its own testing.
"""
from __future__ import annotations

import asyncio


# Draining once is not enough: cancelling a task runs its finalizers, and a
# finalizer can schedule MORE work on the loop. The common case is a
# third-party httpx client whose __del__ schedules `AsyncClient.aclose()` —
# that task is created during the drain, so a single pass closes the loop out
# from under it and the worker logs "Task exception was never retrieved ...
# RuntimeError('Event loop is closed')". Every one of our own clients uses
# `async with`, so this noise comes from vendored SDKs we do not control.
#
# Bounded because a pathological finalizer could otherwise schedule work
# forever; three passes has been enough for every case seen in production, and
# hitting the limit just restores the old (noisy) behaviour rather than hanging.
_MAX_DRAIN_PASSES = 3


def run_async_task(coro):
    """Run a coroutine inside a fresh event loop with orderly shutdown."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.run_until_complete(loop.shutdown_asyncgens())
        _drain_pending(loop)
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _drain_pending(loop) -> None:
    """Cancel and await outstanding tasks, including ones finalizers create."""
    for _ in range(_MAX_DRAIN_PASSES):
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if not pending:
            # One more turn of the loop lets __del__-scheduled callbacks run,
            # so anything they queue is caught by the next pass instead of
            # being orphaned by loop.close().
            loop.run_until_complete(asyncio.sleep(0))
            if not [task for task in asyncio.all_tasks(loop) if not task.done()]:
                return
            continue
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
