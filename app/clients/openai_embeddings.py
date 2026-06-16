"""
OpenAI embeddings client.

Claude is used for reasoning/generation across the CRM, but Anthropic has no
embeddings API — so vector embeddings (semantic search, dedup, clustering,
similarity scoring) go through OpenAI's `text-embedding-3-*` models.

Mock mode: returns zero-vectors when OPENAI_API_KEY is empty, so callers work
offline without crashing.
"""
from __future__ import annotations

import logging
from typing import Sequence

from app.config import settings

logger = logging.getLogger(__name__)

# text-embedding-3-small → 1536 dims, text-embedding-3-large → 3072 dims.
_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _dims() -> int:
    return _MODEL_DIMS.get(settings.OPENAI_EMBED_MODEL, 1536)


def _get_client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class OpenAIEmbeddingsClient:
    def __init__(self) -> None:
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_EMBED_MODEL
        self.mock = not self.api_key

    async def embed(self, text: str) -> list[float]:
        """Embed a single string. Returns a zero-vector in mock mode."""
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of strings in one API call."""
        if self.mock:
            return [[0.0] * _dims() for _ in texts]

        # OpenAI rejects empty strings — swap them for a single space.
        cleaned = [t if t and t.strip() else " " for t in texts]

        try:
            client = _get_client()
            response = await client.embeddings.create(model=self.model, input=cleaned)
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"OpenAI embedding call failed: {e}")
            return [[0.0] * _dims() for _ in texts]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity for two equal-length vectors. Returns 0.0 on mismatch."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


embeddings_client = OpenAIEmbeddingsClient()
