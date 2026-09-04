"""Pub/sub for live board updates.

Fan-out has two layers:

* **In-process** — an ``asyncio.Queue`` per connected SSE client. Every event
  published in this worker lands on every local consumer immediately.
* **Cross-process** — a Redis pub/sub channel bridging the replicas. Production
  runs TWO API replicas behind one Service, so a rep whose stream is pinned to
  pod A would never see a deal moved by a rep on pod B. With the bridge, each
  process publishes to Redis and re-injects what it receives into its own local
  queues.

The bridge is best-effort: if Redis is unreachable the broadcaster degrades to
in-process fan-out (which is all it ever did before) rather than failing the
request that triggered the event. Losing a refetch signal costs a stale board
until the next manual refresh; failing the write would cost the deal edit.

Heartbeat fires every 25s so an idle connection through a corporate proxy
doesn't get culled silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)

# Keep this small. The SSE consumer in the frontend should treat this as a
# "something changed, refetch" signal; the board payload lives in the regular
# GET /api/v1/deals/board endpoint. We send ids, not deal objects, to avoid
# the broker lagging behind the canonical store.
EVENT_KEEPALIVE_SECONDS = 25

# Redis channel every replica publishes to and subscribes on.
BOARD_CHANNEL = "beacon:board-events"

# Identifies THIS process. Redis echoes a published message back to the
# publisher, so without this each replica would fan its own events out locally
# twice — one direct, one round-tripped.
_ORIGIN = uuid.uuid4().hex


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

    def to_payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "deal_id": self.deal_id, "stage": self.stage, "ts": self.ts}

    def to_sse(self) -> str:
        return f"event: {self.kind}\ndata: {json.dumps(self.to_payload())}\n\n"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BoardEvent":
        return cls(
            kind=str(payload.get("kind") or ""),
            deal_id=payload.get("deal_id"),
            stage=payload.get("stage"),
            ts=float(payload.get("ts") or time.time()),
        )


class BoardBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[BoardEvent | None]] = set()
        self._lock = asyncio.Lock()
        # Strong refs to in-flight publish tasks. Without this the event loop
        # holds only a weak reference and can garbage-collect a task mid-await.
        self._pending: set[asyncio.Task[None]] = set()
        self._redis: Any | None = None
        self._bridge_task: asyncio.Task[None] | None = None
        self._redis_unavailable_logged = False

    # ── local subscribers ────────────────────────────────────────────────

    async def subscribe(self) -> asyncio.Queue[BoardEvent | None]:
        q: asyncio.Queue[BoardEvent | None] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[BoardEvent | None]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def _fanout_local(self, event: BoardEvent) -> None:
        """Deliver to this process's SSE consumers.

        If a subscriber's queue is full (slow client), we drop the event for
        that subscriber; the client refetches the full board on reconnect.
        """
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Best-effort: the consumer will resync on reconnect.
                pass

    # ── publishing ───────────────────────────────────────────────────────

    def publish(self, event: BoardEvent) -> None:
        """Non-async publish — safe to call from sync code paths."""
        self._fanout_local(event)
        self._schedule_redis_publish(event)

    def publish_deal_change(self, kind: str, deal_id: str, stage: str | None = None) -> None:
        self.publish(BoardEvent(kind=kind, deal_id=str(deal_id), stage=stage))

    def _schedule_redis_publish(self, event: BoardEvent) -> None:
        """Hand the event to Redis without blocking the caller.

        Every publisher today runs inside a request handler, so there is a
        running loop; if there ever isn't (a sync script, a test), the local
        fan-out above has already happened and cross-process delivery is
        simply skipped.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._publish_redis(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _get_redis(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        try:
            from redis import asyncio as aioredis

            self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception as exc:  # pragma: no cover - import/config failure
            self._log_redis_unavailable(exc)
            return None
        return self._redis

    async def _publish_redis(self, event: BoardEvent) -> None:
        client = await self._get_redis()
        if client is None:
            return
        try:
            await client.publish(
                BOARD_CHANNEL,
                json.dumps({"origin": _ORIGIN, **event.to_payload()}),
            )
        except Exception as exc:
            # A board refetch signal is not worth surfacing to the user.
            self._log_redis_unavailable(exc)

    def _log_redis_unavailable(self, exc: Exception) -> None:
        """Log the first failure at WARNING, later ones at DEBUG.

        A Redis outage would otherwise emit one WARNING per deal edit.
        """
        if not self._redis_unavailable_logged:
            self._redis_unavailable_logged = True
            logger.warning(
                "Board event bridge unavailable, falling back to in-process fan-out "
                "(other replicas will not see these events): %s",
                exc,
            )
        else:
            logger.debug("Board event bridge still unavailable: %s", exc)

    # ── cross-process bridge ─────────────────────────────────────────────

    async def start_bridge(self) -> None:
        """Subscribe to the Redis channel and re-inject peer events locally."""
        if self._bridge_task is not None:
            return
        self._bridge_task = asyncio.create_task(self._bridge_loop())

    async def stop_bridge(self) -> None:
        task, self._bridge_task = self._bridge_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # pragma: no cover - shutdown best effort
                pass
            self._redis = None

    async def _bridge_loop(self) -> None:
        """Reconnecting subscriber.

        Runs for the lifetime of the process. A dropped Redis connection backs
        off and retries rather than silently ending the bridge, which would
        leave the replica permanently isolated.
        """
        delay = 1.0
        while True:
            client = await self._get_redis()
            if client is None:
                await asyncio.sleep(min(delay, 30.0))
                delay = min(delay * 2, 30.0)
                continue
            try:
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(BOARD_CHANNEL)
                delay = 1.0  # connected — reset the backoff
                async for message in pubsub.listen():
                    if not message or message.get("type") != "message":
                        continue
                    self._handle_bridge_message(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_redis_unavailable(exc)
                # Drop the client so the next pass reconnects from scratch.
                self._redis = None
                await asyncio.sleep(min(delay, 30.0))
                delay = min(delay * 2, 30.0)

    def _handle_bridge_message(self, raw: Any) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        # Skip our own echo — publish() already fanned this out locally.
        if payload.get("origin") == _ORIGIN:
            return
        event = BoardEvent.from_payload(payload)
        if not event.kind:
            return
        self._fanout_local(event)

    # ── SSE framing ──────────────────────────────────────────────────────

    async def stream(self, q: asyncio.Queue[BoardEvent | None]) -> AsyncIterator[bytes]:
        """Async generator yielding SSE-formatted bytes.

        Yields a comment heartbeat every EVENT_KEEPALIVE_SECONDS so a reverse
        proxy doesn't kill the connection, and yields any BoardEvent as soon
        as it lands.
        """
        # Open the stream immediately so the client's fetch resolves before the
        # first event lands.
        yield b": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=EVENT_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                # Heartbeat — both keep the connection alive and signal "still here".
                yield b": keepalive\n\n"
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
