"""Shared helpers for Zippy's document generators."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4


# ── Upload deduplication cache ───────────────────────────────────────────────
# Claude's tool-use loop sometimes calls the same generate_* tool twice in a
# row (especially when a previous turn was cut off mid-output). Without a
# guard, that produces duplicate Google Docs in Drive — same content, two URLs,
# AE has to figure out which is canonical. We key on (user, client, kind, day)
# so a re-run within a single working day reuses the already-uploaded link.
_RECENT_UPLOADS: dict[str, str] = {}


def _upload_cache_key(user_id: str, client_name: str, kind: str) -> str:
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    raw = f"{user_id}:{client_name}:{kind}:{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def get_cached_upload(
    user_id: str, client_name: str, kind: str
) -> Optional[str]:
    """Return cached drive_url if this doc was already uploaded today."""
    key = _upload_cache_key(user_id, client_name, kind)
    return _RECENT_UPLOADS.get(key)


def cache_upload(
    user_id: str, client_name: str, kind: str, drive_url: str
) -> None:
    """Remember a successful upload so a duplicate call returns the same link."""
    key = _upload_cache_key(user_id, client_name, kind)
    _RECENT_UPLOADS[key] = drive_url
    # Bound the cache so a long-lived process doesn't grow unbounded. FIFO
    # eviction is fine — anything older than ~50 entries is from an earlier
    # working session and shouldn't be reused anyway.
    if len(_RECENT_UPLOADS) > 50:
        oldest_key = next(iter(_RECENT_UPLOADS))
        del _RECENT_UPLOADS[oldest_key]

# Everything Zippy generates lands under this directory. FastAPI serves it via
# /zippy_outputs so the frontend can link directly to the finished file.
ZIPPY_OUTPUT_DIR = Path(
    os.environ.get(
        "ZIPPY_OUTPUT_DIR",
        str(Path(__file__).resolve().parents[3] / "storage" / "zippy_outputs"),
    )
).resolve()
ZIPPY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class GeneratedDocument:
    """What every generator returns to the agent."""

    filename: str                    # user-facing filename e.g. "MOM - Acme - 19 Apr 2026.docx"
    path: str                        # absolute path on disk
    url: str                         # relative URL the frontend can link to (.docx fallback)
    kind: str                        # "mom" | "nda_in" | "nda_us" | "nda_sg" | "generic_docx"
    summary: str                     # one-liner shown in chat
    created_at: datetime
    drive_file_id: str = ""          # Google Drive file ID (set after upload)
    drive_url: str = ""              # Google Docs webViewLink (preferred link for user)
    body_text: str = ""              # plain-text rewritten body — generators populate this
    #                                  so downstream tools (e.g. generate_poc_ppt that
    #                                  needs the kickoff body as input) can read it
    #                                  off the result without re-fetching the doc.


def _slug(value: str, max_len: int = 48) -> str:
    """Safe filename slug — strip weird chars, collapse whitespace."""
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", value or "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned) or "untitled"
    return cleaned[:max_len].rstrip("_-")


def build_output_path(kind: str, client_name: str, extension: str = "docx") -> tuple[Path, str]:
    """Return ``(absolute_path, public_url)`` for a new document."""
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    unique = uuid4().hex[:6]
    filename = f"{_slug(kind)}-{_slug(client_name)}-{date_str}-{unique}.{extension}"
    path = ZIPPY_OUTPUT_DIR / filename
    url = f"/zippy_outputs/{filename}"
    return path, url


def human_today() -> str:
    return datetime.utcnow().strftime("%d %B %Y")
