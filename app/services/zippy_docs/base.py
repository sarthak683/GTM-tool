"""Shared helpers for Zippy's document generators."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


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

# Scratch space for document rendering — NOT a published directory.
#
# python-docx / openpyxl / python-pptx all render to a filesystem path, and each
# generator's Drive upload reads the finished bytes straight back off it, so a
# real file still has to exist for the duration of the call. What changed is
# that it is now *only* a scratch file: the moment the document becomes a link
# the user can click, `zippy_docs.storage.persist_generated_document` copies the
# bytes into Postgres and deletes this file.
#
# It used to live under ./storage/zippy_outputs and be published by a
# StaticFiles mount at /zippy_outputs. That was the bug: production runs two
# backend replicas with no shared volume, so the file only existed on the pod
# that made it (the link 404'd whenever the request landed on the other replica)
# and the container's writable layer is wiped by every restart and redeploy.
# Pointing this at the system temp dir makes the ephemerality explicit instead
# of pretending a per-pod directory is durable storage.
ZIPPY_OUTPUT_DIR = Path(
    os.environ.get(
        "ZIPPY_OUTPUT_DIR",
        str(Path(tempfile.gettempdir()) / "zippy_outputs"),
    )
).resolve()


def ensure_output_dir() -> Path:
    """Create the scratch directory on first use and return it.

    Deliberately lazy. This used to run at import time, which meant any process
    that imported the app — including ones that never generate a document — had
    to be able to write to the path, and a read-only root filesystem would take
    down boot. Creating it when a generator actually needs it removes that
    coupling; the failure, if it comes, now surfaces on the call that caused it.
    """
    try:
        ZIPPY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "Cannot create Zippy scratch directory %s: %s", ZIPPY_OUTPUT_DIR, exc
        )
        raise
    return ZIPPY_OUTPUT_DIR


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
    """Return ``(scratch_path, provisional_url)`` for a new document.

    The second element is intentionally empty. A document's real URL is a
    capability token minted when the bytes are written to Postgres, which
    happens after generation and after the Drive upload — see
    ``zippy_docs.storage.persist_generated_document``, which sets ``doc.url``.

    The tuple shape is kept so the seven generators that unpack it need no
    change, and an empty string is the honest placeholder: if persistence never
    runs, the artifact carries no link at all rather than the old
    ``/zippy_outputs/...`` path, which pointed at a file on one specific pod's
    ephemeral disk and mostly 404'd.
    """
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    unique = uuid4().hex[:6]
    filename = f"{_slug(kind)}-{_slug(client_name)}-{date_str}-{unique}.{extension}"
    path = ensure_output_dir() / filename
    return path, ""


def human_today() -> str:
    return datetime.utcnow().strftime("%d %B %Y")
