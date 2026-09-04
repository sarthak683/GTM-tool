"""Board-event fan-out: local queues plus the cross-replica Redis bridge.

Production runs two API replicas behind one Service. The broadcaster's queues
are per-process, so without the bridge a rep whose SSE stream is pinned to pod
A never sees a deal moved by a rep on pod B — half of all board changes, on a
feature whose entire purpose is "someone else changed something".

No Redis, database, or Celery needed: the bridge is exercised by feeding it the
message payloads Redis would deliver.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import realtime
from app.services.realtime import BOARD_CHANNEL, BoardBroadcaster, BoardEvent


def test_sse_frame_shape():
    """The wire format the frontend parser depends on."""
    frame = BoardEvent(kind="deal.stage_changed", deal_id="d1", stage="won", ts=1.5).to_sse()
    assert frame.startswith("event: deal.stage_changed\n")
    assert frame.endswith("\n\n"), "frames must be blank-line terminated or the client cannot split them"

    data_line = next(line for line in frame.split("\n") if line.startswith("data:"))
    payload = json.loads(data_line[len("data:"):])
    assert payload == {"kind": "deal.stage_changed", "deal_id": "d1", "stage": "won", "ts": 1.5}


def test_payload_round_trips():
    original = BoardEvent(kind="deal.created", deal_id="d2", stage="new", ts=9.0)
    assert BoardEvent.from_payload(original.to_payload()) == original


@pytest.mark.asyncio
async def test_local_subscribers_receive_published_events():
    bus = BoardBroadcaster()
    q = await bus.subscribe()

    bus.publish_deal_change("deal.updated", "d3")

    event = q.get_nowait()
    assert event.kind == "deal.updated"
    assert event.deal_id == "d3"


@pytest.mark.asyncio
async def test_full_queue_drops_for_that_subscriber_only():
    """A slow client must not block delivery to everyone else."""
    bus = BoardBroadcaster()
    slow: asyncio.Queue = asyncio.Queue(maxsize=1)
    fast = await bus.subscribe()
    async with bus._lock:
        bus._subscribers.add(slow)

    bus.publish_deal_change("deal.updated", "d4")
    bus.publish_deal_change("deal.updated", "d5")  # slow queue is now full

    assert fast.qsize() == 2
    assert slow.qsize() == 1


@pytest.mark.asyncio
async def test_bridge_delivers_a_peer_replica_s_event_locally():
    bus = BoardBroadcaster()
    q = await bus.subscribe()

    # What pod B publishes to Redis and pod A receives.
    bus._handle_bridge_message(
        json.dumps({"origin": "some-other-pod", "kind": "deal.stage_changed", "deal_id": "d6", "stage": "won", "ts": 1.0})
    )

    event = q.get_nowait()
    assert event.kind == "deal.stage_changed"
    assert event.deal_id == "d6"
    assert event.stage == "won"


@pytest.mark.asyncio
async def test_bridge_ignores_this_process_s_own_echo():
    """Redis echoes to the publisher too — without the origin check every
    local event would be delivered twice, refetching the board twice."""
    bus = BoardBroadcaster()
    q = await bus.subscribe()

    bus._handle_bridge_message(
        json.dumps({"origin": realtime._ORIGIN, "kind": "deal.updated", "deal_id": "d7"})
    )

    assert q.empty()


@pytest.mark.asyncio
async def test_bridge_survives_junk_payloads():
    """A malformed message must not kill the subscriber loop."""
    bus = BoardBroadcaster()
    q = await bus.subscribe()

    for junk in (None, "", "not json", "[]", json.dumps({"origin": "x"})):
        bus._handle_bridge_message(junk)

    assert q.empty(), "junk should be dropped, not fanned out"


@pytest.mark.asyncio
async def test_publish_still_works_when_redis_is_down():
    """Losing a refetch signal is acceptable; failing the deal edit is not."""
    bus = BoardBroadcaster()
    q = await bus.subscribe()

    async def boom() -> None:
        raise ConnectionError("redis unreachable")

    bus._get_redis = boom  # type: ignore[assignment]

    bus.publish_deal_change("deal.created", "d8")
    await asyncio.sleep(0)  # let the scheduled publish task run

    assert q.get_nowait().deal_id == "d8", "local fan-out must survive a Redis outage"


@pytest.mark.asyncio
async def test_redis_outage_is_logged_once_not_per_event():
    """One WARNING per deal edit during an outage would drown the log."""
    bus = BoardBroadcaster()
    bus._log_redis_unavailable(ConnectionError("first"))
    assert bus._redis_unavailable_logged is True

    # Subsequent failures take the DEBUG path; the flag stays set.
    bus._log_redis_unavailable(ConnectionError("second"))
    assert bus._redis_unavailable_logged is True


@pytest.mark.asyncio
async def test_stream_opens_immediately_and_heartbeats():
    """The client's fetch must resolve before the first event lands."""
    bus = BoardBroadcaster()
    q = await bus.subscribe()

    agen = bus.stream(q)
    assert await agen.__anext__() == b": connected\n\n"

    bus.publish_deal_change("deal.deleted", "d9")
    frame = await agen.__anext__()
    assert frame.startswith(b"event: deal.deleted\n")

    await agen.aclose()


def test_channel_name_is_shared_by_every_replica():
    """Replicas must agree on the channel or the bridge silently does nothing."""
    assert BOARD_CHANNEL == "beacon:board-events"
