"""Per-scheduled-job health.

One row per beat-scheduled Celery task, upserted automatically by the
task_postrun signal (see app/tasks/job_health_signals.py). Powers the admin
"System Health" panel so a silently-dead scheduler (e.g. reports not sending)
surfaces in the UI instead of going unnoticed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class JobHealth(SQLModel, table=True):
    __tablename__ = "job_health"

    task_name: str = Field(primary_key=True)
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_status: Optional[str] = None          # "success" | "skipped" | "failure"
    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None
    runs_total: int = Field(default=0)
    failures_total: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # "Ran" and "worked" are different questions, and conflating them is what
    # let tl;dv import nothing for four months behind a green badge. A task that
    # returns {"status": "skipped"} did run — the scheduler is alive — but it
    # did no work, so it advances last_run_at and NOT last_effective_at.
    #
    # last_success_at answers "did this complete without error", which stays
    # useful. last_effective_at answers "when did this last actually do
    # something", which is the one a human wants when a rep says email stopped
    # arriving.
    last_effective_at: Optional[datetime] = None
    last_skip_reason: Optional[str] = None
    skips_total: int = Field(default=0)
