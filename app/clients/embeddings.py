"""Embeddings client for Zippy's RAG pipeline.

Supports **Azure OpenAI** (preferred when configured) and falls back to the
direct OpenAI API when Azure isn't wired up. The public surface stays tiny:
hand it a list of strings, get back a list of vectors.

We default to text-embedding-3-small (1536 dims) — cheap enough that a full
Drive folder indexes for a few cents, and the quality is comparable to the
big model for retrieval-style workloads.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Sequence

from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# OpenAI accepts up to 2048 inputs / request, but batches of 100 keep memory
# predictable and let us retry a single batch on transient failure without
# redoing everything.
_BATCH_SIZE = 100

# Sit below the ~8191 token context of text-embedding-3-small with margin.
_MAX_INPUT_CHARS = 25000


class EmbeddingsClient:
    """Thin async wrapper around Azure OpenAI (preferred) or OpenAI embeddings."""

    def __init__(self) -> None:
        self._provider = settings.embeddings_provider  # "azure" or "openai"

        if self._provider == "azure":
            if not settings.embeddings_ready:
                logger.warning(
                    "Azure OpenAI embedding config is incomplete — Zippy indexing "
                    "will fail until AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
                    "and AZURE_OPENAI_EMBED_DEPLOYMENT are all set."
                )
            self._client: AsyncOpenAI | AsyncAzureOpenAI = AsyncAzureOpenAI(
                api_key=settings.AZURE_OPENAI_API_KEY or "missing",
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT or "https://missing",
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            # On Azure, the "model" param we pass to embeddings.create() must
            # be the *deployment name* — Azure routes by deployment, not model.
            self._model_or_deployment = settings.AZURE_OPENAI_EMBED_DEPLOYMENT
            self._dims = settings.AZURE_OPENAI_EMBED_DIMS
        else:
            if not settings.OPENAI_API_KEY:
                logger.warning(
                    "No embeddings provider configured — set either AZURE_OPENAI_* "
                    "or OPENAI_API_KEY before indexing."
                )
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY or "missing")
            self._model_or_deployment = settings.OPENAI_EMBED_MODEL
            self._dims = settings.OPENAI_EMBED_DIMS

    @property
    def dims(self) -> int:
        return self._dims

    @property
    def model(self) -> str:
        """Model name (OpenAI) or deployment name (Azure)."""
        return self._model_or_deployment

    @property
    def provider(self) -> str:
        return self._provider

    async def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a list of texts, batching under the hood."""
        if not texts:
            return []
        if not settings.embeddings_ready:
            raise RuntimeError(
                "No embeddings provider is configured. Set AZURE_OPENAI_API_KEY + "
                "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_EMBED_DEPLOYMENT, or "
                "OPENAI_API_KEY, in .env before indexing."
            )

        # Defensive truncation — the API hard-fails on oversize inputs.
        cleaned: List[str] = []
        for text in texts:
            normalised = (text or "").strip()
            if not normalised:
                # The API rejects empty strings; substitute a space so batch
                # indices line up with caller's chunk list.
                normalised = " "
            if len(normalised) > _MAX_INPUT_CHARS:
                normalised = normalised[:_MAX_INPUT_CHARS]
            cleaned.append(normalised)

        vectors: List[List[float]] = []
        for start in range(0, len(cleaned), _BATCH_SIZE):
            batch = cleaned[start : start + _BATCH_SIZE]
            batch_vectors = await self._embed_batch(batch)
            vectors.extend(batch_vectors)
        return vectors

    async def embed_one(self, text: str) -> List[float]:
        """Embed a single query string."""
        result = await self.embed([text])
        return result[0] if result else []

    async def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        """Call the embedding API with one retry on transient errors.

        Azure OpenAI's older API versions don't accept the `dimensions`
        parameter, so we only pass it on direct OpenAI. Azure returns the
        native dims of the deployed model (1536 for text-embedding-3-small
        and ada-002, 3072 for text-embedding-3-large).
        """
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                kwargs = {
                    "model": self._model_or_deployment,
                    "input": batch,
                }
                if self._provider == "openai":
                    # Explicit dims keeps us portable if we swap the model later.
                    kwargs["dimensions"] = self._dims
                response = await self._client.embeddings.create(**kwargs)
                return [item.embedding for item in response.data]
            except Exception as exc:  # pragma: no cover — network path
                last_exc = exc
                logger.warning(
                    "%s embeddings call failed (attempt %s/2): %s",
                    self._provider,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(1.5 * (attempt + 1))
        assert last_exc is not None  # for type checkers
        raise last_exc


_embeddings_client: EmbeddingsClient | None = None


def get_embeddings_client() -> EmbeddingsClient:
    """Module-level singleton so we reuse the underlying httpx pool."""
    global _embeddings_client
    if _embeddings_client is None:
        _embeddings_client = EmbeddingsClient()
    return _embeddings_client


def reset_embeddings_client() -> None:
    """Drop the cached client (useful after settings change in tests / reloads)."""
    global _embeddings_client
    _embeddings_client = None
