"""
Custom Demo Assistance endpoints.

Two creation paths:
  POST /custom-demos/generate-from-file  — upload PDF/DOCX production guide
  POST /custom-demos/generate-from-editor — submit scene list from in-app editor
    POST /custom-demos/generate-from-brief  — submit structured company/demo brief

Both paths:
  1. Create a CustomDemo record (status=draft)
  2. Extract/store the source text
  3. Fire background generation task
  4. Return demo_id immediately

Polling / delivery:
  GET  /custom-demos/{id}/status  — poll until status=ready or error
  GET  /custom-demos/{id}/html    — download the generated HTML
  POST /custom-demos/{id}/revise  — revise with a natural-language prompt
  GET  /custom-demos/             — list all demos
  DELETE /custom-demos/{id}       — delete a demo
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlmodel import col

from app.core.dependencies import CurrentUser, DBSession
from app.models.custom_demo import CustomDemo
from app.services.demo_generator import (
    extract_pdf_text,
    extract_docx_text,
    run_generation,
)
from app.clients.demo_ai import is_valid_demo_html, repair_demo_html

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/custom-demos", tags=["custom-demos"])


# ── Response schemas ──────────────────────────────────────────────────────────

class DemoOut(BaseModel):
    id: UUID
    title: str
    client_name: Optional[str]
    client_domain: Optional[str]
    creation_path: str
    source_filename: Optional[str]
    status: str
    error_message: Optional[str]
    brand_data: Optional[Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DemoStatusOut(BaseModel):
    id: UUID
    status: str
    error_message: Optional[str]


class SceneIn(BaseModel):
    scene_title: str
    beacon_steps: list[str] = []
    client_screen: str = ""
    reveal_description: str = ""


class EditorPayload(BaseModel):
    title: str
    client_name: Optional[str] = None
    client_domain: Optional[str] = None
    company_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    scenes: list[SceneIn]


class BriefPayload(BaseModel):
    title: str
    client_name: Optional[str] = None
    client_domain: Optional[str] = None
    company_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    industry: Optional[str] = None
    company_summary: str
    audience: Optional[str] = None
    business_objectives: list[str] = []
    demo_objectives: list[str] = []
    workflow_overview: str
    key_capabilities: list[str] = []
    scenes_outline: list[str] = []
    success_metrics: list[str] = []
    constraints: list[str] = []
    additional_context: Optional[str] = None


class RevisePayload(BaseModel):
    instruction: str   # natural-language revision instruction


def _brief_payload_to_source_text(payload: BriefPayload) -> str:
    """Convert structured brief fields into a production-guide style text blob."""
    lines: list[str] = [
        "# AI Demo Builder Brief",
        "",
        f"## Demo Title\n{payload.title}",
        "",
        f"## Client Name\n{payload.client_name or payload.title}",
        "",
        f"## Client Domain\n{payload.client_domain or 'Not provided'}",
        "",
        f"## Industry\n{payload.industry or 'Not provided'}",
        "",
        f"## Company Summary\n{payload.company_summary.strip()}",
        "",
        f"## Audience\n{payload.audience or 'Executive and operational stakeholders'}",
        "",
        f"## Workflow Overview\n{payload.workflow_overview.strip()}",
        "",
    ]

    def add_list_section(title: str, items: list[str]) -> None:
        cleaned = [i.strip() for i in items if i and i.strip()]
        lines.append(f"## {title}")
        if not cleaned:
            lines.append("- None provided")
        else:
            lines.extend([f"- {item}" for item in cleaned])
        lines.append("")

    add_list_section("Business Objectives", payload.business_objectives)
    add_list_section("Demo Objectives", payload.demo_objectives)
    add_list_section("Key Capabilities To Highlight", payload.key_capabilities)
    add_list_section("Scenes Outline", payload.scenes_outline)
    add_list_section("Success Metrics", payload.success_metrics)
    add_list_section("Constraints", payload.constraints)

    if payload.additional_context and payload.additional_context.strip():
        lines.append("## Additional Context")
        lines.append(payload.additional_context.strip())
        lines.append("")

    return "\n".join(lines).strip()


# ── Helper ────────────────────────────────────────────────────────────────────

def _demo_to_out(d: CustomDemo) -> DemoOut:
    return DemoOut(
        id=d.id,
        title=d.title,
        client_name=d.client_name,
        client_domain=d.client_domain,
        creation_path=d.creation_path,
        source_filename=d.source_filename,
        status=d.status,
        error_message=d.error_message,
        brand_data=d.brand_data,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[DemoOut])
async def list_demos(session: DBSession, _user: CurrentUser):
    rows = (await session.execute(
        select(CustomDemo).order_by(CustomDemo.created_at.desc())
    )).scalars().all()
    return [_demo_to_out(r) for r in rows]


@router.post("/generate-from-file", response_model=DemoOut, status_code=202)
async def generate_from_file(
    background_tasks: BackgroundTasks,
    session: DBSession,
    _user: CurrentUser,
    file: UploadFile = File(...),
    title: str = Form(...),
    client_name: str = Form(default=""),
    client_domain: str = Form(default=""),
    company_id: Optional[str] = Form(default=None),
    deal_id: Optional[str] = Form(default=None),
):
    """Upload a PDF or DOCX production guide and generate the demo HTML."""
    filename = file.filename or ""
    content_type = file.content_type or ""

    # Extract text from uploaded file
    raw = await file.read()
    if filename.endswith(".pdf") or "pdf" in content_type:
        try:
            source_text = extract_pdf_text(raw)
        except Exception:
            logger.exception("Failed to read PDF upload for custom demo")
            raise HTTPException(status_code=422, detail="Failed to read PDF")
    elif filename.endswith(".docx") or "word" in content_type:
        try:
            source_text = extract_docx_text(raw)
        except Exception:
            logger.exception("Failed to read DOCX upload for custom demo")
            raise HTTPException(status_code=422, detail="Failed to read DOCX")
    else:
        raise HTTPException(status_code=422, detail="Only PDF and DOCX files are supported.")

    if not source_text.strip():
        raise HTTPException(status_code=422, detail="Could not extract any text from the file.")

    demo = CustomDemo(
        title=title,
        client_name=client_name or None,
        client_domain=client_domain or None,
        company_id=UUID(company_id) if company_id else None,
        deal_id=UUID(deal_id) if deal_id else None,
        creation_path="file_upload",
        source_filename=filename,
        source_text=source_text,
        status="draft",
    )
    session.add(demo)
    await session.commit()
    await session.refresh(demo)

    background_tasks.add_task(_bg_generate, demo.id)

    return _demo_to_out(demo)


@router.post("/generate-from-editor", response_model=DemoOut, status_code=202)
async def generate_from_editor(
    payload: EditorPayload,
    background_tasks: BackgroundTasks,
    session: DBSession,
    _user: CurrentUser,
):
    """Submit a scene list built in the in-app editor."""
    editor_content = [s.model_dump() for s in payload.scenes]

    demo = CustomDemo(
        title=payload.title,
        client_name=payload.client_name,
        client_domain=payload.client_domain,
        company_id=payload.company_id,
        deal_id=payload.deal_id,
        creation_path="editor",
        editor_content=editor_content,
        status="draft",
    )
    session.add(demo)
    await session.commit()
    await session.refresh(demo)

    background_tasks.add_task(_bg_generate, demo.id)

    return _demo_to_out(demo)


@router.post("/generate-from-brief", response_model=DemoOut, status_code=202)
async def generate_from_brief(
    payload: BriefPayload,
    background_tasks: BackgroundTasks,
    session: DBSession,
    _user: CurrentUser,
):
    """Generate demo HTML from a structured company/demo brief."""
    source_text = _brief_payload_to_source_text(payload)

    demo = CustomDemo(
        title=payload.title,
        client_name=payload.client_name,
        client_domain=payload.client_domain,
        company_id=payload.company_id,
        deal_id=payload.deal_id,
        creation_path="brief",
        source_filename=None,
        source_text=source_text,
        status="draft",
    )
    session.add(demo)
    await session.commit()
    await session.refresh(demo)

    background_tasks.add_task(_bg_generate, demo.id)

    return _demo_to_out(demo)


@router.get("/{demo_id}/status", response_model=DemoStatusOut)
async def demo_status(demo_id: UUID, session: DBSession, _user: CurrentUser):
    demo = await session.get(CustomDemo, demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail="Demo not found")
    return DemoStatusOut(id=demo.id, status=demo.status, error_message=demo.error_message)


@router.get("/{demo_id}/html")
async def demo_html(demo_id: UUID, session: DBSession, _user: CurrentUser):
    """Return the raw HTML content."""
    demo = await session.get(CustomDemo, demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail="Demo not found")
    if demo.status != "ready":
        raise HTTPException(status_code=409, detail=f"Demo is not ready (status: {demo.status})")

    html = demo.html_content or ""
    if html and not is_valid_demo_html(html):
        try:
            repaired = await asyncio.wait_for(
                repair_demo_html(html, client_name=demo.client_name or demo.title),
                timeout=25,
            )
            html = repaired
        except Exception:
            logger.exception("Demo HTML repair failed for demo %s", demo_id)
            raise HTTPException(
                status_code=409,
                detail=(
                    "Demo HTML is invalid and automatic repair failed. "
                    "Please regenerate or revise this demo."
                ),
            )

        demo.html_content = html
        demo.updated_at = datetime.utcnow()
        session.add(demo)
        await session.commit()
        await session.refresh(demo)

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html, status_code=200)


@router.post("/{demo_id}/revise", response_model=DemoOut, status_code=202)
async def revise_demo(
    demo_id: UUID,
    payload: RevisePayload,
    background_tasks: BackgroundTasks,
    _user: CurrentUser,
    session: DBSession,
):
    """Revise the generated HTML with a natural-language instruction."""
    demo = await session.get(CustomDemo, demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail="Demo not found")
    if not demo.html_content:
        raise HTTPException(status_code=409, detail="No HTML to revise yet.")

    # Append revision instruction to source text for re-generation
    revision_note = f"\n\n---\nREVISION INSTRUCTION:\n{payload.instruction}"
    if demo.creation_path == "editor":
        demo.editor_content = demo.editor_content or []
    else:
        demo.source_text = (demo.source_text or "") + revision_note

    demo.status = "draft"
    demo.html_content = None
    demo.error_message = None
    demo.updated_at = datetime.utcnow()
    session.add(demo)
    await session.commit()
    await session.refresh(demo)

    background_tasks.add_task(_bg_generate, demo.id)

    return _demo_to_out(demo)


@router.delete("/{demo_id}", status_code=204)
async def delete_demo(demo_id: UUID, session: DBSession, _user: CurrentUser):
    demo = await session.get(CustomDemo, demo_id)
    if not demo:
        raise HTTPException(status_code=404, detail="Demo not found")
    await session.delete(demo)
    await session.commit()


# ── Background task wrapper ───────────────────────────────────────────────────

async def _bg_generate(demo_id: UUID) -> None:
    """Async background task — FastAPI BackgroundTasks handles async functions natively."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await run_generation(demo_id, session)
