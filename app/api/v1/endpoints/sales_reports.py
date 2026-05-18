from datetime import date
from typing import Literal

from fastapi import APIRouter, Query

from app.core.dependencies import AdminUser, DBSession
from app.services.us_pod_call_report import (
    build_us_pod_call_report,
    build_us_pod_weekly_call_report,
    send_us_pod_call_report_email,
)

router = APIRouter(prefix="/sales-reports", tags=["sales-reports"])


@router.get("/us-pod-call-report")
async def preview_us_pod_call_report(
    session: DBSession,
    _admin: AdminUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: Literal["daily", "weekly"] = Query(default="daily"),
):
    if report_type == "weekly":
        return await build_us_pod_weekly_call_report(session, report_date)
    return await build_us_pod_call_report(session, report_date)


@router.post("/us-pod-call-report/send")
async def send_us_pod_call_report(
    session: DBSession,
    _admin: AdminUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: Literal["daily", "weekly"] = Query(default="daily"),
    recipient: str | None = Query(default=None),
):
    recipients = [recipient] if recipient else None
    return await send_us_pod_call_report_email(
        session,
        report_date,
        report_type=report_type,
        recipients=recipients,
    )
