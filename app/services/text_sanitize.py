"""Strip characters that Postgres asyncpg cannot store.

The ICP research pipeline scrapes arbitrary web pages and sometimes lands
raw NUL bytes (\\u0000) or lone UTF-16 surrogates inside JSONB payloads.
Postgres' asyncpg driver refuses these with:
  ``asyncpg.exceptions.UntranslatableCharacterError: unsupported Unicode escape sequence``
and the whole task fails after retries.

We strip them centrally so no caller has to remember to sanitize. The
cleaner walks dicts, lists, tuples, and strings recursively and returns a
structure shaped identically to the input.
"""
from __future__ import annotations

import re
from typing import Any

# Matches:
#   - NUL byte (\x00) — asyncpg rejects outright
#   - Lone surrogates (\uD800-\uDFFF) — invalid UTF-8, asyncpg rejects
#   - Other C0 / C1 control characters except \t (0x09), \n (0x0A), \r (0x0D)
_UNSAFE_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]",
    flags=re.UNICODE,
)


def sanitize_text(value: str) -> str:
    """Remove NUL bytes, lone surrogates, and disallowed control chars."""
    if not value:
        return value
    return _UNSAFE_CHARS_RE.sub("", value)


def sanitize_json_value(value: Any) -> Any:
    """Recursively clean strings inside dicts/lists/tuples. Primitives pass through."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {sanitize_text(k) if isinstance(k, str) else k: sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_json_value(item) for item in value)
    return value
