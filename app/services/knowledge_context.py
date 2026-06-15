"""
Knowledge Base context injection for AI prompts.

Retrieval strategy:
  1. Hard filter on `modules` tag — keeps irrelevant categories out.
  2. If a `query` is supplied AND chunks have embeddings, rank chunks by
     cosine similarity against the query embedding. This is the semantic
     RAG path.
  3. Otherwise fall back to the legacy recency-based whole-doc path so
     nothing breaks in mock mode or for un-embedded legacy rows.

The ranker runs in-process over JSONB-stored vectors. That scales to a
few thousand chunks easily; past that, move to pgvector + an ANN index.

Usage:
    kb_context = await get_knowledge_context(
        session, "pre_meeting", query=f"{company.name} {company.industry}"
    )
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.openai_embeddings import cosine_similarity, embeddings_client

logger = logging.getLogger(__name__)

_CATEGORY_LABELS = {
    "roi_template": "ROI Framework",
    "case_study": "Customer Case Study",
    "competitive_intel": "Competitive Intelligence",
    "product_info": "Product Information",
    "pricing": "Pricing Guide",
    "objection_handling": "Objection Handling",
    "email_template": "Email Template",
    "playbook": "Sales Playbook",
    "other": "Reference Material",
}


async def get_knowledge_context(
    session: AsyncSession,
    module: str,
    *,
    query: Optional[str] = None,
    limit: int = 5,
    max_chars_per_resource: int = 800,
    max_total_chars: int = 3000,
) -> str:
    """
    Return a prompt-ready KB snippet block.

    - `query`: optional free-text describing the current situation (e.g.
      "Acme Corp — HealthTech CFO — WalkMe deployment"). When present, top
      chunks are selected by semantic similarity across all module-matching
      resources.
    - `limit`: max number of snippets returned.
    """
    try:
        from app.repositories.sales_resource import SalesResourceRepository

        repo = SalesResourceRepository(session)
        resources = await repo.search(module=module, active_only=True)
        if not resources:
            return ""

        # ── Semantic path ────────────────────────────────────────────────────
        if query and not embeddings_client.mock:
            ranked = await _rank_chunks_semantic(resources, query, top_k=limit)
            if ranked:
                return _format_snippets(
                    ranked, max_chars_per_resource, max_total_chars
                )
            # Fall through to legacy path if no embedded chunks available.

        # ── Legacy path: recency-ordered whole-doc snippets ─────────────────
        resources.sort(key=lambda r: r.updated_at, reverse=True)
        legacy = [
            {
                "title": r.title,
                "category": r.category,
                "text": r.content,
                "score": None,
            }
            for r in resources[:limit]
        ]
        return _format_snippets(legacy, max_chars_per_resource, max_total_chars)

    except Exception as e:
        logger.warning(f"Knowledge context fetch failed for module={module}: {e}")
        return ""


async def _rank_chunks_semantic(
    resources: list, query: str, *, top_k: int
) -> list[dict]:
    """Embed query, score every chunk, return top_k across all resources."""
    query_vec = await embeddings_client.embed(query)

    scored: list[tuple[float, dict]] = []
    for r in resources:
        for chunk in (r.chunks or []):
            emb = chunk.get("embedding")
            text = chunk.get("text")
            if not emb or not text:
                continue
            score = cosine_similarity(query_vec, emb)
            scored.append(
                (
                    score,
                    {
                        "title": r.title,
                        "category": r.category,
                        "text": text,
                        "score": score,
                    },
                )
            )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def _format_snippets(
    snippets: list[dict], max_chars_per_resource: int, max_total_chars: int
) -> str:
    if not snippets:
        return ""

    lines = ["\n\n--- SALES KNOWLEDGE BASE (internal resources) ---"]
    total = 0

    for s in snippets:
        label = _CATEGORY_LABELS.get(s["category"], s["category"])
        body = s["text"][:max_chars_per_resource]
        if len(s["text"]) > max_chars_per_resource:
            body += "..."
        score_tag = f" (relevance {s['score']:.2f})" if s.get("score") is not None else ""
        block = f"\n[{label}] {s['title']}{score_tag}\n{body}\n"
        if total + len(block) > max_total_chars:
            break
        lines.append(block)
        total += len(block)

    lines.append("--- END KNOWLEDGE BASE ---\n")
    return "\n".join(lines)


# ── Write-side helper: embed a resource's content into chunks ──────────────────

async def build_chunks_for_resource(
    title: str, description: Optional[str], content: str
) -> list[dict]:
    """
    Split `content` into chunks, embed each, and return the JSONB-ready list.
    Title/description are prepended so context is preserved in every chunk's
    embedding (a chunk talking about "monthly savings" without the doc title
    "ROI calculator for PE-backed HealthTech" embeds poorly on its own).
    """
    from app.services.chunking import chunk_text

    if embeddings_client.mock:
        # Still store chunk text so future key-addition can backfill embeddings.
        return [{"text": c, "embedding": []} for c in chunk_text(content)]

    texts = chunk_text(content)
    if not texts:
        return []

    prefix = (title or "").strip()
    if description:
        prefix = f"{prefix} — {description.strip()}" if prefix else description.strip()

    embed_inputs = [f"{prefix}\n\n{t}" if prefix else t for t in texts]
    vectors = await embeddings_client.embed_batch(embed_inputs)
    return [{"text": t, "embedding": v} for t, v in zip(texts, vectors)]
