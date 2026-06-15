"""
Sales Knowledge Base endpoints.

Upload:
  POST /resources/upload          — file upload (PDF, DOCX, TXT) with auto text extraction
  POST /resources                 — create from pasted text

CRUD:
  GET    /resources               — list (filter by category, module, search query)
  GET    /resources/{id}          — single resource
  PUT    /resources/{id}          — update metadata or content
  DELETE /resources/{id}          — delete

AI context:
  GET    /resources/for-module/{module}  — resources relevant to a specific AI module
"""
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel

from app.core.dependencies import AdminUser, CurrentUser, DBSession, Pagination
from app.models.sales_resource import (
    SalesResource,
    SalesResourceCreate,
    SalesResourceRead,
    SalesResourceUpdate,
)
from app.repositories.sales_resource import SalesResourceRepository
from app.schemas.common import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["knowledge-base"])

VALID_CATEGORIES = [
    "roi_template", "case_study", "competitive_intel", "product_info",
    "pricing", "objection_handling", "email_template", "playbook", "other",
]

VALID_MODULES = [
    "pre_meeting", "outreach", "demo_strategy",
    "account_sourcing", "custom_demo", "prospecting",
]


# ── Upload (file) ────────────────────────────────────────────────────────────

@router.post("/upload", response_model=SalesResourceRead, status_code=201)
async def upload_resource(
    session: DBSession,
    _user: CurrentUser,
    file: UploadFile = File(...),
    title: str = Form(...),
    category: str = Form(...),
    description: Optional[str] = Form(default=None),
    tags: str = Form(default="[]"),        # JSON-encoded list
    modules: str = Form(default="[]"),     # JSON-encoded list
):
    """Upload a PDF, DOCX, or TXT file. Text is auto-extracted and stored."""
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {VALID_CATEGORIES}")

    try:
        tags_list = json.loads(tags) if tags else []
        modules_list = json.loads(modules) if modules else []
    except json.JSONDecodeError:
        raise HTTPException(400, "tags and modules must be JSON arrays")

    for m in modules_list:
        if m not in VALID_MODULES:
            raise HTTPException(400, f"Invalid module '{m}'. Must be one of: {VALID_MODULES}")

    file_bytes = await file.read()
    filename = file.filename or "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Extract text
    try:
        if ext == "pdf":
            from app.services.demo_generator import extract_pdf_text
            content = extract_pdf_text(file_bytes)
        elif ext in ("docx", "doc"):
            from app.services.demo_generator import extract_docx_text
            content = extract_docx_text(file_bytes)
        elif ext in ("txt", "md", "csv"):
            content = file_bytes.decode("utf-8", errors="replace")
        else:
            raise HTTPException(
                400,
                f"Unsupported file type '.{ext}'. Supported: PDF, DOCX, TXT, MD, CSV",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Text extraction failed for {filename}")
        raise HTTPException(500, f"Failed to extract text: {str(e)}")

    if not content.strip():
        raise HTTPException(400, "File appears to be empty — no text could be extracted.")

    repo = SalesResourceRepository(session)
    resource = await repo.create({
        "title": title,
        "category": category,
        "description": description,
        "content": content,
        "filename": filename,
        "file_size": len(file_bytes),
        "tags": tags_list,
        "modules": modules_list,
    })
    return resource


# ── Create from text ─────────────────────────────────────────────────────────

@router.post("/", response_model=SalesResourceRead, status_code=201)
async def create_resource(payload: SalesResourceCreate, session: DBSession, _user: CurrentUser):
    """Create a resource from pasted text content."""
    if payload.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {VALID_CATEGORIES}")
    for m in payload.modules:
        if m not in VALID_MODULES:
            raise HTTPException(400, f"Invalid module '{m}'. Must be one of: {VALID_MODULES}")

    repo = SalesResourceRepository(session)
    return await repo.create(payload.model_dump())


# ── List / search ────────────────────────────────────────────────────────────

@router.get("/", response_model=PaginatedResponse[SalesResourceRead])
async def list_resources(
    session: DBSession,
    _user: CurrentUser,
    pagination: Pagination,
    category: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
):
    """List resources with optional filters."""
    repo = SalesResourceRepository(session)
    all_results = await repo.search(
        category=category, module=module, query=q, active_only=active_only,
    )
    total = len(all_results)
    items = all_results[pagination.skip : pagination.skip + pagination.limit]
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


# ── For module (AI context) ──────────────────────────────────────────────────

@router.get("/for-module/{module}", response_model=list[SalesResourceRead])
async def resources_for_module(
    module: str,
    session: DBSession,
    _user: CurrentUser,
    limit: int = Query(default=5, ge=1, le=20),
):
    """Get resources targeted at a specific AI module (for context injection)."""
    if module not in VALID_MODULES:
        raise HTTPException(400, f"Invalid module. Must be one of: {VALID_MODULES}")
    repo = SalesResourceRepository(session)
    return await repo.for_module(module, limit=limit)


# ── Meta: available categories & modules ─────────────────────────────────────

@router.get("/meta/options")
async def get_resource_options(_user: CurrentUser):
    """Return valid categories and modules for the frontend."""
    return {
        "categories": VALID_CATEGORIES,
        "modules": VALID_MODULES,
    }


# ── Single resource ──────────────────────────────────────────────────────────

@router.get("/{resource_id}", response_model=SalesResourceRead)
async def get_resource(resource_id: UUID, session: DBSession, _user: CurrentUser):
    repo = SalesResourceRepository(session)
    return await repo.get_or_raise(resource_id)


# ── Update ───────────────────────────────────────────────────────────────────

@router.put("/{resource_id}", response_model=SalesResourceRead)
async def update_resource(
    resource_id: UUID, payload: SalesResourceUpdate, session: DBSession,
    _user: CurrentUser,
):
    repo = SalesResourceRepository(session)
    resource = await repo.get_or_raise(resource_id)
    update_data = payload.model_dump(exclude_unset=True)
    if "category" in update_data and update_data["category"] not in VALID_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Must be one of: {VALID_CATEGORIES}")
    if "modules" in update_data:
        for m in update_data["modules"]:
            if m not in VALID_MODULES:
                raise HTTPException(400, f"Invalid module '{m}'. Must be one of: {VALID_MODULES}")
    update_data["updated_at"] = datetime.utcnow()
    return await repo.update(resource, update_data)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{resource_id}", status_code=204)
async def delete_resource(resource_id: UUID, session: DBSession, _admin: AdminUser):
    repo = SalesResourceRepository(session)
    resource = await repo.get_or_raise(resource_id)
    await repo.delete(resource)
