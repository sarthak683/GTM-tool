"""
Simple paragraph-aware chunker for RAG.

Why paragraph-aware rather than a sliding token window:
  - Sales resources (playbooks, ROI docs, battlecards) are already structured
    by sections — splitting on blank lines preserves semantic boundaries.
  - A fixed-size window would cut through the middle of an objection/response
    pair, hurting retrieval quality.

Target chunk size ≈ 400 words (~500 tokens). Short paragraphs are merged;
oversized paragraphs are hard-split so no single chunk exceeds the cap.
"""
from __future__ import annotations

from typing import List

TARGET_WORDS = 400
HARD_MAX_WORDS = 600


def chunk_text(text: str) -> List[str]:
    if not text or not text.strip():
        return []

    # Split on blank lines — Markdown/doc-style paragraph boundaries.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0

    for para in paragraphs:
        words = para.split()
        # Oversized paragraph — hard-split on word boundary.
        if len(words) > HARD_MAX_WORDS:
            flush()
            for i in range(0, len(words), TARGET_WORDS):
                chunks.append(" ".join(words[i : i + TARGET_WORDS]))
            continue

        if buf_len + len(words) > TARGET_WORDS and buf:
            flush()
        buf.append(para)
        buf_len += len(words)

    flush()
    return chunks
