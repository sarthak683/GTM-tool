"""Centralised logging setup for the FastAPI backend.

`setup_logging()` replaces the inline `logging.basicConfig(...)` that used to
live in app/main.py. It does three things on top of the old behaviour:

  - picks the formatter from config: a `pythonjsonlogger` JSON formatter when
    `settings.LOG_JSON` is true (structured logs for prod aggregation), or the
    original human-readable `%(asctime)s %(levelname)s %(name)s :: %(message)s`
    otherwise,
  - injects the per-request correlation id (see app/core/request_context.py)
    into every record via a logging.Filter, so both formats can surface it, and
  - preserves the existing quieting of the noisiest dependency loggers.

The level still pins to INFO so the codebase's `logger.info(...)` diagnostics
(e.g. the Zippy per-iteration trace) stay visible; promote via env later if a
quieter prod log is wanted.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.core.request_context import get_request_id

# Human-readable format — unchanged from the original basicConfig call so dev
# logs look exactly as before when LOG_JSON is off.
_HUMAN_FORMAT = "%(asctime)s %(levelname)s %(name)s :: %(message)s"


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id (or "-" outside one).

    Attached to the root handler so the value is available to both the human and
    JSON formatters. Reads the ContextVar rather than taking the id as state, so
    one shared filter instance is correct across concurrent requests.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def _build_formatter() -> logging.Formatter:
    """JSON formatter when LOG_JSON is set, else the legacy human format."""
    if settings.LOG_JSON:
        # Imported lazily so a missing python-json-logger never breaks boot in a
        # dev setup that runs with the human formatter anyway.
        from pythonjsonlogger import jsonlogger

        # List the fields we want promoted to top-level JSON keys; request_id is
        # supplied by RequestIdFilter, the rest are standard LogRecord attrs.
        return jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s"
        )
    return logging.Formatter(_HUMAN_FORMAT)


def setup_logging() -> None:
    """Configure the root logger. Safe to call once at startup."""
    formatter = _build_formatter()
    request_id_filter = RequestIdFilter()

    # Single stream handler on the root logger, mirroring force=True semantics:
    # drop any handlers a dependency installed at import time so our format wins.
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(request_id_filter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Belt-and-suspenders: also filter at the logger level so records created via
    # logging.<level>(...) helpers (which may bypass per-handler filters on
    # propagation) still carry request_id.
    root.addFilter(request_id_filter)

    # Quiet the noisiest dependency loggers so the signal-to-noise stays sane.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
