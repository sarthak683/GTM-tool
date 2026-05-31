import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
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
from app.repositories.deal import DealRepository
from app.schemas.common import PaginatedResponse
from app.services.company_stage_milestones import record_deal_stage_milestone
from app.services.deal_stage_history import record_stage_transition
from app.services.deal_stages import get_configured_deal_stage_ids, get_configured_default_deal_stage
from app.services.meddpicc_assist import generate_meddpicc_assist
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


# ── Board ────────────────────────────────────────────────────────────────────

@router.get("/board", response_model=dict[str, list[DealRead]])
async def deal_board(
    session: DBSession,
    _user: CurrentUser,
    pipeline_type: str = Query(default="deal"),
):
    """Return deals grouped by stage for kanban board display."""
    return await DealRepository(session).board(pipeline_type)


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
    filters = []
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
        order_by=Deal.created_at.desc(),
    )
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


# ── Create ───────────────────────────────────────────────────────────────────

@router.post("/", response_model=DealRead, status_code=201)
async def create_deal(payload: DealCreate, session: DBSession, _user: CurrentUser):
    data = payload.model_dump()

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
    await session.commit()

    return await DealRepository(session).get_with_joins(deal.id) or deal


# ── Bulk actions ─────────────────────────────────────────────────────────────

class BulkDealUpdate(BaseModel):
    deal_ids: list[UUID]
    # stage: move all selected deals to this stage (validated per pipeline_type)
    stage: Optional[str] = None
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

    for deal_id in payload.deal_ids:
        deal = await session.get(Deal, deal_id)
        if deal is None:
            continue

        update_data: dict = {}
        previous_stage = deal.stage
        stage_changed = False

        if payload.stage and payload.stage != deal.stage:
            if deal.pipeline_type not in valid_cache:
                valid_cache[deal.pipeline_type] = await _valid_stages(session, deal.pipeline_type)
            if payload.stage not in valid_cache[deal.pipeline_type]:
                raise ValidationError(f"Invalid stage. Must be one of: {sorted(valid_cache[deal.pipeline_type])}")
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

        update_data["updated_at"] = now
        upd = await repo.update(deal, update_data)
        updated += 1

        if stage_changed:
            session.add(Activity(
                deal_id=deal_id, type="stage_change", source="system",
                content=f"Stage moved from {previous_stage} to {upd.stage} (bulk)",
            ))
            await record_deal_stage_milestone(
                session, deal=upd, stage=upd.stage,
                reached_at=upd.stage_entered_at or upd.updated_at, source="bulk_update",
            )
            await record_stage_transition(
                session, deal_id=deal_id, from_stage=previous_stage, to_stage=upd.stage,
                changed_by_id=_user.id, source="bulk_update", changed_at=upd.stage_entered_at,
            )

    await session.commit()
    return {"updated": updated}


# ── Get single ───────────────────────────────────────────────────────────────

@router.get("/{deal_id}", response_model=DealRead)
async def get_deal(deal_id: UUID, session: DBSession, _user: CurrentUser):
    result = await DealRepository(session).get_with_joins(deal_id)
    if not result:
        raise NotFoundError(f"Deal {deal_id} not found")
    return result


# ── Update ───────────────────────────────────────────────────────────────────

@router.put("/{deal_id}", response_model=DealRead)
async def update_deal(deal_id: UUID, payload: DealUpdate, session: DBSession, _user: CurrentUser):
    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)
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

    # Validate stage if changed
    if "stage" in update_data and update_data["stage"] != deal.stage:
        pt = update_data.get("pipeline_type", deal.pipeline_type)
        valid = await _valid_stages(session, pt)
        if update_data["stage"] not in valid:
            raise ValidationError(f"Invalid stage. Must be one of: {sorted(valid)}")
        update_data["stage_entered_at"] = datetime.utcnow()
        update_data["days_in_stage"] = 0
        stage_changed = True

    # Auto-log field changes
    changes: list[str] = []
    if "value" in update_data and update_data["value"] != deal.value:
        changes.append(f"Amount changed to ${update_data['value']}")
    if "priority" in update_data and update_data["priority"] != deal.priority:
        changes.append(f"Priority changed to {update_data['priority']}")
    if "assigned_to_id" in update_data and str(update_data.get("assigned_to_id")) != str(deal.assigned_to_id):
        changes.append("Assignee changed")
    if "commit_to_deal" in update_data and update_data["commit_to_deal"] != deal.commit_to_deal:
        label = "committed" if update_data["commit_to_deal"] else "uncommitted"
        changes.append(f"Deal {label}")
    if "next_step" in update_data and update_data["next_step"] != _normalize_optional_text(deal.next_step):
        changes.append(_summarize_text_change("Next step", update_data["next_step"]))
        # Only stamp next_step_updated_at when the text itself changed —
        # ignore no-op writes that send the same string back.
        update_data["next_step_updated_at"] = datetime.utcnow()
    if "description" in update_data and update_data["description"] != _normalize_optional_text(deal.description):
        changes.append(_summarize_text_change("Description", update_data["description"]))

    update_data["updated_at"] = datetime.utcnow()
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
        session.add(
            Activity(
                deal_id=deal_id,
                type="stage_change",
                source="system",
                content=f"Stage moved from {previous_stage} to {updated.stage}",
            )
        )
        await record_deal_stage_milestone(
            session,
            deal=updated,
            stage=updated.stage,
            reached_at=updated.stage_entered_at or updated.updated_at,
            source="deal_update",
        )
        await record_stage_transition(
            session,
            deal_id=deal_id,
            from_stage=previous_stage,
            to_stage=updated.stage,
            changed_by_id=_user.id,
            source="deal_update",
            changed_at=updated.stage_entered_at,
        )

    if changes:
        activity = Activity(
            deal_id=deal_id,
            type="field_change",
            source="system",
            content="; ".join(changes),
        )
        session.add(activity)

    if stage_changed or changes or company_changed:
        await session.commit()

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

@router.patch("/{deal_id}/stage", response_model=DealRead)
async def move_stage(deal_id: UUID, body: dict, session: DBSession, _user: CurrentUser):
    new_stage = body.get("stage")
    if not new_stage:
        raise ValidationError("stage is required")

    repo = DealRepository(session)
    deal = await repo.get_or_raise(deal_id)

    valid = await _valid_stages(session, deal.pipeline_type)
    if new_stage not in valid:
        raise ValidationError(f"Invalid stage for {deal.pipeline_type}. Must be one of: {sorted(valid)}")

    old_stage = deal.stage
    if new_stage == old_stage:
        return await repo.get_with_joins(deal_id)

    transition_at = datetime.utcnow()
    await repo.update(deal, {
        "stage": new_stage,
        "stage_entered_at": transition_at,
        "days_in_stage": 0,
        "updated_at": transition_at,
    })

    # Auto-log stage change
    activity = Activity(
        deal_id=deal_id,
        type="stage_change",
        source="system",
        content=f"Stage moved from {old_stage} to {new_stage}",
    )
    session.add(activity)
    await record_deal_stage_milestone(
        session,
        deal=deal,
        stage=new_stage,
        reached_at=deal.stage_entered_at or deal.updated_at,
        source="stage_move",
    )
    await record_stage_transition(
        session,
        deal_id=deal_id,
        from_stage=old_stage,
        to_stage=new_stage,
        changed_by_id=_user.id,
        source="stage_move",
        changed_at=transition_at,
    )
    await session.commit()

    return await repo.get_with_joins(deal_id)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{deal_id}", status_code=204)
async def delete_deal(deal_id: UUID, session: DBSession, _admin: AdminUser):
    repo = DealRepository(session)
    await repo.get_or_raise(deal_id)
    await repo.delete_with_cascade(deal_id)


# ── Deal Contacts ────────────────────────────────────────────────────────────

@router.get("/{deal_id}/contacts", response_model=list[DealContactRead])
async def list_deal_contacts(deal_id: UUID, session: DBSession):
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
    contact = await session.get(Contact, body.contact_id)
    if not contact:
        raise NotFoundError(f"Contact {body.contact_id} not found")

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
async def list_deal_activities(deal_id: UUID, session: DBSession):
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
