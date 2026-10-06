"""Request-owned SDK cleanup must finish before a Celery loop is closed."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients.claude import ClaudeClient
from app.clients.lifecycle import closing_client
from app.clients.openai_embeddings import OpenAIEmbeddingsClient
from app.tasks._runner import run_async_task


@pytest.mark.parametrize("failure", [False, True])
def test_owned_sdk_closes_on_each_fresh_worker_loop(failure):
    loops = []
    for _ in range(2):
        async def close():
            loop = asyncio.get_running_loop()
            assert not loop.is_closed()
            loops.append(loop)
        sdk = SimpleNamespace(close=AsyncMock(side_effect=close))

        async def operation(sdk=sdk):
            async with closing_client(sdk):
                if failure:
                    raise RuntimeError("API failed")
                return "done"
        if failure:
            with pytest.raises(RuntimeError, match="API failed"):
                run_async_task(operation())
        else:
            assert run_async_task(operation()) == "done"
        sdk.close.assert_awaited_once()
    assert loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in loops)


@pytest.mark.asyncio
async def test_owned_sdk_closes_before_cancellation_propagates():
    started = asyncio.Event()
    sdk = SimpleNamespace(close=AsyncMock())
    async def operation():
        async with closing_client(sdk):
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(operation())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sdk.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_claude_completion_closes_its_sdk(monkeypatch, failure):
    sdk = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(
            side_effect=RuntimeError("API failed") if failure else None,
            return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="done")]),
        )),
        close=AsyncMock(),
    )
    client = ClaudeClient()
    client.mock = False
    monkeypatch.setattr(client, "_get_client", lambda: sdk)
    assert await client.complete("system", "user") == (None if failure else "done")
    sdk.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_embeddings_close_the_sdk_with_fallback_preserved(monkeypatch, failure):
    sdk = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(
            side_effect=RuntimeError("API failed") if failure else None,
            return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 2.0])]),
        )),
        close=AsyncMock(),
    )
    monkeypatch.setattr("app.clients.openai_embeddings._get_client", lambda: sdk)
    client = OpenAIEmbeddingsClient()
    client.mock = False
    result = await client.embed_batch(["example"])
    assert result == [[1.0, 2.0]] if not failure else result == [[0.0] * 1536]
    sdk.close.assert_awaited_once()


@pytest.mark.parametrize("failure", [False, True])
def test_actual_anthropic_sdk_is_closed_before_each_worker_loop_ends(monkeypatch, failure):
    import anthropic
    import httpx2 as httpx

    clients = []
    def new_sdk():
        def response(_request):
            if failure:
                return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "fixture failure"}})
            return httpx.Response(200, json={
                "id": "msg_fixture", "type": "message", "role": "assistant", "model": "fixture",
                "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        sdk = anthropic.AsyncAnthropic(api_key="fixture-only", max_retries=0,
                                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
        clients.append(sdk)
        return sdk
    wrapper = ClaudeClient()
    wrapper.mock = False
    monkeypatch.setattr(wrapper, "_get_client", new_sdk)
    for _ in range(2):
        assert run_async_task(wrapper.complete("system", "user")) == (None if failure else "done")
        assert clients[-1].is_closed()
    assert clients[0] is not clients[1]
