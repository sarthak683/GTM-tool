import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as v1_router
from app.config import settings
from app.core.exceptions import BeaconError, register_exception_handlers
from app.core.logging_config import setup_logging
from app.core.request_context import get_request_id, request_id_var
from app.services.background_jobs import shutdown_background_workers, start_background_workers
from app.services.meeting_automation import run_due_pre_meeting_intel_once
from app.services.zippy_docs.base import ZIPPY_OUTPUT_DIR

# Configure app-wide logging. Without this, the root logger sits at WARNING and
# every `logger.info(...)` in the codebase — including the Zippy per-iteration
# diagnostic at zippy_agent.py:760 — is dropped on the floor. setup_logging()
# pins INFO, picks JSON vs human format from settings.LOG_JSON, stamps each
# record with the per-request id, and quiets the noisy dependency loggers.
setup_logging()

logger = logging.getLogger(__name__)


# ── Sentry error tracking ─────────────────────────────────────────────────────
# Initialised only when a DSN is configured, and behind a defensive import so a
# missing sentry-sdk (or a DSN-less dev box) never breaks boot.
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[FastApiIntegration()],
            environment=settings.SENTRY_ENVIRONMENT or settings.ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        )
        logger.info("Sentry error tracking initialised")
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed; skipping Sentry init")


async def _pre_meeting_automation_loop() -> None:
    while True:
        try:
            await run_due_pre_meeting_intel_once()
        except Exception:
            logger.exception("Pre-meeting automation loop failed")
        await asyncio.sleep(600)


async def _ensure_instantly_webhook() -> None:
    """Register Instantly webhook on startup if configured."""
    if not settings.INSTANTLY_API_KEY or not settings.INSTANTLY_WEBHOOK_URL:
        return
    try:
        from app.clients.instantly import InstantlyClient
        from app.clients.instantly_events import INSTANTLY_WEBHOOK_EVENTS

        client = InstantlyClient()
        await client.ensure_webhook(
            url=settings.INSTANTLY_WEBHOOK_URL,
            event_types=INSTANTLY_WEBHOOK_EVENTS,
        )
        logger.info("Instantly webhook registered on startup")
    except Exception:
        logger.exception("Failed to register Instantly webhook on startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Refuse to boot a prod/staging deploy that is still on insecure dev
    # placeholders. Returns [] in development, so this is a no-op locally.
    secret_problems = settings.validate_runtime_secrets()
    if secret_problems:
        for problem in secret_problems:
            logger.error("Startup secret check failed: %s", problem)
        raise RuntimeError(
            "Refusing to start: runtime secret validation failed "
            f"({len(secret_problems)} problem(s)). See logs above."
        )
    await start_background_workers()
    await _ensure_instantly_webhook()
    automation_task = asyncio.create_task(_pre_meeting_automation_loop(), name="pre-meeting-automation")
    try:
        yield
    finally:
        automation_task.cancel()
        await asyncio.gather(automation_task, return_exceptions=True)
        await shutdown_background_workers()

# FastAPI app bootstrap:
# 1. create the app
# 2. attach cross-origin policy for the browser frontend
# 3. register shared exception handling
# 4. mount the versioned API router
app = FastAPI(
    title="Beacon CRM API",
    description="GTM Sales CRM for Beacon.li — AI Implementation Orchestration",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# The frontend talks to the API directly from the browser, so allowed origins
# come from settings instead of being hard-coded in each route.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Centralise app-specific errors so route handlers can raise domain errors
# without duplicating HTTP status mapping logic everywhere.
register_exception_handlers(app)


# ── Request correlation id ────────────────────────────────────────────────────
# Seed the request_id ContextVar from an incoming X-Request-ID (so a load
# balancer / upstream trace id flows through to our logs) or mint a fresh uuid4,
# then echo it back on the response so callers can correlate too.
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID")
    request_id = incoming or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        # Reset so the value never leaks into a pooled task that handles the
        # next, unrelated request.
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


# ── Catch-all for truly unhandled exceptions ──────────────────────────────────
# BeaconError subclasses and HTTPException keep their existing handlers
# (registered above / by FastAPI); this only fires for genuinely unexpected
# errors. We log the full traceback server-side with the request id but return a
# generic 500 so no stack trace or internal detail ever reaches the client.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception on %s %s (request_id=%s)",
        request.method,
        request.url.path,
        get_request_id(),
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# All API endpoints live under /api/v1. A future v2 can be mounted alongside it
# without changing the existing route modules.
app.include_router(v1_router, prefix="/api/v1")

# ── Prometheus /metrics ───────────────────────────────────────────────────────
# Expose request metrics for scraping when enabled. Behind a defensive import so
# a missing prometheus-fastapi-instrumentator never breaks boot.
if settings.ENABLE_METRICS:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics")
        logger.info("Prometheus metrics exposed at /metrics")
    except ImportError:
        logger.warning(
            "ENABLE_METRICS is set but prometheus-fastapi-instrumentator is not "
            "installed; skipping /metrics"
        )

# Serve Zippy-generated Word docs (MOM, NDAs, drafts) so the frontend can link
# straight to them. The directory is ensured at import time by zippy_docs.base.
app.mount(
    "/zippy_outputs",
    StaticFiles(directory=str(ZIPPY_OUTPUT_DIR), check_dir=False),
    name="zippy_outputs",
)


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "healthy", "service": "beacon-crm-api", "version": "2.0.0"}


@app.get("/", tags=["health"])
async def root():
    return {"message": "Beacon CRM API", "docs": "/docs", "version": "2.0.0"}
