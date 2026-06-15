"""Admin-only scheduled-job health endpoint.

Surfaces every beat-scheduled Celery job with its last run, status, and a
computed staleness verdict (ok / stale / failing / unknown) so admins can spot
a silently-dead scheduler in the UI instead of finding out from a colleague.
"""
from __future__ import annotations

from datetime import datetime, timezone

from celery.schedules import crontab
from fastapi import APIRouter
from sqlalchemy import select

from app.celery_app import celery_app
from app.core.dependencies import AdminUser, DBSession
from app.models.job_health import JobHealth

router = APIRouter(prefix="/admin", tags=["admin"])


def _expected_gap_seconds(sched) -> float:
    """Rough max-expected gap between runs, used only for staleness."""
    if isinstance(sched, (int, float)):
        return float(sched)
    if isinstance(sched, crontab):
        # Specific hour set → runs at most daily; otherwise it's sub-hourly.
        hour = getattr(sched, "_orig_hour", "*")
        return 86400.0 if hour not in ("*", "*/1", None, "") else 3600.0
    return 3600.0


def _schedule_label(sched) -> str:
    if isinstance(sched, (int, float)):
        minutes = int(sched) // 60
        return f"every {minutes} min" if minutes else f"every {int(sched)}s"
    if isinstance(sched, crontab):
        return f"cron(min={sched._orig_minute} hour={sched._orig_hour})"
    return str(sched)


@router.get("/job-health")
async def get_job_health(session: DBSession, _admin: AdminUser) -> dict:
    rows = {
        r.task_name: r
        for r in (await session.execute(select(JobHealth))).scalars().all()
    }
    now = datetime.now(timezone.utc)
    jobs = []
    for beat_name, entry in (celery_app.conf.beat_schedule or {}).items():
        task = entry.get("task")
        sched = entry.get("schedule")
        gap = _expected_gap_seconds(sched)
        r = rows.get(task)
        last_run = r.last_run_at if r else None
        last_run_aware = (
            last_run.replace(tzinfo=timezone.utc)
            if last_run is not None and last_run.tzinfo is None
            else last_run
        )

        if r is None or last_run is None:
            staleness = "unknown"
        elif r.last_status == "failure":
            staleness = "failing"
        elif (now - last_run_aware).total_seconds() > (2 * gap + 300):
            staleness = "stale"
        else:
            staleness = "ok"

        jobs.append({
            "beat_name": beat_name,
            "task": task,
            "schedule": _schedule_label(sched),
            "last_run_at": last_run.isoformat() if last_run else None,
            "last_success_at": r.last_success_at.isoformat() if r and r.last_success_at else None,
            "last_status": r.last_status if r else None,
            "last_error": r.last_error if r else None,
            "last_duration_ms": r.last_duration_ms if r else None,
            "runs_total": r.runs_total if r else 0,
            "failures_total": r.failures_total if r else 0,
            "staleness": staleness,
        })

    order = {"failing": 0, "stale": 1, "unknown": 2, "ok": 3}
    jobs.sort(key=lambda j: order.get(j["staleness"], 9))
    return {"jobs": jobs, "as_of": now.isoformat()}
