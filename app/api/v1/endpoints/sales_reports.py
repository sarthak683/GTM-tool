from datetime import date
from typing import Literal

from fastapi import APIRouter, Query

from app.core.dependencies import AdminUser, DBSession
from app.services.us_pod_call_report import (
    INDIA_DEFAULT_SALES_REPORT_SETTINGS,
    INDIA_POD_REPS,
    build_us_pod_call_report,
    build_us_pod_weekly_call_report,
    load_sales_report_settings,
    send_us_pod_call_report_email,
)
from app.services.weekly_digest import (
    load_weekly_digest_settings,
    send_weekly_digest_email,
    weekly_digest_period,
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


@router.get("/india-pod-call-report")
async def preview_india_pod_call_report(
    session: DBSession,
    _admin: AdminUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: Literal["daily", "weekly"] = Query(default="daily"),
):
    report_settings = await load_sales_report_settings(
        session, key="india_sales_report", defaults=INDIA_DEFAULT_SALES_REPORT_SETTINGS
    )
    if report_type == "weekly":
        report = await build_us_pod_weekly_call_report(
            session, report_date, report_settings=report_settings, reps=INDIA_POD_REPS
        )
    else:
        report = await build_us_pod_call_report(
            session, report_date, report_settings=report_settings, reps=INDIA_POD_REPS
        )
    report["pod_label"] = "India Pod"
    return report


@router.post("/india-pod-call-report/send")
async def send_india_pod_call_report(
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
        config_key="india_sales_report",
        config_defaults=INDIA_DEFAULT_SALES_REPORT_SETTINGS,
        reps=INDIA_POD_REPS,
        pod_label="India Pod",
    )


@router.get("/weekly-digest")
async def preview_weekly_digest(
    session: DBSession,
    _admin: AdminUser,
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
):
    digest_settings = await load_weekly_digest_settings(session)
    if period_start and period_end:
        start, end = period_start, period_end
    else:
        start, end = weekly_digest_period(digest_settings=digest_settings)
    from app.services.weekly_digest import build_weekly_digest

    digest = await build_weekly_digest(session, start, end, digest_settings=digest_settings)
    return {
        "period_start": digest.period_start,
        "period_end": digest.period_end,
        "subject": digest.subject,
        "html_body": digest.html_body,
        "stage_changes": len(digest.stage_changes),
        "account_status_changes": len(digest.account_status_changes),
        "prospect_dnd": len(digest.prospect_dnd),
        "imports": len(digest.imports),
    }


@router.post("/weekly-digest/send")
async def send_weekly_digest(
    session: DBSession,
    _admin: AdminUser,
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
    recipient: str | None = Query(default=None),
):
    digest_settings = await load_weekly_digest_settings(session)
    if period_start and period_end:
        start, end = period_start, period_end
    else:
        start, end = weekly_digest_period(digest_settings=digest_settings)
    recipients = [recipient] if recipient else None
    digest = await send_weekly_digest_email(
        session, start, end, recipients=recipients, digest_settings=digest_settings
    )
    return {
        "period_start": digest.period_start,
        "period_end": digest.period_end,
        "recipients": digest.recipients,
        "send_results": digest.send_results,
    }
