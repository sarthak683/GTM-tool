"""Semantic search facade — embed the query, call Qdrant, shape results.

Kept separate from the indexer so the agent layer has a small, stable surface
(``search_knowledge``) regardless of how the index is built.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from app.clients.embeddings import get_embeddings_client
from app.clients.qdrant import SearchHit, get_qdrant_client

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeSnippet:
    """What the agent / UI sees for a single citation."""

    source_id: str
    source_name: str
    source_type: str
    drive_url: str
    mime_type: str
    chunk_index: int
    score: float
    text: str

    def as_citation(self) -> dict:
        # Trim the snippet so it's useful as a tooltip / hover card without
        # dumping the whole chunk into the frontend payload.
        snippet = self.text.strip()
        if len(snippet) > 400:
            snippet = snippet[:400].rstrip() + "…"
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "drive_url": self.drive_url,
            "mime_type": self.mime_type,
            "chunk_index": self.chunk_index,
            "score": round(self.score, 3),
            "snippet": snippet,
        }


async def search_knowledge(
    query: str,
    *,
    user_id: Optional[UUID],
    include_admin: bool = True,
    top_k: Optional[int] = None,
    source_ids: Optional[list[str]] = None,
) -> List[KnowledgeSnippet]:
    """Vector search scoped to the current user (+ admin folder by default)."""
    query = (query or "").strip()
    if not query:
        return []

    embeddings = get_embeddings_client()
    qdrant = get_qdrant_client()

    try:
        vector = await embeddings.embed_one(query)
    except Exception as exc:
        logger.warning("Embedding failed for query: %s", exc)
        return []

    if not vector:
        return []

    hits: List[SearchHit] = await qdrant.search(
        query_vector=vector,
        user_id=str(user_id) if user_id else None,
        include_admin=include_admin,
        top_k=top_k,
        source_ids=source_ids,
    )
    return [
        KnowledgeSnippet(
            source_id=hit.source_id,
            source_name=hit.source_name,
            source_type=hit.source_type,
            drive_url=hit.drive_url,
            mime_type=hit.mime_type,
            chunk_index=hit.chunk_index,
            score=hit.score,
            text=hit.text,
        )
        for hit in hits
    ]
