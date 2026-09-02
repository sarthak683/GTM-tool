import logging
import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.core.dependencies import AdminUser, CurrentUser, DBSession, Pagination
from app.core.exceptions import NotFoundError, ValidationError
from app.models.activity import Activity, ActivityRead
from app.models.contact import Contact
from app.models.deal_stage_history import DealStageHistory, DealStageHistoryRead
from app.models.deal import (
    ALL_STAGES, DEAL_STAGES, PROSPECT_STAGES, PRIORITIES,
    Deal, DealContactCreate, DealContactRead, DealCreate, DealRead, DealUpdate,
)
from app.models.user import User
from app.repositories.deal import DealRepository, can_see_deal, deal_visibility_filter
from app.schemas.common import PaginatedResponse
from app.services.close_reason_backfill import backfill_close_reasons
from app.services.company_stage_milestones import record_deal_stage_milestone
from app.services.contact_access import get_visible_contact
from app.services.deal_stage_history import CLOSE_REASONS, record_stage_transition
from app.services.deal_stages import get_configured_deal_stage_ids, get_configured_default_deal_stage
from app.services.meddpicc_assist import generate_meddpicc_assist
from app.services.permissions import can_view_all_deals
from app.services.realtime import broadcaster
from app.services.timeline import build_deal_timeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/deals", tags=["deals"])


async def _valid_stages(session, pipeline_type: str) -> frozenset[str]:
    return frozenset(await get_configured_deal_stage_ids(session)) if pipeline_type == "deal" else frozenset(PROSPECT_STAGES)


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _default_next_step_due() -> datetime:
    """3 business days out at 09:00 (naive UTC). Reps weren't setting due dates,
    so the reminder path stayed dark; defaulting one when a next step is set
    gives deal_reminders a concrete target the AE can still edit."""
    d = datetime.utcnow()
    added = 0
    while added < 3:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            added += 1
    return d.replace(hour=9, minute=0, second=0, microsecond=0)


def _summarize_text_change(label: str, value: str | None) -> str:
    if not value:
        return f"{label} cleared"
    compact = " ".join(value.split())
    preview = compact if len(compact) <= 120 else f"{compact[:117]}..."
    return f"{label} updated: {preview}"


# ── Win/loss close reasons ───────────────────────────────────────────────────

# Stages whose transitions carry a close reason. Every other target stage
# ignores/nulls the reason so system moves and ordinary lane changes stay
# reason-less.
_CLOSE_REASON_STAGES = frozenset({"closed_won", "closed_lost"})


def _normalize_close_reason(
    target_stage: str, reason: str | None, reason_detail: str | None
) -> tuple[str | None, str | None]:
    """Validate and normalize the win/loss reason for a stage move.

    - closed_lost REQUIRES a reason (422 otherwise);
    - closed_won: reason optional;
    - any other target stage: reason/detail are ignored (nulled).
    Reasons must come from the shared CLOSE_REASONS enum — the win/loss
    rollup matches on exactly those values.
    """
    reason = (reason or "").strip() or None
    detail = (reason_detail or "").strip() or None
    if target_stage not in _CLOSE_REASON_STAGES:
        return None, None
    if reason is not None and reason not in CLOSE_REASONS:
        raise ValidationError(f"Invalid close reason. Must be one of: {sorted(CLOSE_REASONS)}")
    if target_stage == "closed_lost" and reason is None:
        raise ValidationError("A close reason is required when moving a deal to closed_lost.")
    return reason, detail


def _close_qualification(
    deal: Deal, target_stage: str, reason: str, reason_detail: str | None, at: datetime
) -> dict:
    """Deal.qualification with the close reason merged in.

    Returns a NEW dict — in-place mutation of the JSONB attribute is a silent
    no-op (no change event, nothing persisted). ``close_reason`` holds the
    enum value; ``close_reason_detail`` the optional free text. The
    ``close_outcome`` / ``closed_reason_at`` keys mirror what the drawer's
    old free-text flow wrote, so existing readers keep working.
    """
    qualification = dict(deal.qualification or {})
    qualification["close_reason"] = reason
    qualification["close_reason_detail"] = reason_detail
    qualification["close_outcome"] = "won" if target_stage == "closed_won" else "lost"
    qualification["closed_reason_at"] = at.isoformat()
    return qualification


# ── Board ────────────────────────────────────────────────────────────────────

@router.get("/board", response_model=dict[str, list[DealRead]])
async def deal_board(
    session: DBSession,
    _user: CurrentUser,
    pipeline_type: str = Query(default="deal"),
):
    """Return deals grouped by stage for kanban board display."""
    view_all = _user.is_admin or await can_view_all_deals(session, _user)
    return await DealRepository(session).board(
        pipeline_type,
        user_id=_user.id,
        is_admin=view_all,
    )


@router.get("/board/stream")
async def deal_board_stream(_user: CurrentUser):
    """Server-Sent Events stream of deal-board changes.

    Pushes a lightweight event (kind + deal_id + stage) whenever a deal is
    created, updated, moved, or deleted anywhere in the workspace. The client
    treats each event as a "refetch the board" signal — the stream carries no
    payload, so the broker never lags behind the canonical store. A 25s
    heartbeat keeps corporate proxies from culling idle connections.
    """
    async def event_generator():
        q = await broadcaster.subscribe()
        try:
            async for chunk in broadcaster.stream(q):
                yield chunk
        finally:
            await broadcaster.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering
        },
    )


# ── List (paginated, backward-compatible) ────────────────────────────────────

@router.get("/", response_model=PaginatedResponse[DealRead])
async def list_deals(
    session: DBSession,
    _user: CurrentUser,
    pagination: Pagination,
    company_id: Optional[UUID] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    pipeline_type: Optional[str] = Query(default=None),
):
    repo = DealRepository(session)
    # Scope to deals the caller may see (admins + view-all grantees see all).
    # Keep first so it ANDs with every other filter.
    view_all = _user.is_admin or await can_view_all_deals(session, _user)
    filters = [deal_visibility_filter(_user.id, view_all)]
    if company_id:
        filters.append(Deal.company_id == company_id)
    if stage:
        filters.append(Deal.stage == stage)
    if pipeline_type:
        filters.append(Deal.pipeline_type == pipeline_type)
    items, total = await repo.list_paginated(
        *filters,
        skip=pagination.skip,
        limit=pagination.limit,
        # id tiebreaker: created_at alone is non-unique, and OFFSET pagination
        # over a non-unique sort repeats/drops rows across pages.
        order_by=(Deal.created_at.desc(), Deal.id.desc()),
    )
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


# ── Trash ────────────────────────────────────────────────────────────────────
# ROUTE ORDER: "/trash" MUST stay above "/{deal_id}" — FastAPI matches in
# declaration order, so a literal segment declared after the UUID param route
# never gets reached (the request 422s on uuid parsing instead).


class DealTrashRow(BaseModel):
    id: UUID
    name: str
    stage: Optional[str] = None
    amount: Optional[float] = None  # Deal.value, surfaced under the API's name
    company_name: Optional[str] = None
    deleted_at: Optional[datetime] = None


class DealRestoreResponse(BaseModel):
    id: UUID
    name: str
    tasks_reopened: int


@router.post("/backfill-close-reasons", response_model=dict)
async def backfill_deal_close_reasons(
    session: DBSession,
    _admin: AdminUser,
    dry_run: bool = Query(True, description="Preview only. Defaults to TRUE — nothing is written unless you pass dry_run=false."),
    limit: int | None = Query(None, ge=1, le=1000),
):
    """Structure historical free-text close reasons onto the enum (admin).

    The close-reason dropdown shipped on 2026-08-17; every reason written before
    that is prose sitting in the enum's field. This moves the prose to
    ``close_reason_detail`` and puts the matching enum value in
    ``close_reason`` — nothing is overwritten, and rows the rules cannot place
    confidently are returned under ``unmatched`` for a human rather than being
    filed as "other".

    Dry-run by default. Run it once to read the proposals, then again with
    ``dry_run=false`` if they look right.
    """
    return await backfill_close_reasons(session, dry_run=dry_run, limit=limit)


@router.get("/trash", response_model=list[DealTrashRow])
async def list_deal_trash(
    session: DBSession,
    _admin: AdminUser,
    limit: int = Query(default=50, ge=1, le=500),
):
    """Soft-deleted deals, newest deletion first (admin).

    Includes deals that went down with a soft-deleted company — restoring the
    ACCOUNT brings those back in one step (POST /companies/{id}/restore), which
    is usually what you want; restoring one from here revives a single deal
    under a still-deleted account, where it stays invisible on the board.
    """
    from app.models.company import Company

    rows = (
        await session.execute(
            select(
                Deal.id,
                Deal.name,
                Deal.stage,
                Deal.value,
                Deal.deleted_at,
                Company.name.label("company_name"),
            )
            .outerjoin(Company, Company.id == Deal.company_id)
            .where(Deal.deleted_at.is_not(None))
            .order_by(Deal.deleted_at.desc(), Deal.id)
            .limit(limit)
        )
    ).all()
    return [
        DealTrashRow(
            id=row.id,
            name=row.name,
            stage=row.stage,
            amount=float(row.value) if row.value is not None else None,
            company_name=row.company_name,
            deleted_at=row.deleted_at,
        )
        for row in rows
    ]


# ── Create ───────────────────────────────────────────────────────────────────

@router.post("/", response_model=DealRead, status_code=201)
async def create_deal(payload: DealCreate, session: DBSession, _user: CurrentUser):
    data = payload.model_dump()

    # Company is mandatory — every deal must be linked to an account so pipeline,
    # analytics and stakeholder linking have an anchor (Annie 2026-06-17).
    if not data.get("company_id"):
        raise ValidationError("A company is required to create a deal.")

    # Default stage based on pipeline type
    if not data.get("stage"):
        data["stage"] = await get_configured_default_deal_stage(session) if data.get("pipeline_type", "deal") == "deal" else "cold_account"

    valid = await _valid_stages(session, data.get("pipeline_type", "deal"))
    if data["stage"] not in valid:
        raise ValidationError(f"Invalid stage for {data['pipeline_type']}. Must be one of: {sorted(valid)}")

    data["stage_entered_at"] = datetime.utcnow()
    # Seed next_step_updated_at iff the create payload actually carries a
    # next_step note (so the card shows a real date on first render).
    if (data.get("next_step") or "").strip():
        data["next_step_updated_at"] = datetime.utcnow()
    deal = await DealRepository(session).create(data)

    # Auto-log activity
    activity = Activity(
        deal_id=deal.id,
        type="deal_created",
        source="system",
        content=f"Deal created in {deal.pipeline_type} pipeline",
        created_by_id=_user.id,
    )
    session.add(activity)
    await record_deal_stage_milestone(
        session,
        deal=deal,
        stage=deal.stage,
        reached_at=deal.stage_entered_at or deal.created_at,
        source="deal_created",
    )
    await record_stage_transition(
        session,
        deal_id=deal.id,
        from_stage=None,
        to_stage=deal.stage,
        changed_by_id=_user.id,
        source="deal_created",
        changed_at=deal.stage_entered_at,
    )
    # Attach the account's existing contacts so a new deal opens with its people
    # already linked (link-only; meeting/email signals don't exist yet on a
    # brand-new deal, and the daily reconcile fills those in).
    if deal.company_id:
        try:
            from app.services.deal_linker import reconcile_deal_stakeholders
            await reconcile_deal_stakeholders(session, deal, create_from_signals=False)
        except Exception:
            logger.exception("deal create: stakeholder link failed for %s", deal.id)
        # A live deal on the account means the account is in the pipeline —
        # reflect that on the account status (forward-only; no-op if the account
        # is already past this stage or manually parked).
        try:
            from app.services.account_status import bump_company_account_status
            await bump_company_account_status(
                session, deal.company_id, "in_pipeline"
            )
        except Exception:
            logger.exception("deal create: account_status bump failed for %s", deal.company_id)
    await session.commit()

    broadcaster.publish_deal_change("deal.created", str(deal.id), deal.stage)

    return await DealRepository(session).get_with_joins(deal.id) or deal


# ── Bulk actions ─────────────────────────────────────────────────────────────

class BulkDealUpdate(BaseModel):
    deal_ids: list[UUID]
    # stage: move all selected deals to this stage (validated per pipeline_type)
    stage: Optional[str] = None
    # Win/loss close reason (shared CLOSE_REASONS enum) + optional free text.
    # Required when stage=closed_lost, optional for closed_won, ignored for
    # every other stage. Applies to ALL selected deals.
    reason: Optional[str] = None
    reason_detail: Optional[str] = None
    # add_tags: union these tags into each deal's existing tags (no removals)
    add_tags: Optional[list[str]] = None
    # reassign=True applies assigned_to_id (which may be None to unassign);
    # the flag distinguishes "set to unassigned" from "leave owner alone".
    reassign: bool = False
    assigned_to_id: Optional[UUID] = None


@router.post("/bulk-update", response_model=dict)
async def bulk_update_deals(payload: BulkDealUpdate, session: DBSession, _user: CurrentUser):
    """Apply the same change (stage / owner / tags) to many deals at once.

    Mirrors the per-deal update side effects: stage moves record a stage_change
    activity, a stage milestone, and a stage-history transition so velocity and
    the timeline stay accurate. Missing deal ids are skipped.
    """
    if not payload.deal_ids:
        raise ValidationError("deal_ids is required")
    if len(payload.deal_ids) > 200:
        raise ValidationError("Too many deals in one bulk update (max 200)")

    repo = DealRepository(session)
    now = datetime.utcnow()
    valid_cache: dict[str, frozenset[str]] = {}
    updated = 0
    # One IN() fetch instead of up-to-200 session.get round trips. Scope the
    # fetch to deals the caller may see so a non-admin can't mutate deals they
    # don't own; ids they can't see are simply absent from the map and the loop
    # skips them (no error — same as a missing id, so a mixed batch still
    # applies to the visible deals).
    deals_by_id = {
        deal.id: deal
        for deal in (
            await session.execute(
                DealRepository.visible_to(_user).where(
                    Deal.id.in_(payload.deal_ids),
                )
            )
        ).scalars()
    }

    # Validate every stage transition BEFORE mutating anything. The check used
    # to run mid-loop with per-deal commits, so one prospect mixed into a deal
    # selection 422'd the request AFTER earlier deals were already committed
    # (and the most recent one lost its audit/stage-history rows to the
    # rollback). Now an invalid stage fails cleanly with nothing changed.
    if payload.stage:
        for deal in deals_by_id.values():
            if payload.stage == deal.stage:
                continue
            if deal.pipeline_type not in valid_cache:
                valid_cache[deal.pipeline_type] = await _valid_stages(session, deal.pipeline_type)
            if payload.stage not in valid_cache[deal.pipeline_type]:
                raise ValidationError(f"Invalid stage. Must be one of: {sorted(valid_cache[deal.pipeline_type])}")

    # Close reason: validated up-front (before any mutation) so a missing
    # reason on a closed_lost bulk move 422s with nothing changed.
    close_reason: Optional[str] = None
    close_reason_detail: Optional[str] = None
    if payload.stage:
        close_reason, close_reason_detail = _normalize_close_reason(
            payload.stage, payload.reason, payload.reason_detail
        )

    for deal_id in payload.deal_ids:
        deal = deals_by_id.get(deal_id)
        if deal is None:
            continue

        update_data: dict = {}
        previous_stage = deal.stage
        stage_changed = False

        if payload.stage and payload.stage != deal.stage:
            update_data["stage"] = payload.stage
            update_data["stage_entered_at"] = now
            update_data["days_in_stage"] = 0
            stage_changed = True

        if payload.reassign:
            update_data["assigned_to_id"] = payload.assigned_to_id

        if payload.add_tags:
            existing = list(deal.tags or [])
            update_data["tags"] = existing + [t for t in payload.add_tags if t and t not in existing]

        if not update_data:
            continue

        if stage_changed and close_reason:
            # New dict per deal (JSONB in-place mutation is a silent no-op).
            update_data["qualification"] = _close_qualification(
                deal, payload.stage, close_reason, close_reason_detail, now
            )

        update_data["updated_at"] = now
        upd = await repo.update(deal, update_data)
        updated += 1

        if stage_changed:
            session.add(Activity(
                deal_id=deal_id, type="stage_change", source="system",
                content=f"Stage moved from {previous_stage} to {upd.stage} (bulk)",
                created_by_id=_user.id,
            ))
            await record_deal_stage_milestone(
                session, deal=upd, stage=upd.stage,
                reached_at=upd.stage_entered_at or upd.updated_at, source="bulk_update",
            )
            await record_stage_transition(
                session, deal_id=deal_id, from_stage=previous_stage, to_stage=upd.stage,
                changed_by_id=_user.id, source="bulk_update", changed_at=upd.stage_entered_at,
                reason=close_reason,
            )

    await session.commit()
    for deal_id in updated:
        broadcaster.publish_deal_change("deal.updated", str(deal_id))
    return {"updated": updated}


# ── Get single ───────────────────────────────────────────────────────────────

@router.get("/{deal_id}", response_model=DealRead)
async def get_deal(deal_id: UUID, session: DBSession, _user: CurrentUser):
    view_all = _user.is_admin or await can_view_all_deals(session, _user)
    result = await DealRepository(session).get_with_joins(
        deal_id, user_id=_user.id, is_admin=view_all
    )
    if not result:
        # 404 (not 403) so a non-admin can't probe which deal ids exist.
        raise NotFoundError(f"Deal {deal_id} not found")
    return result


# ── Update ───────────────────────────────────────────────────────────────────

@router.put("/{deal_id}", response_model=DealRead)
async def update_deal(deal_id: UUID, payload: DealUpdate, session: DBSession, _user: CurrentUser):
    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)
    view_all = _user.is_admin or await can_view_all_deals(session, _user)
    if not can_see_deal(deal, _user, view_all):
        raise NotFoundError(f"Deal {deal_id} not found")
    update_data = payload.model_dump(exclude_unset=True)
    stage_changed = False
    previous_stage = deal.stage
    previous_company_id = deal.company_id

    if "description" in update_data:
        update_data["description"] = _normalize_optional_text(update_data.get("description"))
    if "next_step" in update_data:
        update_data["next_step"] = _normalize_optional_text(update_data.get("next_step"))
        # Default a due date when a rep sets a next step without one, so Beacon
        # reminds them. Skip if the same request sets/clears the due explicitly,
        # or the deal already has one (don't override).
        if (
            update_data["next_step"]
            and "next_step_due_at" not in update_data
            and deal.next_step_due_at is None
        ):
            update_data["next_step_due_at"] = _default_next_step_due()
    if "qualification_reason" in update_data:
        update_data["qualification_reason"] = _normalize_optional_text(update_data.get("qualification_reason"))

    # Validate stage if changed
    if "stage" in update_data and update_data["stage"] != deal.stage:
        pt = update_data.get("pipeline_type", deal.pipeline_type)
        valid = await _valid_stages(session, pt)
        if update_data["stage"] not in valid:
            raise ValidationError(f"Invalid stage. Must be one of: {sorted(valid)}")
        update_data["stage_entered_at"] = datetime.utcnow()
        update_data["days_in_stage"] = 0
        stage_changed = True

    # Detect demo reschedule: close_date_est changed to a new value on a
    # demo_scheduled deal that already had a date set. Logged as a separate
    # activity so analytics can count it independently from general field changes.
    close_date_rescheduled = (
        "close_date_est" in update_data
        and update_data["close_date_est"] is not None
        and deal.close_date_est is not None         # had a date before → reschedule
        and str(update_data["close_date_est"]) != str(deal.close_date_est)  # actually changed
        and deal.stage == "demo_scheduled"
    )

    # Any close date edit (set, change, clear) is a visible event in the deal
    # timeline. The demo-scheduled reschedule branch above keeps its dedicated
    # `demo_rescheduled` activity for analytics; here we cover every other case
    # (initial set on a new deal, push/extend on later stages, clearing) so the
    # AE can see when their forecast date moved and why.
    close_date_changed = (
        "close_date_est" in update_data
        and str(update_data["close_date_est"]) != str(deal.close_date_est)
    )

    # Auto-log field changes
    changes: list[str] = []
    if "value" in update_data and update_data["value"] != deal.value:
        changes.append(f"Amount changed to ${update_data['value']}")
    if "priority" in update_data and update_data["priority"] != deal.priority:
        changes.append(f"Priority changed to {update_data['priority']}")
    if "priority_tag" in update_data and update_data["priority_tag"] != deal.priority_tag:
        changes.append(f"Priority changed to {update_data['priority_tag'] or 'none'}")
    if "assigned_to_id" in update_data and str(update_data.get("assigned_to_id")) != str(deal.assigned_to_id):
        changes.append("Assignee changed")
    if "commit_to_deal" in update_data and update_data["commit_to_deal"] != deal.commit_to_deal:
        label = "committed" if update_data["commit_to_deal"] else "uncommitted"
        changes.append(f"Deal {label}")
    if close_date_changed and not close_date_rescheduled:
        # Suppressed when the demo-rescheduled branch already logs a dedicated
        # activity for the same write — otherwise the timeline gets two entries
        # for one edit.
        old_close = deal.close_date_est.isoformat() if deal.close_date_est else None
        new_close = update_data["close_date_est"]
        new_iso = new_close.isoformat() if new_close else None
        if new_iso and old_close:
            changes.append(f"Close date changed from {old_close} to {new_iso}")
        elif new_iso:
            changes.append(f"Close date set to {new_iso}")
        else:
            changes.append(f"Close date cleared (was {old_close})")
    if "next_step" in update_data and update_data["next_step"] != _normalize_optional_text(deal.next_step):
        changes.append(_summarize_text_change("Next step", update_data["next_step"]))
        # Only stamp next_step_updated_at when the text itself changed —
        # ignore no-op writes that send the same string back.
        update_data["next_step_updated_at"] = datetime.utcnow()
    if "qualification_reason" in update_data and update_data["qualification_reason"] != _normalize_optional_text(deal.qualification_reason):
        changes.append(_summarize_text_change("Qualification criteria", update_data["qualification_reason"]))
    if "description" in update_data and update_data["description"] != _normalize_optional_text(deal.description):
        changes.append(_summarize_text_change("Description", update_data["description"]))

    update_data["updated_at"] = datetime.utcnow()

    # Stage audit rows are added BEFORE repo.update — which commits internally —
    # so the deal row and its history/activity land in ONE transaction. The old
    # order (update-commit, then history, then a second commit) could crash in
    # between and leave a moved deal with no audit row, which is exactly the
    # drift stage-history analytics cannot detect.
    if stage_changed:
        session.add(
            Activity(
                deal_id=deal_id,
                type="stage_change",
                source="system",
                content=f"Stage moved from {previous_stage} to {update_data['stage']}",
                created_by_id=_user.id,
            )
        )
        await record_stage_transition(
            session,
            deal_id=deal_id,
            from_stage=previous_stage,
            to_stage=update_data["stage"],
            changed_by_id=_user.id,
            source="deal_update",
            changed_at=update_data.get("stage_entered_at"),
        )

    updated = await repo.update(deal, update_data)

    # Re-link the account's contacts when the deal is pointed at a (new) company.
    company_changed = updated.company_id is not None and updated.company_id != previous_company_id
    if company_changed:
        try:
            from app.services.deal_linker import reconcile_deal_stakeholders
            await reconcile_deal_stakeholders(session, updated, create_from_signals=False)
        except Exception:
            logger.exception("deal update: stakeholder link failed for %s", deal_id)

    if stage_changed:
        await record_deal_stage_milestone(
            session,
            deal=updated,
            stage=updated.stage,
            reached_at=updated.stage_entered_at or updated.updated_at,
            source="deal_update",
        )

    if changes:
        activity = Activity(
            deal_id=deal_id,
            type="field_change",
            source="system",
            content="; ".join(changes),
            created_by_id=_user.id,
        )
        session.add(activity)

    if close_date_rescheduled:
        session.add(Activity(
            deal_id=deal_id,
            type="demo_rescheduled",
            source="system",
            content=f"Demo rescheduled to {update_data['close_date_est']}",
            created_by_id=_user.id,
        ))

    if stage_changed or changes or close_date_rescheduled or company_changed:
        await session.commit()

    kind = "deal.stage_changed" if stage_changed else "deal.updated"
    broadcaster.publish_deal_change(kind, str(deal_id), updated.stage)

    return await repo.get_with_joins(deal_id) or updated


@router.patch("/{deal_id}", response_model=DealRead)
async def patch_deal(deal_id: UUID, payload: DealUpdate, session: DBSession, _user: CurrentUser):
    """PATCH alias for update_deal — same logic."""
    return await update_deal(deal_id, payload, session, _user)


@router.post("/{deal_id}/meddpicc/auto-fill", response_model=DealRead)
async def auto_fill_meddpicc(deal_id: UUID, session: DBSession, _user: CurrentUser):
    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)

    assist_payload = await generate_meddpicc_assist(session, deal)
    qualification = dict(deal.qualification or {})
    qualification["meddpicc"] = assist_payload["meddpicc"]
    qualification["meddpicc_ai"] = assist_payload["meddpicc_ai"]

    updated = await repo.update(
        deal,
        {
            "qualification": qualification,
            "updated_at": datetime.utcnow(),
        },
    )
    session.add(
        Activity(
            deal_id=deal_id,
            type="qualification_update",
            source="beacon_ai",
            content="Beacon AI refreshed MEDDPICC from current deal evidence.",
        )
    )
    await session.commit()

    return await repo.get_with_joins(deal_id) or updated


# ── Stage move ───────────────────────────────────────────────────────────────

class DealStageMoveRequest(BaseModel):
    stage: Optional[str] = None
    # Win/loss close reason (shared CLOSE_REASONS enum) + optional free text.
    # Required when stage=closed_lost, optional for closed_won, ignored for
    # every other target stage.
    reason: Optional[str] = None
    reason_detail: Optional[str] = None


@router.patch("/{deal_id}/stage", response_model=DealRead)
async def move_stage(deal_id: UUID, body: DealStageMoveRequest, session: DBSession, _user: CurrentUser):
    new_stage = body.stage
    if not new_stage:
        raise ValidationError("stage is required")

    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)
    view_all = _user.is_admin or await can_view_all_deals(session, _user)
    if not can_see_deal(deal, _user, view_all):
        raise NotFoundError(f"Deal {deal_id} not found")

    valid = await _valid_stages(session, deal.pipeline_type)
    if new_stage not in valid:
        raise ValidationError(f"Invalid stage for {deal.pipeline_type}. Must be one of: {sorted(valid)}")

    old_stage = deal.stage
    if new_stage == old_stage:
        return await repo.get_with_joins(deal_id)

    close_reason, close_reason_detail = _normalize_close_reason(
        new_stage, body.reason, body.reason_detail
    )

    transition_at = datetime.utcnow()
    # Audit rows FIRST, then repo.update (which commits) — one transaction, so
    # a crash can never leave a moved deal without its history row.
    session.add(
        Activity(
            deal_id=deal_id,
            type="stage_change",
            source="system",
            content=f"Stage moved from {old_stage} to {new_stage}",
            created_by_id=_user.id,
        )
    )
    await record_stage_transition(
        session,
        deal_id=deal_id,
        from_stage=old_stage,
        to_stage=new_stage,
        changed_by_id=_user.id,
        source="stage_move",
        changed_at=transition_at,
        reason=close_reason,
    )
    update_data: dict = {
        "stage": new_stage,
        "stage_entered_at": transition_at,
        "days_in_stage": 0,
        "updated_at": transition_at,
    }
    if close_reason:
        # New dict, never in-place (JSONB mutation without reassignment is a
        # silent no-op that persists nothing).
        update_data["qualification"] = _close_qualification(
            deal, new_stage, close_reason, close_reason_detail, transition_at
        )
    await repo.update(deal, update_data)

    await record_deal_stage_milestone(
        session,
        deal=deal,
        stage=new_stage,
        reached_at=deal.stage_entered_at or deal.updated_at,
        source="stage_move",
    )
    await session.commit()

    broadcaster.publish_deal_change("deal.stage_changed", str(deal_id), new_stage)

    return await repo.get_with_joins(deal_id)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{deal_id}", status_code=204)
async def delete_deal(deal_id: UUID, session: DBSession, _admin: AdminUser):
    """SOFT-delete a deal (admin).

    The old hard cascade removed the deal's activities and (via FK CASCADE) its
    stage-history rows, so deleting a test/dead deal retroactively changed
    every historical outcome metric built on that history. Now the deal just
    leaves current-state surfaces via deleted_at; open system tasks on it are
    dismissed so they don't nag about an invisible deal.

    Reversible: GET /deals/trash lists what is in here and
    POST /deals/{id}/restore brings a deal back (Settings -> Trash in the UI).
    The tasks dismissed above stay dismissed — see the restore docstring.
    """
    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)
    if deal.deleted_at is None:
        deal.deleted_at = datetime.utcnow()
        deal.updated_at = deal.deleted_at
        session.add(deal)
        from sqlalchemy import update as sa_update

        from app.models.task import Task

        await session.execute(
            sa_update(Task)
            .where(Task.entity_type == "deal", Task.entity_id == deal_id, Task.status == "open")
            .values(status="dismissed", updated_at=datetime.utcnow())
            .execution_options(synchronize_session=False)
        )
        await session.commit()

        broadcaster.publish_deal_change("deal.deleted", str(deal_id), deal.stage)


@router.post("/{deal_id}/restore", response_model=DealRestoreResponse)
async def restore_deal(deal_id: UUID, session: DBSession, _admin: AdminUser):
    """Bring a soft-deleted deal back onto the board (admin).

    Restores the DEAL ROW ONLY. Its activities, stage history, milestones and
    contact links were never removed by the soft delete, so they are already
    attached and come back with it — the board, pipeline value and stage
    history all read correctly again the moment deleted_at clears.

    What does NOT come back: the open tasks ``delete_deal`` dismissed. They stay
    dismissed on purpose — a task's due date does not pause while the deal sits
    in the trash, so re-opening them would hand the owner a pile of
    already-overdue nags instead of a working deal. The task rows and their
    comments are intact; re-open the ones that still matter from the task
    center.

    A deal whose COMPANY is still soft-deleted restores fine but stays hidden
    on company-scoped surfaces — restore the account instead
    (POST /companies/{id}/restore), which brings its deals with it.
    """
    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)
    if deal.deleted_at is None:
        raise NotFoundError(f"Deal {deal_id} is not in the trash")

    deal.deleted_at = None
    deal.updated_at = datetime.utcnow()
    session.add(deal)
    await session.commit()
    return DealRestoreResponse(id=deal.id, name=deal.name, tasks_reopened=0)


# ── Deal Contacts ────────────────────────────────────────────────────────────

@router.get("/{deal_id}/contacts", response_model=list[DealContactRead])
async def list_deal_contacts(deal_id: UUID, session: DBSession, _user: CurrentUser):
    repo = DealRepository(session)
    await repo.get_or_raise(deal_id)
    return await repo.list_contacts(deal_id)


@router.post("/{deal_id}/contacts", response_model=DealContactRead, status_code=201)
async def add_deal_contact(deal_id: UUID, body: DealContactCreate, session: DBSession, _user: CurrentUser):
    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)

    # Idempotent link behavior for repeated client retries.
    existing_contacts = await repo.list_contacts(deal_id)
    existing = next((c for c in existing_contacts if c.contact_id == body.contact_id), None)
    if existing:
        return existing

    # Verify contact exists
    contact = await get_visible_contact(session, _user, body.contact_id)

    dc = await repo.add_contact(deal_id, body.contact_id, body.role)

    # Auto-log
    name = f"{contact.first_name} {contact.last_name}"
    role_str = f" as {body.role}" if body.role else ""
    activity = Activity(
        deal_id=deal_id,
        type="contact_linked",
        source="system",
        content=f"Contact {name} linked{role_str}",
        contact_id=body.contact_id,
    )
    session.add(activity)
    await session.commit()

    contacts = await repo.list_contacts(deal_id)
    return next((c for c in contacts if c.contact_id == body.contact_id), dc)


@router.post("/{deal_id}/contacts/{contact_id}", response_model=DealContactRead, status_code=201)
async def add_deal_contact_by_path(deal_id: UUID, contact_id: UUID, session: DBSession, _user: CurrentUser):
    """Backward-compatible route used by older clients/tests."""
    payload = DealContactCreate(contact_id=contact_id)
    return await add_deal_contact(deal_id, payload, session, _user)


@router.delete("/{deal_id}/contacts/{contact_id}", status_code=204)
async def remove_deal_contact(deal_id: UUID, contact_id: UUID, session: DBSession, _user: CurrentUser):
    repo = DealRepository(session)
    await repo.get_or_raise(deal_id)
    removed = await repo.remove_contact(deal_id, contact_id)
    if not removed:
        raise NotFoundError("Contact not linked to this deal")


# ── Deal Activities ──────────────────────────────────────────────────────────

@router.get("/{deal_id}/timeline")
async def list_deal_timeline(
    deal_id: UUID,
    session: DBSession,
    _user: CurrentUser,
    limit: int = Query(default=150, ge=1, le=500),
):
    """Unified chronological timeline: activities + meetings, newest first."""
    await DealRepository(session).get_or_raise(deal_id)
    return {"items": await build_deal_timeline(session, deal_id, limit=limit)}


@router.get("/{deal_id}/stage-history", response_model=list[DealStageHistoryRead])
async def list_deal_stage_history(deal_id: UUID, session: DBSession, _user: CurrentUser):
    """Ordered stage transitions for a deal (oldest first), so the UI can show
    the stage journey and time spent in each stage."""
    rows = (
        await session.execute(
            select(DealStageHistory)
            .where(DealStageHistory.deal_id == deal_id)
            .order_by(DealStageHistory.changed_at.asc())
        )
    ).scalars().all()
    return rows


@router.get("/{deal_id}/activities", response_model=list[ActivityRead])
async def list_deal_activities(deal_id: UUID, session: DBSession, _user: CurrentUser):
    from app.models.meeting import Meeting

    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)

    # Include activities directly on the deal, plus TLDV meeting activities
    # linked via the deal's company (where deal_id wasn't set on the activity)
    filters = [Activity.deal_id == deal_id]
    if deal.company_id:
        tldv_ext_ids = (
            select(("tldv:meeting:" + Meeting.external_source_id))
            .where(
                Meeting.company_id == deal.company_id,
                Meeting.external_source_id.isnot(None),
            )
        )
        tldv_transcript_ids = (
            select(("tldv:transcript:" + Meeting.external_source_id))
            .where(
                Meeting.company_id == deal.company_id,
                Meeting.external_source_id.isnot(None),
            )
        )
        filters.append(Activity.external_source_id.in_(tldv_ext_ids))
        filters.append(Activity.external_source_id.in_(tldv_transcript_ids))

    stmt = (
        select(Activity, User.name.label("user_name"))
        .outerjoin(User, Activity.created_by_id == User.id)
        .where(or_(*filters))
        .order_by(Activity.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    # Deduplicate in case an activity has both deal_id and external_source_id match
    seen: set[UUID] = set()
    result = []
    for act, user_name in rows:
        if act.id in seen:
            continue
        seen.add(act.id)
        read = ActivityRead.model_validate(act)
        read.user_name = user_name
        result.append(read)
    return result


@router.post("/{deal_id}/activities", response_model=ActivityRead, status_code=201)
async def add_deal_comment(deal_id: UUID, body: dict, session: DBSession, user: CurrentUser):
    repo = DealRepository(session)
    await repo.get_or_raise(deal_id)

    content = body.get("body", "").strip()
    if not content:
        raise ValidationError("Comment body is required")

    activity = Activity(
        deal_id=deal_id,
        type="comment",
        source="manual",
        content=content,
        created_by_id=user.id,
    )
    session.add(activity)
    await session.commit()
    await session.refresh(activity)
    read = ActivityRead.model_validate(activity)
    read.user_name = user.name
    return read
