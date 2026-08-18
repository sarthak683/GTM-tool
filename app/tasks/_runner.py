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


def run_async_task(coro):
    """Run a coroutine inside a fresh event loop with orderly shutdown."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.run_until_complete(loop.shutdown_asyncgens())
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()
