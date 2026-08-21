"""In-process pub/sub for live board updates.

Beacon ships one backend pod locally and (today) one in production, so we
keep state in-memory — every stage move / create / delete lands on every
SSE consumer within the same process via asyncio.Queue fan-out. When the
backend scales horizontally, swap `_subscribers` for a Redis pub/sub (one
pub per process, each subscribes once) without touching callers.

Heartbeat fires every 25s so an idle connection through a corporate proxy
doesn't get culled silently — clients reconnect on missed heartbeats and
the server drops queues that fail to receive two in a row.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

# Keep this small. The SSE consumer in the frontend should treat this as a
# "something changed, refetch" signal; the board payload lives in the regular
# GET /api/v1/deals/board endpoint. We send ids, not deal objects, to avoid
# the broker lagging behind the canonical store.
EVENT_KEEPALIVE_SECONDS = 25


@dataclass
class BoardEvent:
    """Lightweight change notification.

    `kind` is one of: deal.created, deal.updated, deal.deleted, deal.stage_changed.
    `deal_id` is the UUID of the affected deal, when applicable.
    `stage` is included on stage_changed so the frontend can route the refetch
    to a specific column rather than reloading the whole board.
    """

    kind: str
    deal_id: str | None = None
    stage: str | None = None
    ts: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        payload = json.dumps({"kind": self.kind, "deal_id": self.deal_id, "stage": self.stage, "ts": self.ts})
        return f"event: {self.kind}\ndata: {payload}\n\n"


class BoardBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[BoardEvent | None]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[BoardEvent | None]:
        q: asyncio.Queue[BoardEvent | None] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[BoardEvent | None]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: BoardEvent) -> None:
        """Non-async publish — safe to call from sync code paths.

        If a subscriber's queue is full (slow client), we drop the event for
        that subscriber; the next heartbeat will surface a reconnect, and the
        client refetches the full board on reconnect anyway.
        """
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Best-effort: the consumer will resync on reconnect.
                pass

    def publish_deal_change(self, kind: str, deal_id: str, stage: str | None = None) -> None:
        self.publish(BoardEvent(kind=kind, deal_id=str(deal_id), stage=stage))

    async def stream(self, q: asyncio.Queue[BoardEvent | None]) -> AsyncIterator[bytes]:
        """Async generator yielding SSE-formatted bytes.

        Yields a comment heartbeat every EVENT_KEEPALIVE_SECONDS so a reverse
        proxy doesn't kill the connection, and yields any BoardEvent as soon
        as it lands. Stops on QueueGet timeout via the heartbeat — the
        consumer cancels the iterator and we unsubscribe cleanly.
        """
        # Open the stream immediately so the browser's EventSource resolves
        # before the first event lands.
        yield b": connected\n\n"
        last_keepalive = time.time()
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=EVENT_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                # Heartbeat — both keep the connection alive and signal "still here".
                yield b": keepalive\n\n"
                last_keepalive = time.time()
                continue
            if event is None:
                # Sentinel from close(); done.
                return
            yield event.to_sse().encode("utf-8")


# Module-level singleton — imported by the SSE endpoint and by deal endpoints.
broadcaster = BoardBroadcaster()


async def stop_all_subscribers() -> None:
    """Send the close sentinel to every subscriber; for graceful shutdown."""
    async with broadcaster._lock:
        for q in list(broadcaster._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
