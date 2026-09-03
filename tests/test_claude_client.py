from types import SimpleNamespace

import pytest

from app.clients.claude import ClaudeClient


class _Messages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _client_with_response(response):
    client = ClaudeClient()
    client.api_key = "test-key"
    client.mock = False
    messages = _Messages(response)
    client._get_client = lambda: SimpleNamespace(messages=messages)
    return client, messages


@pytest.mark.asyncio
async def test_complete_uses_anthropic_1_x_supported_parameters():
    client, messages = _client_with_response(
        SimpleNamespace(content=[SimpleNamespace(type="text", text="done")])
    )

    result = await client.complete("system", "user", max_tokens=50)

    assert result == "done"
    assert len(messages.calls) == 1
    assert "temperature" not in messages.calls[0]


@pytest.mark.asyncio
async def test_complete_structured_uses_anthropic_1_x_supported_parameters():
    payload = {"classification": "champion"}
    client, messages = _client_with_response(
        SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name="classify", input=payload)]
        )
    )

    result = await client.complete_structured(
        system="system",
        user="user",
        tool_name="classify",
        tool_description="Classify the record",
        input_schema={"type": "object"},
    )

    assert result == payload
    assert len(messages.calls) == 1
    assert "temperature" not in messages.calls[0]
