from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.dependencies import CurrentUser, DBSession
from app.repositories.company import CompanyRepository

router = APIRouter(prefix="/enrichment", tags=["enrichment"])


@router.post("/company/{company_id}")
async def trigger_company_enrichment(
    company_id: UUID, session: DBSession, current_user: CurrentUser
) -> dict:
    """Queue a Celery enrichment task for the given company."""
    company = await CompanyRepository(session).get_or_raise(company_id)
    try:
        from app.tasks.enrichment import enrich_company_task

        task = enrich_company_task.delay(str(company_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to queue company enrichment") from exc

    return {
        "status": "queued",
        "task_id": task.id,
        "company_id": str(company_id),
        "domain": company.domain,
        "message": f"Enrichment queued for {company.domain}",
    }


@router.get("/task/{task_id}")
async def get_task_status(task_id: str, current_user: CurrentUser) -> dict:
    """Poll Celery task status."""
    from app.celery_app import celery_app

    result = celery_app.AsyncResult(task_id)

    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }
