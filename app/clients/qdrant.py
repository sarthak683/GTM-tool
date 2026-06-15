"""Qdrant client wrapper for Zippy's knowledge base.

We store every chunk with a rich payload so retrieval can filter by user,
admin scope, Drive source, and content type without any extra DB hops.

Point ID strategy
-----------------
Point IDs are UUIDs derived deterministically from `(source_id, chunk_index)`.
That makes re-indexing idempotent — a second pass on the same Drive file
overwrites the existing vectors instead of duplicating them, which matters
because Drive sync runs on every folder change.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeChunk:
    """One indexable chunk, ready for upsert."""

    text: str
    vector: List[float]
    source_id: str          # drive_file_id or an internal identifier
    source_type: str        # "drive_file" | "manual" | "template"
    source_name: str        # human-readable title shown in citations
    chunk_index: int        # position within the source document
    owner_user_id: Optional[str] = None  # None for admin/shared folder
    is_admin: bool = False
    mime_type: str = ""
    drive_url: str = ""
    page: Optional[int] = None
    extra: Optional[dict] = None

    def to_payload(self) -> dict:
        payload: dict[str, Any] = {
            "text": self.text,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "chunk_index": self.chunk_index,
            "owner_user_id": self.owner_user_id,
            "is_admin": self.is_admin,
            "mime_type": self.mime_type,
            "drive_url": self.drive_url,
        }
        if self.page is not None:
            payload["page"] = self.page
        if self.extra:
            payload["extra"] = self.extra
        return payload

    def point_id(self) -> str:
        # Deterministic UUID5 — stable across runs, collision-free for unique
        # (source_id, chunk_index) pairs.
        raw = f"{self.source_id}::{self.chunk_index}".encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return str(uuid.UUID(digest[:32]))


@dataclass
class SearchHit:
    """What a semantic search returns to callers."""

    score: float
    text: str
    source_id: str
    source_name: str
    source_type: str
    drive_url: str
    mime_type: str
    chunk_index: int
    payload: dict


class QdrantKnowledgeClient:
    """High-level wrapper — collection bootstrap, upsert, search, delete."""

    def __init__(self) -> None:
        self._collection = settings.QDRANT_COLLECTION
        # Use the active provider's dims — Azure and OpenAI can be different.
        self._dims = settings.embeddings_dims
        self._client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            prefer_grpc=False,
        )
        self._bootstrapped = False

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self) -> None:
        """Create the collection + payload indexes on first use."""
        if self._bootstrapped:
            return

        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}

        if self._collection not in existing:
            logger.info("Creating Qdrant collection %s (dims=%s)", self._collection, self._dims)
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qmodels.VectorParams(
                    size=self._dims,
                    distance=qmodels.Distance.COSINE,
                ),
            )

        # Payload indexes let us filter by owner/scope at query time without
        # scanning every point. Safe to call repeatedly — Qdrant ignores dupes.
        for field, schema in [
            ("owner_user_id", qmodels.PayloadSchemaType.KEYWORD),
            ("is_admin", qmodels.PayloadSchemaType.BOOL),
            ("source_id", qmodels.PayloadSchemaType.KEYWORD),
            ("source_type", qmodels.PayloadSchemaType.KEYWORD),
        ]:
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception as exc:  # index already exists → fine
                logger.debug("Payload index %s already present: %s", field, exc)

        self._bootstrapped = True

    async def upsert(self, chunks: Sequence[KnowledgeChunk]) -> int:
        """Insert or update a batch of chunks. Returns the count written."""
        if not chunks:
            return 0
        await self.ensure_collection()
        points = [
            qmodels.PointStruct(
                id=chunk.point_id(),
                vector=chunk.vector,
                payload=chunk.to_payload(),
            )
            for chunk in chunks
        ]
        # wait=True keeps the write synchronous — the caller's next query sees
        # the new vectors. Fine at the scale we're operating at.
        await self._client.upsert(
            collection_name=self._collection,
            points=points,
            wait=True,
        )
        return len(points)

    async def delete_by_source(self, source_id: str) -> None:
        """Remove all chunks for a given source (e.g. Drive file was deleted)."""
        await self.ensure_collection()
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="source_id",
                            match=qmodels.MatchValue(value=source_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def delete_by_owner(self, owner_user_id: Optional[str], *, is_admin: bool = False) -> None:
        """Wipe the entire scope for a user or the admin folder — used on folder change."""
        await self.ensure_collection()
        conditions: List[qmodels.FieldCondition] = [
            qmodels.FieldCondition(
                key="is_admin",
                match=qmodels.MatchValue(value=is_admin),
            )
        ]
        if not is_admin:
            conditions.append(
                qmodels.FieldCondition(
                    key="owner_user_id",
                    match=qmodels.MatchValue(value=owner_user_id or ""),
                )
            )
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(must=conditions)
            ),
            wait=True,
        )

    async def search(
        self,
        query_vector: List[float],
        *,
        user_id: Optional[str],
        include_admin: bool = True,
        top_k: Optional[int] = None,
        source_ids: Optional[Iterable[str]] = None,
    ) -> List[SearchHit]:
        """Semantic search scoped to a user (and optionally the admin folder)."""
        await self.ensure_collection()
        k = top_k or settings.ZIPPY_TOP_K

        # Scope filter: the user's own chunks OR (when allowed) the admin ones.
        scope_clauses: List[qmodels.Condition] = []
        if user_id:
            scope_clauses.append(
                qmodels.FieldCondition(
                    key="owner_user_id",
                    match=qmodels.MatchValue(value=user_id),
                )
            )
        if include_admin:
            scope_clauses.append(
                qmodels.FieldCondition(
                    key="is_admin",
                    match=qmodels.MatchValue(value=True),
                )
            )
        if not scope_clauses:
            # No scope to search — caller isn't logged in and admin content is
            # disallowed. Return empty rather than silently leaking everything.
            return []

        filter_ = qmodels.Filter(should=scope_clauses)
        if source_ids:
            filter_ = qmodels.Filter(
                should=scope_clauses,
                must=[
                    qmodels.FieldCondition(
                        key="source_id",
                        match=qmodels.MatchAny(any=list(source_ids)),
                    )
                ],
            )

        response = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=k,
            query_filter=filter_,
            with_payload=True,
        )

        hits: List[SearchHit] = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    score=point.score,
                    text=payload.get("text", ""),
                    source_id=payload.get("source_id", ""),
                    source_name=payload.get("source_name", ""),
                    source_type=payload.get("source_type", ""),
                    drive_url=payload.get("drive_url", ""),
                    mime_type=payload.get("mime_type", ""),
                    chunk_index=payload.get("chunk_index", 0),
                    payload=payload,
                )
            )
        return hits


_qdrant_client: QdrantKnowledgeClient | None = None


def get_qdrant_client() -> QdrantKnowledgeClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantKnowledgeClient()
    return _qdrant_client
