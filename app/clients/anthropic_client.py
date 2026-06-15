"""Shared Anthropic async client.

Older services instantiate their own ``anthropic.AsyncAnthropic`` — this
module gives Zippy a single, reusable client so we're not spinning up fresh
httpx pools for every agent call.
"""
from __future__ import annotations

import logging
from typing import Optional

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None


def get_anthropic_client() -> Optional[anthropic.AsyncAnthropic]:
    """Return a cached async Anthropic client, or None if no key is set."""
    global _client
    api_key = settings.claude_api_key
    if not api_key:
        return None
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client
