"""A long-lived board stream must not retain its authentication DB connection."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI

from app.api.v1.endpoints import deals
from app.core import dependencies


def test_board_stream_releases_auth_session_before_streaming(monkeypatch):
    async def run():
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=lambda: SimpleNamespace(is_active=True),
        ))
        session.close = AsyncMock()

        async def get_session():
            try:
                yield session
            finally:
                await session.close()

        monkeypatch.setattr(dependencies, "decode_access_token", lambda _: {"sub": "test-user"})
        queue = asyncio.Queue()
        monkeypatch.setattr(deals.broadcaster, "subscribe", AsyncMock(return_value=queue))
        unsubscribe = AsyncMock()
        monkeypatch.setattr(deals.broadcaster, "unsubscribe", unsubscribe)

        async def stream(_queue):
            yield ": connected\n\n"
            await asyncio.Event().wait()

        monkeypatch.setattr(deals.broadcaster, "stream", stream)
        app = FastAPI()
        app.include_router(deals.router)
        app.dependency_overrides[dependencies.get_session] = get_session
        started = asyncio.Event()
        disconnect = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                # Checked while the response is still open, before dependency
                # teardown can hide the connection leak.
                session.execute.assert_awaited_once()
                session.close.assert_awaited_once()
                started.set()
            if message["type"] == "http.response.body" and message.get("body"):
                assert message["body"] == b": connected\n\n"
                disconnect.set()

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        await asyncio.wait_for(app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/deals/board/stream", "raw_path": b"/deals/board/stream",
            "query_string": b"", "headers": [(b"authorization", b"Bearer test")],
            "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }, receive, send), timeout=5)
        assert started.is_set()
        unsubscribe.assert_awaited_once_with(queue)

    asyncio.run(run())


def test_board_stream_requires_auth(client):
    assert client.get("/api/v1/deals/board/stream").status_code == 401
