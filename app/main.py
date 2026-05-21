import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as v1_router
from app.config import settings
from app.core.exceptions import BeaconError, register_exception_handlers
from app.services.background_jobs import shutdown_background_workers, start_background_workers
from app.services.meeting_automation import run_due_pre_meeting_intel_once
from app.services.zippy_docs.base import ZIPPY_OUTPUT_DIR

# Configure app-wide logging level. Without this, the root logger sits at
# WARNING and every `logger.info(...)` in the codebase — including the
# Zippy per-iteration diagnostic at zippy_agent.py:760 — is dropped on
# the floor. We pin to INFO in dev so the trace is visible. Promote to
# WARNING via env if a quieter prod log is needed later.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    force=True,
)
# Quiet the noisiest dependency loggers so the signal-to-noise stays sane.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def _pre_meeting_automation_loop() -> None:
    while True:
        try:
            await run_due_pre_meeting_intel_once()
        except Exception:
            logger.exception("Pre-meeting automation loop failed")
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await start_background_workers()
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

# All API endpoints live under /api/v1. A future v2 can be mounted alongside it
# without changing the existing route modules.
app.include_router(v1_router, prefix="/api/v1")

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
