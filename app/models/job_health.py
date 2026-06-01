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
    last_status: Optional[str] = None          # "success" | "failure"
    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None
    runs_total: int = Field(default=0)
    failures_total: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
