"""Per-request correlation id, shared across logging and middleware.

A single `contextvars.ContextVar` holds the current request's id for the
lifetime of that request's async task. Two consumers read it:

  - the request-id logging filter (app/core/logging_config.py), which stamps
    every log line emitted while handling the request, and
  - the HTTP middleware in app/main.py, which seeds the var from an incoming
    `X-Request-ID` header (or a fresh uuid4) and echoes it back on the response.

Kept in its own module so logging_config can import the var without dragging in
FastAPI/main, avoiding an import cycle.
"""
from __future__ import annotations

import contextvars

# Default "-" so logs emitted outside any request (startup, background loops,
# Celery) still render a stable, grep-friendly placeholder instead of blowing up
# the formatter with a missing attribute.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request's id, or "-" when outside a request scope."""
    return request_id_var.get()
