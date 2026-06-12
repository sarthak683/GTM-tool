from datetime import datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import logging

from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy import func, or_, select

from app.core.dependencies import CurrentUser, DBSession, Pagination
from app.models.activity import Activity, ActivityCreate, ActivityRead, ActivityUpdate
from app.models.deal import Deal
from app.models.meeting import Meeting
from app.repositories.activity import ActivityRepository
from app.schemas.common import PaginatedResponse
from app.database import AsyncSessionLocal
from app.services.disposition_effects import (
    apply_call_disposition_effects,
    is_meeting_booked_disposition,
)
from app.services.tasks import (
    backfill_open_task_assignments,
    compute_deal_task_input_hash,
    mark_deal_task_refresh_requested,
    refresh_system_tasks_for_entity,
    should_queue_deal_task_refresh,
)

router = APIRouter(prefix="/activities", tags=["activities"])
logger = logging.getLogger(__name__)
# Legacy fallback only. The live "calls today" tile now reads the SAME configured
# report cutoff as the daily call report (single source of truth), so it can never
# drift from the report's business-day boundary again. These constants are kept as
# the fallback when no report config is loadable.
REPORT_CUTOFF_TIMEZONE = ZoneInfo("Asia/Kolkata")
REPORT_CUTOFF_HOUR = 6


def _pod_report_config_key(email: str | None) -> tuple[str, dict]:
    """Pick the report-config block for the viewer's pod so the live counter uses
    the same cutoff the rep's daily report uses. India pod -> india_sales_report
    (IST-midnight day); everyone else -> US sales_report (7:30 AM IST cutoff)."""
    from app.core.pods import pod_rep_emails
    from app.services.us_pod_call_report import (
        DEFAULT_SALES_REPORT_SETTINGS,
        INDIA_DEFAULT_SALES_REPORT_SETTINGS,
    )

    normalized = (email or "").strip().lower()
    if normalized in pod_rep_emails("india"):
        return "india_sales_report", INDIA_DEFAULT_SALES_REPORT_SETTINGS
    return "sales_report", DEFAULT_SALES_REPORT_SETTINGS


async def _refresh_tasks_after_activity_background(deal_id: UUID | None, contact_id: UUID | None) -> None:
    async with AsyncSessionLocal() as session:
        try:
            # Scope the assignee backfill to the just-touched entity — this fires
            # on every activity create, and only this entity's tasks can change.
            if contact_id:
                await refresh_system_tasks_for_entity(session, "contact", contact_id)
                await backfill_open_task_assignments(session, entity_type="contact", entity_id=contact_id)
            if deal_id:
                # refresh_system_tasks_for_entity(..., "deal", ...) unconditionally
                # runs two Opus calls (interpret_deal_activity + emit_ai_tasks). This
                # fires on EVERY activity create, so several activities in one session
                # re-run identical Opus pairs even when the deal's inputs are unchanged.
                # Apply the same hash+TTL+debounce gate the GET /tasks path already
                # trusts (tasks.py list_tasks), short-circuiting BEFORE the LLM calls.
                # Scoped to this activity-triggered deal path only — the GET "force"
                # path and other always-run callers of refresh_system_tasks_for_entity
                # are not affected because the gate lives here, not in that function.
                deal = await session.get(Deal, deal_id)
                if deal is not None:
                    input_hash = await compute_deal_task_input_hash(session, deal)
                    if should_queue_deal_task_refresh(deal, input_hash=input_hash):
                        # Mark requested so a concurrent GET-path debounce sees this
                        # in-flight refresh, mirroring the GET path's bookkeeping.
                        mark_deal_task_refresh_requested(deal)
                        session.add(deal)
                        await refresh_system_tasks_for_entity(session, "deal", deal_id)
                        await backfill_open_task_assignments(session, entity_type="deal", entity_id=deal_id)
            await session.commit()
        except Exception as exc:  # pragma: no cover - background safety net
            logger.warning("activity-triggered task refresh failed for deal=%s contact=%s: %s", deal_id, contact_id, exc)
            await session.rollback()


@router.get("/", response_model=PaginatedResponse[ActivityRead])
async def list_activities(
    session: DBSession,
    pagination: Pagination,
    current_user: CurrentUser,
    deal_id: Optional[UUID] = Query(default=None),
    contact_id: Optional[UUID] = Query(default=None),
    company_id: Optional[UUID] = Query(default=None),
    type: Optional[str] = Query(default=None),
    created_by_id: Optional[UUID] = Query(default=None),
    created_by_me: bool = Query(default=False, description="Shortcut for created_by_id=current_user.id"),
    since: Optional[str] = Query(default=None, description="ISO datetime — only activities at or after this moment"),
):
    repo = ActivityRepository(session)
    filters = []
    if deal_id:
        filters.append(Activity.deal_id == deal_id)
    if contact_id:
        filters.append(Activity.contact_id == contact_id)
    if company_id:
        # Activities linked via deals belonging to this company,
        # or via meetings mapped to this company
        company_deal_ids = select(Deal.id).where(Deal.company_id == company_id)
        meeting_ext_ids = select(
            ("tldv:meeting:" + Meeting.external_source_id)
        ).where(Meeting.company_id == company_id, Meeting.external_source_id.isnot(None))
        filters.append(
            or_(
                Activity.deal_id.in_(company_deal_ids),
                Activity.external_source_id.in_(meeting_ext_ids),
            )
        )
    if type:
        filters.append(Activity.type == type)
    # Per-rep filter — supports the "Calls Logged" tile on the Contacts page
    # showing TODAY'S real call count for the logged-in rep, instead of the
    # page-bounded contact-derived count which silently drops to 0 when the
    # paginated view rotates (the 2026-05-07 "6 -> 0" Mahesh saw).
    effective_creator = created_by_id or (current_user.id if created_by_me else None)
    if effective_creator:
        filters.append(Activity.created_by_id == effective_creator)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            # Compare against naive-UTC created_at, so explicitly convert to
            # UTC. astimezone() without an arg uses the pod's local timezone
            # which we cannot guarantee is UTC.
            if since_dt.tzinfo is not None:
                since_dt = since_dt.astimezone(timezone.utc).replace(tzinfo=None)
            filters.append(Activity.created_at >= since_dt)
        except ValueError:
            # Bad input — ignore the filter rather than 500-ing the dashboard.
            logger.warning("activities.list: invalid since=%r — ignoring", since)
    items, total = await repo.list_paginated(
        *filters,
        skip=pagination.skip,
        limit=pagination.limit,
        order_by=Activity.created_at.desc(),
    )
    # Resolve the logger's display name so the UI can show "Manually logged by
    # <person>" instead of a faceless "Manually logged". One batched lookup.
    # SQLModel table rows reject undeclared attrs, so hydrate ActivityRead
    # objects (which declare user_name) and set the name there.
    creator_ids = {it.created_by_id for it in items if getattr(it, "created_by_id", None)}
    names: dict = {}
    if creator_ids:
        from app.models.user import User
        name_rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(creator_ids)))
        ).all()
        names = {uid: nm for uid, nm in name_rows}
    reads = []
    for it in items:
        read = ActivityRead.model_validate(it, from_attributes=True)
        nm = names.get(getattr(it, "created_by_id", None))
        if nm:
            read.user_name = nm
        reads.append(read)
    return PaginatedResponse.build(reads, total, pagination.skip, pagination.limit)


@router.get("/me/calls-today")
async def my_calls_today(session: DBSession, current_user: CurrentUser):
    # Use the SAME cutoff the rep's daily call report uses, so the live tile and
    # the report always agree on where "today" starts. Previously this endpoint
    # hardcoded a 6:00 AM IST cutoff while the US report config was 7:30 AM IST,
    # so the tile reset ~1.5h early and looked like calls were "lost" (Mahesh,
    # 2026-06-13). Now both read the configured cutoff_hour/minute/timezone.
    from app.services.us_pod_call_report import (
        load_sales_report_settings,
        _latest_completed_report_cutoff,
    )

    reference = datetime.now(timezone.utc)
    config_key, config_defaults = _pod_report_config_key(current_user.email)
    try:
        report_config = await load_sales_report_settings(session, key=config_key, defaults=config_defaults)
        # Start of the currently-open report day = the most recent cutoff boundary
        # at or before now (subtracts a day automatically when now is before today's
        # cutoff). This is the exact boundary the daily report uses.
        period_start_naive = (
            _latest_completed_report_cutoff(reference, report_config)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        cutoff_tz = report_config["cutoff_timezone"]
        cutoff_label = f"{report_config['cutoff_hour']:02d}:{report_config['cutoff_minute']:02d}"
    except Exception:  # fall back to the legacy fixed cutoff rather than 500 the tile
        local_reference = reference.astimezone(REPORT_CUTOFF_TIMEZONE)
        period_start_date = local_reference.date()
        if local_reference.time() < time(REPORT_CUTOFF_HOUR):
            period_start_date -= timedelta(days=1)
        period_start_naive = (
            datetime.combine(period_start_date, time(REPORT_CUTOFF_HOUR), tzinfo=REPORT_CUTOFF_TIMEZONE)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        cutoff_tz = str(REPORT_CUTOFF_TIMEZONE)
        cutoff_label = f"{REPORT_CUTOFF_HOUR:02d}:00"

    period_start = period_start_naive
    period_end = reference.replace(tzinfo=None)

    total = (
        await session.execute(
            select(func.count(Activity.id)).where(
                Activity.created_by_id == current_user.id,
                or_(
                    func.lower(Activity.type) == "call",
                    func.lower(Activity.medium) == "call",
                ),
                Activity.created_at >= period_start,
                Activity.created_at < period_end,
            )
        )
    ).scalar_one()
    return {
        "total": int(total or 0),
        "timezone": cutoff_tz,
        "cutoff": cutoff_label,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }


@router.post("/", response_model=ActivityRead, status_code=201)
async def create_activity(payload: ActivityCreate, session: DBSession, current_user: CurrentUser, background_tasks: BackgroundTasks):
    # Stamp the creator so manual call / LinkedIn / note activities have rep
    # attribution, without requiring the frontend to send it.
    data = payload.model_dump()
    if not data.get("created_by_id"):
        data["created_by_id"] = current_user.id
    if not data.get("source"):
        data["source"] = "manual"
    activity = await ActivityRepository(session).create(data)

    # The deal-page call logger records the disposition in event_metadata rather
    # than PATCHing the contact's call_disposition field, so booking a demo there
    # used to be a backend no-op — no status advance, no "meeting booked" bell
    # alert. Run the same meeting-booked side-effects the prospect-page PATCH
    # triggers. Guard: if the contact's call_disposition already equals this
    # disposition, the prospect-page PATCH path applied the effects for this same
    # call moments ago, so skip to avoid duplicate work (the alert is deduped
    # regardless). refresh_tasks=False because the background task below already
    # reconciles system tasks for this contact.
    meta = data.get("event_metadata") if isinstance(data.get("event_metadata"), dict) else None
    disp = meta.get("call_disposition") if meta else None
    if activity.contact_id and is_meeting_booked_disposition(disp):
        from app.models.contact import Contact

        contact = await session.get(Contact, activity.contact_id)
        if contact and contact.call_disposition != disp:
            await apply_call_disposition_effects(
                session, contact, disposition=disp, refresh_tasks=False
            )
            await session.commit()

    if activity.deal_id or activity.contact_id:
        background_tasks.add_task(_refresh_tasks_after_activity_background, activity.deal_id, activity.contact_id)
    return activity


@router.get("/{activity_id}", response_model=ActivityRead)
async def get_activity(activity_id: UUID, session: DBSession, _user: CurrentUser):
    return await ActivityRepository(session).get_or_raise(activity_id)


@router.put("/{activity_id}", response_model=ActivityRead)
async def update_activity(activity_id: UUID, payload: ActivityUpdate, session: DBSession, _user: CurrentUser):
    repo = ActivityRepository(session)
    activity = await repo.get_or_raise(activity_id)
    return await repo.update(activity, payload.model_dump(exclude_unset=True))


@router.delete("/{activity_id}", status_code=204)
async def delete_activity(activity_id: UUID, session: DBSession, _user: CurrentUser):
    repo = ActivityRepository(session)
    activity = await repo.get_or_raise(activity_id)
    await repo.delete(activity)
