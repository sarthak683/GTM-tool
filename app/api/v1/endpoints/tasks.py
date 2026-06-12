import logging
import re
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query, Response
from sqlalchemy import and_, case, delete, func, or_, select

from app.config import settings
from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.task import (
    TASK_ASSIGNED_ROLES,
    TASK_ENTITY_TYPES,
    TASK_PRIORITIES,
    TASK_STATUSES,
    Task,
    TaskComment,
    TaskCommentCreate,
    TaskCommentRead,
    TaskCreate,
    TaskRead,
    TaskUpdate,
    TaskWorkspaceRead,
)
from app.models.user import User
from app.services.tasks import (
    backfill_open_task_assignments,
    complete_system_task,
    compute_deal_task_input_hash,
    mark_deal_task_refresh_requested,
    refresh_system_tasks_for_entity,
    should_queue_deal_task_refresh,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


def _validate_entity_type(entity_type: str) -> None:
    if entity_type not in TASK_ENTITY_TYPES:
        raise ValidationError(f"entity_type must be one of: {sorted(TASK_ENTITY_TYPES)}")


def _validate_priority(priority: str) -> None:
    if priority not in TASK_PRIORITIES:
        raise ValidationError(f"priority must be one of: {sorted(TASK_PRIORITIES)}")


def _validate_status(status: str) -> None:
    if status not in TASK_STATUSES:
        raise ValidationError(f"status must be one of: {sorted(TASK_STATUSES)}")


def _validate_assigned_role(role: str) -> None:
    if role not in TASK_ASSIGNED_ROLES:
        raise ValidationError(f"assigned_role must be one of: {sorted(TASK_ASSIGNED_ROLES)}")


def _normalize_task_title(title: str | None) -> str:
    cleaned = (title or "").strip()
    if len(cleaned) < 3:
        raise ValidationError("Task title must be at least 3 characters.")
    if re.fullmatch(r"[\d\s.,_-]+", cleaned):
        raise ValidationError("Task title must describe the work, not just a number.")
    return cleaned


def _display_domain(value: str | None) -> str | None:
    domain = (value or "").strip()
    if not domain:
        return None
    if domain.lower().endswith(".unknown") or domain.isdigit():
        return "Domain not found"
    return domain


def _can_delete_task(task: Task, current_user: User) -> bool:
    if current_user.role == "admin":
        return True
    return bool(task.created_by_id and task.created_by_id == current_user.id)


def _can_manage_task(task: Task, current_user: User) -> bool:
    if current_user.role == "admin":
        return True
    if task.assigned_to_id and task.assigned_to_id == current_user.id:
        return True
    return bool(task.task_type == "manual" and task.created_by_id and task.created_by_id == current_user.id)


async def _build_task_reads(session: DBSession, tasks: list[Task]) -> list[TaskRead]:
    if not tasks:
        return []

    task_ids = [task.id for task in tasks if task.id]
    user_ids = {task.created_by_id for task in tasks if task.created_by_id} | {task.assigned_to_id for task in tasks if task.assigned_to_id}

    comments_rows = (
        await session.execute(
            select(TaskComment, User.name.label("user_name"))
            .outerjoin(User, TaskComment.created_by_id == User.id)
            .where(TaskComment.task_id.in_(task_ids))
            .order_by(TaskComment.created_at.asc())
        )
    ).all()
    comments_by_task: dict[UUID, list[TaskCommentRead]] = defaultdict(list)
    for comment, user_name in comments_rows:
        read = TaskCommentRead.model_validate(comment)
        read.created_by_name = user_name
        comments_by_task[comment.task_id].append(read)
        if comment.created_by_id:
            user_ids.add(comment.created_by_id)

    users = {}
    if user_ids:
        user_rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(list(user_ids))))
        ).all()
        users = {user_id: name for user_id, name in user_rows}

    reads: list[TaskRead] = []
    for task in tasks:
        read = TaskRead.model_validate(task)
        read.created_by_name = users.get(task.created_by_id)
        read.assigned_to_name = users.get(task.assigned_to_id)
        read.comments = comments_by_task.get(task.id or UUID(int=0), [])
        reads.append(read)
    return reads


async def _build_workspace_task_reads(session: DBSession, tasks: list[Task]) -> list[TaskWorkspaceRead]:
    base_reads = await _build_task_reads(session, tasks)
    if not base_reads:
        return []

    ids_by_type: dict[str, list[UUID]] = defaultdict(list)
    for task in base_reads:
        ids_by_type[task.entity_type].append(task.entity_id)

    company_map: dict[UUID, Company] = {}
    contact_map: dict[UUID, Contact] = {}
    deal_map: dict[UUID, Deal] = {}

    if ids_by_type["company"]:
        rows = (
            await session.execute(select(Company).where(Company.id.in_(ids_by_type["company"])))
        ).scalars().all()
        company_map = {row.id: row for row in rows if row.id}
    if ids_by_type["contact"]:
        rows = (
            await session.execute(select(Contact).where(Contact.id.in_(ids_by_type["contact"])))
        ).scalars().all()
        contact_map = {row.id: row for row in rows if row.id}
    if ids_by_type["deal"]:
        rows = (
            await session.execute(select(Deal).where(Deal.id.in_(ids_by_type["deal"])))
        ).scalars().all()
        deal_map = {row.id: row for row in rows if row.id}

    reads: list[TaskWorkspaceRead] = []
    for task in base_reads:
        entity_name = ""
        entity_subtitle = None
        entity_link = "/"

        if task.entity_type == "company":
            company = company_map.get(task.entity_id)
            if not company:
                continue
            entity_name = company.name
            entity_subtitle = _display_domain(company.domain)
            entity_link = f"/account-sourcing/{task.entity_id}"
        elif task.entity_type == "contact":
            contact = contact_map.get(task.entity_id)
            if not contact:
                continue
            entity_name = f"{contact.first_name} {contact.last_name}".strip() or contact.email or contact.company_name or "Unnamed prospect"
            entity_subtitle = contact.title or contact.email or contact.company_name
            entity_link = f"/account-sourcing/contacts/{task.entity_id}"
        elif task.entity_type == "deal":
            deal = deal_map.get(task.entity_id)
            if not deal:
                continue
            entity_name = deal.name
            entity_subtitle = deal.stage.replace("_", " ")
            entity_link = f"/pipeline?deal={task.entity_id}"

        reads.append(
            TaskWorkspaceRead(
                **task.model_dump(),
                entity_name=entity_name,
                entity_subtitle=entity_subtitle,
                entity_link=entity_link,
            )
        )
    return reads


def _task_list_stmt(entity_type: str, entity_id: UUID, include_closed: bool):
    status_rank = case(
        (Task.status == "open", 0),
        (Task.status == "completed", 1),
        else_=2,
    )
    priority_rank = case(
        (Task.priority == "high", 0),
        (Task.priority == "medium", 1),
        else_=2,
    )

    stmt = (
        select(Task)
        .where(Task.entity_type == entity_type, Task.entity_id == entity_id)
        .order_by(status_rank, priority_rank, Task.created_at.desc())
    )
    if not include_closed:
        stmt = stmt.where(Task.status == "open")
    return stmt


async def _list_entity_tasks(session: DBSession, entity_type: str, entity_id: UUID, include_closed: bool) -> list[Task]:
    return (await session.execute(_task_list_stmt(entity_type, entity_id, include_closed))).scalars().all()


async def _refresh_entity_tasks_background(entity_type: str, entity_id: UUID) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await refresh_system_tasks_for_entity(session, entity_type, entity_id)
            await backfill_open_task_assignments(session, entity_type=entity_type, entity_id=entity_id)
            await session.commit()
        except Exception as exc:  # pragma: no cover - background safety net
            logger.warning("background task refresh failed for %s %s: %s", entity_type, entity_id, exc)
            await session.rollback()


@router.get("/", response_model=list[TaskRead])
async def list_tasks(
    session: DBSession,
    current_user: CurrentUser,
    response: Response,
    background_tasks: BackgroundTasks,
    entity_type: str = Query(...),
    entity_id: UUID = Query(...),
    include_closed: bool = Query(default=True),
    refresh_mode: str = Query(default="auto"),
):
    _ = current_user
    _validate_entity_type(entity_type)
    if refresh_mode not in {"auto", "force", "none"}:
        raise ValidationError("refresh_mode must be one of: ['auto', 'force', 'none']")

    # Manual-tasks-only mode: never regenerate or queue system tasks on read.
    # Collapsing to "none" makes every refresh/queue branch below inert, so the
    # endpoint just returns the rows that exist (which, after migration 085, are
    # human-created tasks only).
    if not settings.ENABLE_SYSTEM_TASKS:
        refresh_mode = "none"

    refresh_result = "skipped"
    tasks: list[Task]

    if refresh_mode == "force":
        await refresh_system_tasks_for_entity(session, entity_type, entity_id)
        await backfill_open_task_assignments(session)
        await session.commit()
        tasks = await _list_entity_tasks(session, entity_type, entity_id, include_closed)
        refresh_result = "sync"
    elif entity_type != "deal":
        if refresh_mode == "auto":
            await refresh_system_tasks_for_entity(session, entity_type, entity_id)
            refresh_result = "sync"
        await backfill_open_task_assignments(session)
        await session.commit()
        tasks = await _list_entity_tasks(session, entity_type, entity_id, include_closed)
    else:
        deal = await session.get(Deal, entity_id)
        current_tasks = await _list_entity_tasks(session, entity_type, entity_id, include_closed)
        if deal and refresh_mode == "auto" and deal.ai_tasks_refreshed_at is None:
            await refresh_system_tasks_for_entity(session, entity_type, entity_id)
            await backfill_open_task_assignments(session)
            await session.commit()
            tasks = await _list_entity_tasks(session, entity_type, entity_id, include_closed)
            refresh_result = "sync"
        else:
            if deal and refresh_mode == "auto":
                input_hash = await compute_deal_task_input_hash(session, deal)
                if should_queue_deal_task_refresh(deal, input_hash=input_hash):
                    mark_deal_task_refresh_requested(deal)
                    session.add(deal)
                    background_tasks.add_task(_refresh_entity_tasks_background, entity_type, entity_id)
                    refresh_result = "queued"
            await backfill_open_task_assignments(session)
            await session.commit()
            tasks = current_tasks if refresh_result != "queued" else await _list_entity_tasks(session, entity_type, entity_id, include_closed)

    response.headers["X-Beacon-Refresh-Mode"] = refresh_result
    return await _build_task_reads(session, tasks)


@router.get("/count")
async def get_task_count(session: DBSession, current_user: CurrentUser):
    """Return the number of open tasks assigned to the current user."""
    await backfill_open_task_assignments(session)
    await session.commit()
    count = (
        await session.execute(
            select(func.count(Task.id)).where(
                Task.assigned_to_id == current_user.id,
                Task.status == "open",
            )
        )
    ).scalar_one()
    return {"open": count}


@router.get("/workspace", response_model=list[TaskWorkspaceRead])
async def list_workspace_tasks(
    session: DBSession,
    current_user: CurrentUser,
    include_closed: bool = Query(default=False),
    task_type: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    deal_id: UUID | None = Query(default=None),
    scope: str = Query(default="mine"),
):
    if task_type and task_type not in {"manual", "system"}:
        raise ValidationError("task_type must be one of: ['manual', 'system']")
    if entity_type:
        _validate_entity_type(entity_type)
    if scope not in {"mine", "team"}:
        raise ValidationError("scope must be one of: ['mine', 'team']")
    if scope == "team" and current_user.role != "admin":
        raise ForbiddenError("Only admins can access the team queue")

    status_rank = case(
        (Task.status == "open", 0),
        (Task.status == "completed", 1),
        else_=2,
    )
    priority_rank = case(
        (Task.priority == "high", 0),
        (Task.priority == "medium", 1),
        else_=2,
    )

    stmt = select(Task).order_by(status_rank, priority_rank, Task.updated_at.desc())
    await backfill_open_task_assignments(session)
    await session.commit()
    if scope == "mine" or current_user.role != "admin":
        stmt = stmt.where(Task.assigned_to_id == current_user.id)
    if not include_closed:
        stmt = stmt.where(Task.status == "open")
    if task_type:
        stmt = stmt.where(Task.task_type == task_type)
    if entity_type:
        stmt = stmt.where(Task.entity_type == entity_type)
    if deal_id:
        stmt = stmt.where(
            or_(
                and_(Task.entity_type == "deal", Task.entity_id == deal_id),
                Task.action_payload["deal_id"].astext == str(deal_id),
            )
        )

    tasks = (await session.execute(stmt)).scalars().all()
    return await _build_workspace_task_reads(session, tasks)


@router.post("/", response_model=TaskRead, status_code=201)
async def create_task(payload: TaskCreate, session: DBSession, current_user: CurrentUser):
    _validate_entity_type(payload.entity_type)
    _validate_priority(payload.priority)
    if payload.assigned_role:
        _validate_assigned_role(payload.assigned_role)
    title = _normalize_task_title(payload.title)

    task = Task(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        task_type="manual",
        title=title,
        description=payload.description.strip() if payload.description else None,
        priority=payload.priority,
        due_at=payload.due_at,
        source="manual",
        created_by_id=current_user.id,
        assigned_role=None,
        assigned_to_id=payload.assigned_to_id or current_user.id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    reads = await _build_task_reads(session, [task])
    return reads[0]


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(task_id: UUID, payload: TaskUpdate, session: DBSession, current_user: CurrentUser):
    _ = current_user
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    if not _can_manage_task(task, current_user):
        raise ForbiddenError("Only the assigned user or an admin can update this task")

    update_data = payload.model_dump(exclude_unset=True)
    if "priority" in update_data:
        _validate_priority(update_data["priority"])
    if "status" in update_data:
        _validate_status(update_data["status"])
        if update_data["status"] in {"completed", "dismissed"} and not task.completed_at:
            task.completed_at = datetime.utcnow()
        elif update_data["status"] == "open":
            # Reopening must clear the stale completion timestamp
            task.completed_at = None
    if "assigned_role" in update_data and update_data["assigned_role"] is not None:
        _validate_assigned_role(update_data["assigned_role"])
    if "title" in update_data:
        update_data["title"] = _normalize_task_title(update_data["title"])
    for key, value in update_data.items():
        setattr(task, key, value)
    task.updated_at = datetime.utcnow()
    session.add(task)
    await session.commit()
    await session.refresh(task)
    reads = await _build_task_reads(session, [task])
    return reads[0]


@router.post("/{task_id}/comments", response_model=TaskCommentRead, status_code=201)
async def add_task_comment(task_id: UUID, payload: TaskCommentCreate, session: DBSession, current_user: CurrentUser):
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    if not _can_manage_task(task, current_user):
        raise ForbiddenError("Only the assigned user or an admin can update this task")

    comment = TaskComment(
        task_id=task_id,
        body=payload.body.strip(),
        created_by_id=current_user.id,
    )
    task.updated_at = datetime.utcnow()
    session.add(comment)
    session.add(task)
    await session.commit()
    await session.refresh(comment)
    read = TaskCommentRead.model_validate(comment)
    read.created_by_name = current_user.name
    return read


@router.post("/{task_id}/accept", response_model=TaskRead)
async def accept_task(task_id: UUID, session: DBSession, current_user: CurrentUser):
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    if task.task_type != "system":
        raise ValidationError("Only system tasks can be accepted")
    if not _can_manage_task(task, current_user):
        raise ForbiddenError("Only the assigned user or an admin can act on this task")

    await complete_system_task(session, task, current_user)
    await session.commit()
    await session.refresh(task)
    reads = await _build_task_reads(session, [task])
    return reads[0]


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: UUID, session: DBSession, current_user: CurrentUser):
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    if not _can_delete_task(task, current_user):
        raise ForbiddenError("Only admins or the user who created this task can delete it")

    # Delete dependent comments first to satisfy FK constraints reliably.
    await session.execute(delete(TaskComment).where(TaskComment.task_id == task_id))
    await session.flush()
    await session.delete(task)
    await session.commit()


@router.post("/admin/dismiss-inactive-deal-tasks", response_model=dict)
async def dismiss_inactive_deal_tasks(session: DBSession, current_user: CurrentUser):
    """
    Admin-only. Dismisses all open tasks linked to deals that are in a
    closed or inactive stage (closed_lost, cold, on_hold, nurture, churned,
    not_a_fit, closed_won, closed). Fixes tasks incorrectly created during
    CRM imports for parked/closed accounts.
    """
    if current_user.role != "admin":
        raise ForbiddenError("Only admins can run this operation")

    inactive_stages = {
        "closed_won", "closed_lost", "not_a_fit", "cold",
        "on_hold", "nurture", "churned", "closed",
    }

    result = await session.execute(
        select(Task.id)
        .join(Deal, and_(Task.entity_type == "deal", Task.entity_id == Deal.id))
        .where(Task.status == "open", Deal.stage.in_(inactive_stages))
    )
    task_ids = [row[0] for row in result.all()]

    if not task_ids:
        return {"dismissed": 0, "message": "No open tasks found on inactive deals."}

    now = datetime.utcnow()
    await session.execute(
        Task.__table__.update()
        .where(Task.id.in_(task_ids))
        .values(status="dismissed", completed_at=now, updated_at=now)
    )
    await session.commit()

    return {
        "dismissed": len(task_ids),
        "message": f"Dismissed {len(task_ids)} open tasks linked to inactive/closed deals.",
    }
