from __future__ import annotations

import re
from typing import Any

import httpx

_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:api_key|apikey|key|token|access_token|client_secret|password)=)[^&\s'\"]+",
    re.IGNORECASE,
)


def safe_error_message(error: Any) -> str:
    """Return an operator-useful error string without leaking URL credentials."""
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        reason = response.reason_phrase or ""
        return f"HTTP {response.status_code} {reason}".strip()
    return _SENSITIVE_QUERY_RE.sub(r"\1[redacted]", str(error))
