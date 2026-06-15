import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlmodel import SQLModel

from app.core.dependencies import CurrentUser, DBSession
from app.services.permissions import require_workspace_permission
from app.tasks.crm_import import run_clickup_import

router = APIRouter(prefix="/crm-imports", tags=["crm-imports"])
logger = logging.getLogger(__name__)


class ClickUpCrmImportRequest(SQLModel):
    replace_existing: bool = True
    limit: Optional[int] = None
    cache_dir: Optional[str] = "tmp/clickup_import_cache"
    skip_comments: bool = False
    skip_subtasks: bool = False


@router.post("/clickup-sales-crm", response_model=dict)
async def import_clickup_sales_crm(
    body: ClickUpCrmImportRequest,
    session: DBSession,
    current_user: CurrentUser,
):
    """
    Kicks off the ClickUp CRM import as a background Celery task.
    Returns immediately with a task_id — poll /status/{task_id} for progress.
    """
    await require_workspace_permission(session, current_user, "crm_import")

    task = run_clickup_import.delay(
        replace_existing=body.replace_existing,
        limit=body.limit or 0,
        cache_dir=body.cache_dir,
        skip_comments=body.skip_comments,
        skip_subtasks=body.skip_subtasks,
    )

    logger.info(
        "clickup crm import queued by %s (task_id=%s, replace_existing=%s)",
        current_user.email,
        task.id,
        body.replace_existing,
    )

    return {
        "status": "queued",
        "task_id": task.id,
        "message": "Import is running in the background. Poll /api/v1/crm-imports/status/{task_id} to check progress.",
    }


@router.get("/status/{task_id}", response_model=dict)
async def get_import_status(task_id: str, current_user: CurrentUser):
    """
    Poll this endpoint to check the status of a background CRM import.
    Returns: pending | running | success (with result) | failure (with error)
    """
    from celery.result import AsyncResult
    result = AsyncResult(task_id)

    if result.state == "PENDING":
        return {"task_id": task_id, "status": "pending", "message": "Import is queued, not started yet."}
    if result.state == "STARTED":
        return {"task_id": task_id, "status": "running", "message": "Import is in progress..."}
    if result.state == "SUCCESS":
        return {"task_id": task_id, "status": "success", "result": result.result}
    if result.state == "FAILURE":
        return {"task_id": task_id, "status": "failure", "error": str(result.result)}

    return {"task_id": task_id, "status": result.state}
