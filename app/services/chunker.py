"""Token-aware chunking for RAG indexing.

Uses tiktoken to count real tokens (not characters), then slices with a
configurable overlap so context isn't lost across chunk boundaries. Boundaries
prefer paragraph / sentence breaks for human-readable snippets in citations.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List

import tiktoken

from app.config import settings

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        # cl100k_base is the tokenizer used by text-embedding-3-* and GPT-4.
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


@dataclass
class Chunk:
    index: int
    text: str
    token_count: int


# Sentence-end heuristic — good enough for most English prose. We fall back to
# token-count slicing when a single paragraph is itself too large.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(])")


def chunk_text(text: str) -> List[Chunk]:
    """Return token-sized chunks with overlap. Empty string → empty list."""
    text = (text or "").strip()
    if not text:
        return []

    enc = _get_encoder()
    target = settings.ZIPPY_CHUNK_SIZE
    overlap = settings.ZIPPY_CHUNK_OVERLAP

    # First split on blank lines to stay close to the document's structure.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # Build rough units: paragraphs too long to fit are pre-split at sentence
    # boundaries so the greedy packer below never has to cut inside a sentence.
    units: List[str] = []
    for para in paragraphs:
        para_tokens = len(enc.encode(para))
        if para_tokens <= target:
            units.append(para)
            continue
        for sentence in _SENTENCE_SPLIT.split(para):
            sent = sentence.strip()
            if not sent:
                continue
            sent_tokens = len(enc.encode(sent))
            if sent_tokens <= target:
                units.append(sent)
            else:
                # Last-resort: slice a runaway sentence by tokens.
                tokens = enc.encode(sent)
                for start in range(0, len(tokens), target):
                    units.append(enc.decode(tokens[start : start + target]))

    # Greedy pack units into chunks up to `target` tokens.
    chunks: List[Chunk] = []
    current_tokens: list[int] = []
    current_text_parts: list[str] = []

    def flush() -> None:
        if not current_tokens:
            return
        chunks.append(
            Chunk(
                index=len(chunks),
                text="\n\n".join(current_text_parts).strip(),
                token_count=len(current_tokens),
            )
        )

    for unit in units:
        unit_tokens = enc.encode(unit)
        if current_tokens and len(current_tokens) + len(unit_tokens) > target:
            flush()
            # Start the next chunk with the tail of the previous for overlap,
            # keeping context flowing across boundaries.
            if overlap > 0 and current_tokens:
                tail = current_tokens[-overlap:]
                current_tokens = list(tail)
                current_text_parts = [enc.decode(tail)]
            else:
                current_tokens = []
                current_text_parts = []
        current_tokens.extend(unit_tokens)
        current_text_parts.append(unit)

    flush()
    return chunks
