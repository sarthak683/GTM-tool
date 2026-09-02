from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.services.permissions import require_workspace_permission
from app.services.us_pod_call_report import (
    INDIA_DEFAULT_SALES_REPORT_SETTINGS,
    INDIA_POD_REPS,
    build_us_pod_period_call_report,
    load_sales_report_settings,
    send_us_pod_call_report_email,
)
from app.services.weekly_digest import (
    load_weekly_digest_settings,
    send_weekly_digest_email,
    weekly_digest_period,
)

router = APIRouter(prefix="/sales-reports", tags=["sales-reports"])

ReportType = Literal["daily", "weekly", "month_to_date", "prior_quarter", "custom"]


def _validate_period(report_type: ReportType, period_start: date | None, period_end: date | None) -> None:
    if report_type == "custom" and (not period_start or not period_end):
        raise HTTPException(status_code=422, detail="Custom reports require both period_start and period_end.")
    if period_start and period_end and period_start > period_end:
        raise HTTPException(status_code=422, detail="period_start must be on or before period_end.")


@router.get("/us-pod-call-report")
async def preview_us_pod_call_report(
    session: DBSession,
    current_user: CurrentUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: ReportType = Query(default="daily"),
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
):
    await require_workspace_permission(session, current_user, "manage_reports")
    _validate_period(report_type, period_start, period_end)
    return await build_us_pod_period_call_report(
        session,
        report_type=report_type,
        report_date=report_date,
        period_start=period_start,
        period_end=period_end,
    )


@router.post("/us-pod-call-report/send")
async def send_us_pod_call_report(
    session: DBSession,
    current_user: CurrentUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: ReportType = Query(default="daily"),
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
    recipient: str | None = Query(default=None),
):
    await require_workspace_permission(session, current_user, "manage_reports")
    _validate_period(report_type, period_start, period_end)
    recipients = [recipient] if recipient else None
    return await send_us_pod_call_report_email(
        session,
        report_date,
        report_type=report_type,
        recipients=recipients,
        period_start=period_start,
        period_end=period_end,
    )


@router.get("/india-pod-call-report")
async def preview_india_pod_call_report(
    session: DBSession,
    current_user: CurrentUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: ReportType = Query(default="daily"),
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
):
    await require_workspace_permission(session, current_user, "manage_reports")
    _validate_period(report_type, period_start, period_end)
    report_settings = await load_sales_report_settings(
        session, key="india_sales_report", defaults=INDIA_DEFAULT_SALES_REPORT_SETTINGS
    )
    report = await build_us_pod_period_call_report(
        session,
        report_type=report_type,
        report_date=report_date,
        period_start=period_start,
        period_end=period_end,
        report_settings=report_settings,
        reps=INDIA_POD_REPS,
    )
    report["pod_label"] = "India Pod"
    return report


@router.post("/india-pod-call-report/send")
async def send_india_pod_call_report(
    session: DBSession,
    current_user: CurrentUser,
    report_date: date | None = Query(default=None, alias="date"),
    report_type: ReportType = Query(default="daily"),
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
    recipient: str | None = Query(default=None),
):
    await require_workspace_permission(session, current_user, "manage_reports")
    _validate_period(report_type, period_start, period_end)
    recipients = [recipient] if recipient else None
    return await send_us_pod_call_report_email(
        session,
        report_date,
        report_type=report_type,
        recipients=recipients,
        period_start=period_start,
        period_end=period_end,
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
