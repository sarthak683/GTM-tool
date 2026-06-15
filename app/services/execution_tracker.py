from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.assignment_update import (
    AssignmentUpdate,
    AssignmentUpdateCreate,
    AssignmentUpdateRead,
    ExecutionTrackerItemRead,
    ExecutionTrackerSummary,
    TRACKER_STALE_DAYS,
)
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User


POSITIVE_PROGRESS_STATES = frozenset({"meeting_booked", "qualified", "deal_created"})
POSITIVE_BUYER_SIGNALS = frozenset({
    "interested",
    "champion_identified",
    "meeting_requested",
    "commercial_discussion",
    "verbal_yes",
})


@dataclass(frozen=True)
class AssignmentContext:
    entity_type: str
    entity_id: UUID
    entity_name: str
    entity_subtitle: Optional[str]
    entity_link: str
    company_name: Optional[str]
    assignee_id: UUID
    assignee_name: Optional[str]
    assignee_email: Optional[str]
    assignment_role: str
    system_status: Optional[str]
    entity_updated_at: datetime

    @property
    def key(self) -> tuple[str, UUID, str, UUID]:
        return (self.entity_type, self.entity_id, self.assignment_role, self.assignee_id)


def _join_parts(*parts: Optional[str]) -> Optional[str]:
    values = [str(part).strip() for part in parts if part and str(part).strip()]
    return " - ".join(values) if values else None


def _build_update_read(update: AssignmentUpdate, created_by_name: Optional[str]) -> AssignmentUpdateRead:
    read = AssignmentUpdateRead.model_validate(update)
    read.created_by_name = created_by_name
    return read


def _is_stale(update: Optional[AssignmentUpdateRead]) -> bool:
    if update is None:
        return True
    return (date.today() - update.created_at.date()).days >= TRACKER_STALE_DAYS


def _is_overdue(update: Optional[AssignmentUpdateRead]) -> bool:
    if update is None or update.next_step_due_date is None:
        return False
    if update.progress_state in {"closed", "deal_created"}:
        return False
    return update.next_step_due_date < date.today()


def _has_positive_momentum(update: Optional[AssignmentUpdateRead]) -> bool:
    if update is None:
        return False
    if update.progress_state in POSITIVE_PROGRESS_STATES:
        return True
    if update.buyer_signal in POSITIVE_BUYER_SIGNALS:
        return True
    return update.confidence == "high" and update.progress_state not in {"blocked", "closed"}


async def _load_company_contexts(
    session: AsyncSession,
    current_user: User,
    assignee_id: Optional[UUID] = None,
) -> list[AssignmentContext]:
    stmt = (
        select(
            Company,
            User.name.label("assignee_name"),
            User.email.label("assignee_email"),
        )
        .join(User, Company.assigned_to_id == User.id)
    )
    if assignee_id:
        stmt = stmt.where(Company.assigned_to_id == assignee_id)
    elif current_user.role != "admin":
        stmt = stmt.where(Company.assigned_to_id == current_user.id)

    rows = (await session.execute(stmt)).all()
    return [
        AssignmentContext(
            entity_type="company",
            entity_id=company.id,
            entity_name=company.name,
            entity_subtitle=_join_parts(company.domain, company.industry),
            entity_link=f"/account-sourcing/{company.id}",
            company_name=company.name,
            assignee_id=company.assigned_to_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role="owner",
            system_status=company.disposition or company.outreach_status or company.recommended_outreach_lane or company.icp_tier,
            entity_updated_at=company.updated_at,
        )
        for company, assignee_name, assignee_email in rows
        if company.id and company.assigned_to_id
    ]


async def _load_contact_contexts(
    session: AsyncSession,
    current_user: User,
    assignee_id: Optional[UUID] = None,
) -> list[AssignmentContext]:
    contexts: list[AssignmentContext] = []

    ae_stmt = (
        select(
            Contact,
            Company.name.label("company_name"),
            User.name.label("assignee_name"),
            User.email.label("assignee_email"),
        )
        .outerjoin(Company, Contact.company_id == Company.id)
        .join(User, Contact.assigned_to_id == User.id)
    )
    if assignee_id:
        ae_stmt = ae_stmt.where(Contact.assigned_to_id == assignee_id)
    elif current_user.role != "admin":
        ae_stmt = ae_stmt.where(Contact.assigned_to_id == current_user.id)

    for contact, company_name, assignee_name, assignee_email in (await session.execute(ae_stmt)).all():
        if not contact.id or not contact.assigned_to_id:
            continue
        contexts.append(AssignmentContext(
            entity_type="contact",
            entity_id=contact.id,
            entity_name=f"{contact.first_name} {contact.last_name}".strip(),
            entity_subtitle=_join_parts(contact.title, company_name),
            entity_link=f"/account-sourcing/contacts/{contact.id}",
            company_name=company_name,
            assignee_id=contact.assigned_to_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role="ae",
            system_status=contact.sequence_status or contact.outreach_lane or contact.persona_type or contact.persona,
            entity_updated_at=contact.updated_at,
        ))

    sdr_stmt = (
        select(
            Contact,
            Company.name.label("company_name"),
            User.name.label("assignee_name"),
            User.email.label("assignee_email"),
        )
        .outerjoin(Company, Contact.company_id == Company.id)
        .join(User, Contact.sdr_id == User.id)
    )
    if assignee_id:
        sdr_stmt = sdr_stmt.where(Contact.sdr_id == assignee_id)
    elif current_user.role != "admin":
        sdr_stmt = sdr_stmt.where(Contact.sdr_id == current_user.id)

    for contact, company_name, assignee_name, assignee_email in (await session.execute(sdr_stmt)).all():
        if not contact.id or not contact.sdr_id:
            continue
        contexts.append(AssignmentContext(
            entity_type="contact",
            entity_id=contact.id,
            entity_name=f"{contact.first_name} {contact.last_name}".strip(),
            entity_subtitle=_join_parts(contact.title, company_name),
            entity_link=f"/account-sourcing/contacts/{contact.id}",
            company_name=company_name,
            assignee_id=contact.sdr_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role="sdr",
            system_status=contact.sequence_status or contact.outreach_lane or contact.persona_type or contact.persona,
            entity_updated_at=contact.updated_at,
        ))

    return contexts


async def _load_deal_contexts(
    session: AsyncSession,
    current_user: User,
    assignee_id: Optional[UUID] = None,
) -> list[AssignmentContext]:
    stmt = (
        select(
            Deal,
            Company.name.label("company_name"),
            User.name.label("assignee_name"),
            User.email.label("assignee_email"),
        )
        .outerjoin(Company, Deal.company_id == Company.id)
        .join(User, Deal.assigned_to_id == User.id)
    )
    if assignee_id:
        stmt = stmt.where(Deal.assigned_to_id == assignee_id)
    elif current_user.role != "admin":
        stmt = stmt.where(Deal.assigned_to_id == current_user.id)

    rows = (await session.execute(stmt)).all()
    return [
        AssignmentContext(
            entity_type="deal",
            entity_id=deal.id,
            entity_name=deal.name,
            entity_subtitle=_join_parts(company_name, deal.pipeline_type),
            entity_link=f"/pipeline?deal={deal.id}",
            company_name=company_name,
            assignee_id=deal.assigned_to_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role="owner",
            system_status=deal.stage,
            entity_updated_at=deal.updated_at,
        )
        for deal, company_name, assignee_name, assignee_email in rows
        if deal.id and deal.assigned_to_id
    ]


async def load_assignment_contexts(
    session: AsyncSession,
    current_user: User,
    *,
    assignee_id: Optional[UUID] = None,
    entity_type: Optional[str] = None,
) -> list[AssignmentContext]:
    effective_assignee_id = assignee_id if current_user.role == "admin" else current_user.id
    contexts: list[AssignmentContext] = []
    if entity_type in (None, "company"):
        contexts.extend(await _load_company_contexts(session, current_user, effective_assignee_id))
    if entity_type in (None, "contact"):
        contexts.extend(await _load_contact_contexts(session, current_user, effective_assignee_id))
    if entity_type in (None, "deal"):
        contexts.extend(await _load_deal_contexts(session, current_user, effective_assignee_id))
    return contexts


async def load_latest_updates(
    session: AsyncSession,
    contexts: list[AssignmentContext],
) -> dict[tuple[str, UUID, str, UUID], AssignmentUpdateRead]:
    if not contexts:
        return {}

    valid_keys = {context.key for context in contexts}
    entity_types = sorted({context.entity_type for context in contexts})
    entity_ids = sorted({context.entity_id for context in contexts}, key=str)
    assignee_ids = sorted({context.assignee_id for context in contexts}, key=str)

    stmt = (
        select(AssignmentUpdate, User.name.label("created_by_name"))
        .outerjoin(User, AssignmentUpdate.created_by_id == User.id)
        .where(
            AssignmentUpdate.entity_type.in_(entity_types),
            AssignmentUpdate.entity_id.in_(entity_ids),
            AssignmentUpdate.assignee_id.in_(assignee_ids),
        )
        .order_by(AssignmentUpdate.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    latest: dict[tuple[str, UUID, str, UUID], AssignmentUpdateRead] = {}
    for update, created_by_name in rows:
        if update.assignee_id is None:
            continue
        key = (update.entity_type, update.entity_id, update.assignment_role, update.assignee_id)
        if key not in valid_keys or key in latest:
            continue
        latest[key] = _build_update_read(update, created_by_name)

    return latest


def _matches_search(context: AssignmentContext, update: Optional[AssignmentUpdateRead], q: Optional[str]) -> bool:
    normalized = (q or "").strip().lower()
    if not normalized:
        return True
    haystack = " ".join(filter(None, [
        context.entity_name,
        context.entity_subtitle or "",
        context.company_name or "",
        context.assignee_name or "",
        context.system_status or "",
        update.summary if update else "",
        update.next_step if update else "",
    ])).lower()
    return normalized in haystack


def build_execution_items(
    contexts: list[AssignmentContext],
    latest_updates: dict[tuple[str, UUID, str, UUID], AssignmentUpdateRead],
    *,
    q: Optional[str] = None,
    progress_state: Optional[str] = None,
    needs_update_only: bool = False,
) -> list[ExecutionTrackerItemRead]:
    items: list[ExecutionTrackerItemRead] = []

    for context in contexts:
        latest = latest_updates.get(context.key)
        needs_update = _is_stale(latest)
        overdue = _is_overdue(latest)

        if progress_state and (latest is None or latest.progress_state != progress_state):
            continue
        if needs_update_only and not needs_update:
            continue
        if not _matches_search(context, latest, q):
            continue

        items.append(ExecutionTrackerItemRead(
            entity_type=context.entity_type,
            entity_id=context.entity_id,
            entity_name=context.entity_name,
            entity_subtitle=context.entity_subtitle,
            entity_link=context.entity_link,
            company_name=context.company_name,
            assignee_id=context.assignee_id,
            assignee_name=context.assignee_name,
            assignment_role=context.assignment_role,
            system_status=context.system_status,
            entity_updated_at=context.entity_updated_at,
            needs_update=needs_update,
            next_step_overdue=overdue,
            latest_update=latest,
        ))

    items.sort(
        key=lambda item: (
            0 if item.next_step_overdue else 1,
            0 if item.needs_update else 1,
            item.latest_update.next_step_due_date if item.latest_update and item.latest_update.next_step_due_date else date.max,
            -(
                item.latest_update.created_at if item.latest_update else item.entity_updated_at
            ).timestamp(),
        )
    )
    return items


def build_execution_summary(items: list[ExecutionTrackerItemRead]) -> ExecutionTrackerSummary:
    no_update_items = sum(1 for item in items if item.latest_update is None)
    blocked_items = sum(
        1
        for item in items
        if item.latest_update and (
            item.latest_update.progress_state == "blocked"
            or item.latest_update.blocker_type not in {"", "none"}
        )
    )
    return ExecutionTrackerSummary(
        total_items=len(items),
        no_update_items=no_update_items,
        needs_update_items=sum(1 for item in items if item.needs_update),
        blocked_items=blocked_items,
        overdue_next_steps=sum(1 for item in items if item.next_step_overdue),
        positive_momentum_items=sum(1 for item in items if _has_positive_momentum(item.latest_update)),
    )


async def get_current_assignment_context(
    session: AsyncSession,
    current_user: User,
    *,
    entity_type: str,
    entity_id: UUID,
    assignment_role: str,
) -> AssignmentContext:
    if entity_type == "company":
        if assignment_role != "owner":
            raise NotFoundError("Company assignments use the owner role")
        stmt = (
            select(
                Company,
                User.name.label("assignee_name"),
                User.email.label("assignee_email"),
            )
            .join(User, Company.assigned_to_id == User.id)
            .where(Company.id == entity_id)
        )
        row = (await session.execute(stmt)).first()
        if not row:
            raise NotFoundError("Assigned company not found")
        company, assignee_name, assignee_email = row
        if current_user.role != "admin" and company.assigned_to_id != current_user.id:
            raise ForbiddenError("You can only view updates for your own assignments")
        return AssignmentContext(
            entity_type="company",
            entity_id=company.id,
            entity_name=company.name,
            entity_subtitle=_join_parts(company.domain, company.industry),
            entity_link=f"/account-sourcing/{company.id}",
            company_name=company.name,
            assignee_id=company.assigned_to_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role="owner",
            system_status=company.disposition or company.outreach_status or company.recommended_outreach_lane or company.icp_tier,
            entity_updated_at=company.updated_at,
        )

    if entity_type == "contact":
        if assignment_role not in {"ae", "sdr"}:
            raise NotFoundError("Contact assignments use the ae or sdr role")
        assignee_column = Contact.assigned_to_id if assignment_role == "ae" else Contact.sdr_id
        stmt = (
            select(
                Contact,
                Company.name.label("company_name"),
                User.name.label("assignee_name"),
                User.email.label("assignee_email"),
            )
            .outerjoin(Company, Contact.company_id == Company.id)
            .join(User, assignee_column == User.id)
            .where(Contact.id == entity_id)
        )
        row = (await session.execute(stmt)).first()
        if not row:
            raise NotFoundError("Assigned contact not found")
        contact, company_name, assignee_name, assignee_email = row
        assignee_id = contact.assigned_to_id if assignment_role == "ae" else contact.sdr_id
        if assignee_id is None:
            raise NotFoundError("Assigned contact not found")
        if current_user.role != "admin" and assignee_id != current_user.id:
            raise ForbiddenError("You can only view updates for your own assignments")
        return AssignmentContext(
            entity_type="contact",
            entity_id=contact.id,
            entity_name=f"{contact.first_name} {contact.last_name}".strip(),
            entity_subtitle=_join_parts(contact.title, company_name),
            entity_link=f"/account-sourcing/contacts/{contact.id}",
            company_name=company_name,
            assignee_id=assignee_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role=assignment_role,
            system_status=contact.sequence_status or contact.outreach_lane or contact.persona_type or contact.persona,
            entity_updated_at=contact.updated_at,
        )

    if entity_type == "deal":
        if assignment_role != "owner":
            raise NotFoundError("Deal assignments use the owner role")
        stmt = (
            select(
                Deal,
                Company.name.label("company_name"),
                User.name.label("assignee_name"),
                User.email.label("assignee_email"),
            )
            .outerjoin(Company, Deal.company_id == Company.id)
            .join(User, Deal.assigned_to_id == User.id)
            .where(Deal.id == entity_id)
        )
        row = (await session.execute(stmt)).first()
        if not row:
            raise NotFoundError("Assigned deal not found")
        deal, company_name, assignee_name, assignee_email = row
        if current_user.role != "admin" and deal.assigned_to_id != current_user.id:
            raise ForbiddenError("You can only view updates for your own assignments")
        return AssignmentContext(
            entity_type="deal",
            entity_id=deal.id,
            entity_name=deal.name,
            entity_subtitle=_join_parts(company_name, deal.pipeline_type),
            entity_link=f"/pipeline?deal={deal.id}",
            company_name=company_name,
            assignee_id=deal.assigned_to_id,
            assignee_name=assignee_name,
            assignee_email=assignee_email,
            assignment_role="owner",
            system_status=deal.stage,
            entity_updated_at=deal.updated_at,
        )

    raise NotFoundError("Assigned item not found")


async def list_item_updates(
    session: AsyncSession,
    current_user: User,
    *,
    entity_type: str,
    entity_id: UUID,
    assignment_role: str,
) -> list[AssignmentUpdateRead]:
    context = await get_current_assignment_context(
        session,
        current_user,
        entity_type=entity_type,
        entity_id=entity_id,
        assignment_role=assignment_role,
    )
    stmt = (
        select(AssignmentUpdate, User.name.label("created_by_name"))
        .outerjoin(User, AssignmentUpdate.created_by_id == User.id)
        .where(
            AssignmentUpdate.entity_type == context.entity_type,
            AssignmentUpdate.entity_id == context.entity_id,
            AssignmentUpdate.assignment_role == context.assignment_role,
            AssignmentUpdate.assignee_id == context.assignee_id,
        )
        .order_by(AssignmentUpdate.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return [_build_update_read(update, created_by_name) for update, created_by_name in rows]


async def create_item_update(
    session: AsyncSession,
    current_user: User,
    payload: AssignmentUpdateCreate,
) -> AssignmentUpdateRead:
    context = await get_current_assignment_context(
        session,
        current_user,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        assignment_role=payload.assignment_role,
    )
    if current_user.role != "admin" and context.assignee_id != current_user.id:
        raise ForbiddenError("You can only update your own assignments")

    update = AssignmentUpdate(
        **payload.model_dump(),
        assignee_id=context.assignee_id,
        created_by_id=current_user.id,
        entity_name_snapshot=context.entity_name,
        company_name_snapshot=context.company_name,
        assignee_name_snapshot=context.assignee_name,
        assignee_email_snapshot=context.assignee_email,
    )
    session.add(update)
    await session.commit()
    await session.refresh(update)
    return _build_update_read(update, current_user.name)
