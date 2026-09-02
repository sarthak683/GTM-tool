"""Zippy chat endpoints — conversations, messages, one-turn sends."""
from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select as sm_select

from app.config import settings
from app.core.dependencies import CurrentUser, DBSession
from app.models.zippy import (
    ZippyConversation,
    ZippyMessage,
)
from app.services.zippy_agent import AgentTurn, run_turn
from app.services.zippy_docs.storage import load_document, retention_days

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/zippy", tags=["zippy"])


# ── Schemas ───────────────────────────────────────────────────────────────────


class ZippyMessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    citations: Optional[list[dict]] = None
    artifacts: Optional[list[dict]] = None
    created_at: str


class ZippyConversationSummary(BaseModel):
    id: UUID
    title: str
    summary: Optional[str] = None
    updated_at: str
    created_at: str
    message_count: int
    is_pinned: bool = False


class ZippyConversationDetail(BaseModel):
    id: UUID
    title: str
    summary: Optional[str] = None
    messages: list[ZippyMessageResponse]
    created_at: str
    updated_at: str
    is_pinned: bool = False


class SendMessageRequest(BaseModel):
    conversation_id: Optional[UUID] = None
    message: str
    source_ids: Optional[list[str]] = None  # Restrict retrieval to these files.
    # Optional image payload for vision-enabled turns (e.g. a LinkedIn
    # profile screenshot the user wants Zippy to read). We don't persist
    # the image — it only travels into the current Claude call.
    image_base64: Optional[str] = None
    image_media_type: Optional[str] = None


class SendMessageResponse(BaseModel):
    conversation_id: UUID
    message: ZippyMessageResponse


# ── Helpers ───────────────────────────────────────────────────────────────────


def _message_to_response(msg: ZippyMessage) -> ZippyMessageResponse:
    return ZippyMessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        citations=msg.citations,
        artifacts=msg.artifacts,
        created_at=msg.created_at.isoformat() if msg.created_at else "",
    )


def _agent_turn_to_response(turn: AgentTurn) -> SendMessageResponse:
    return SendMessageResponse(
        conversation_id=turn.conversation_id,
        message=ZippyMessageResponse(
            id=turn.message_id,
            conversation_id=turn.conversation_id,
            role="assistant",
            content=turn.content,
            citations=turn.citations or None,
            artifacts=turn.artifacts or None,
            created_at=turn.created_at.isoformat(),
        ),
    )


# ── Generated-document download ───────────────────────────────────────────────
#
# Zippy's generators used to write .docx/.xlsx/.pptx files to a directory inside
# the container and publish them through a StaticFiles mount at /zippy_outputs.
# With two backend replicas and no shared volume that link was a coin flip — the
# file only existed on the pod that generated it — and every restart or redeploy
# destroyed the lot. The bytes now live in Postgres (see
# app.services.zippy_docs.storage) and are served from here, which every replica
# can do and which survives a restart.
#
# This route is intentionally NOT behind CurrentUser. The link is rendered as a
# plain <a href target="_blank"> in the chat transcript, and browser navigation
# cannot carry the app's bearer token, so requiring auth would turn every
# download into a 401. The 256-bit token in the path is the credential instead —
# the same capability-URL posture as the static mount it replaces, but with far
# more entropy than the old guessable "mom-Acme-2026-08-17-a1b2c3.docx" names.


def _wants_json(request: Request) -> bool:
    """True when the caller is an API client rather than a navigating browser."""
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return False
    return "application/json" in accept or accept in ("", "*/*")


def document_unavailable_response(
    request: Request,
    *,
    status_code: int,
    headline: str,
    detail: str,
) -> Response:
    """Explain why a document link is dead, instead of a bare 404.

    A missing file used to produce an unstyled framework 404 with no indication
    of what had happened or what to do about it, which is indistinguishable from
    the app being broken. Since these links are opened by clicking an anchor,
    the browser renders whatever comes back — so return a real page that names
    the cause and points at the fix (ask Zippy to regenerate it), and reserve
    the JSON shape for programmatic callers.
    """
    if _wants_json(request):
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail, "error": headline},
        )

    # Escaped even though every value here is server-side: `detail` embeds the
    # stored filename, which comes from a generator's slug of a user-supplied
    # client name. The slug strips angle brackets today — this makes the page
    # safe regardless of whether it always will.
    headline = escape(headline)
    detail = escape(detail)
    app_url = escape((settings.FRONTEND_URL or "").rstrip("/"), quote=True)
    back_link = (
        f'<p class="actions"><a href="{app_url}/zippy">Back to Zippy</a></p>'
        if app_url
        else ""
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{headline}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #faf9f7; color: #1c1917;
    font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  .card {{
    max-width: 30rem; margin: 2rem; padding: 2rem 2.25rem; background: #fff;
    border: 1px solid #e7e5e4; border-radius: 14px;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
  }}
  h1 {{ margin: 0 0 .75rem; font-size: 1.15rem; letter-spacing: -.01em; }}
  p {{ margin: 0 0 .75rem; color: #57534e; }}
  .actions a {{
    display: inline-block; margin-top: .5rem; padding: .5rem .9rem;
    background: #7c3aed; color: #fff; border-radius: 8px;
    text-decoration: none; font-weight: 600;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1c1917; color: #f5f5f4; }}
    .card {{ background: #292524; border-color: #44403c; }}
    p {{ color: #d6d3d1; }}
  }}
</style>
</head>
<body>
  <div class="card">
    <h1>{headline}</h1>
    <p>{detail}</p>
    <p>Ask Zippy to generate it again — the conversation it came from still has
       all the context.</p>
    {back_link}
  </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=status_code)


@router.get("/documents/{token}")
async def download_document(token: str, request: Request) -> Response:
    """Serve a Zippy-generated document by capability token."""
    record = await load_document(token)

    if record is None:
        return document_unavailable_response(
            request,
            status_code=404,
            headline="This document isn’t available",
            detail=(
                "We couldn’t find a document for this link. It may have been "
                "removed, or the link may be incomplete."
            ),
        )

    if record.expires_at and record.expires_at < datetime.utcnow():
        return document_unavailable_response(
            request,
            status_code=410,
            headline="This document has expired",
            detail=(
                f"Zippy keeps generated documents for {retention_days()} days. "
                f"“{record.filename}” was created on "
                f"{record.created_at.strftime('%d %b %Y')} and has since been "
                "cleared."
            ),
        )

    # ASCII-only fallback plus RFC 5987 form, so a client name with an accent
    # cannot produce a header the browser rejects.
    ascii_name = (
        record.filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    )
    quoted = quote(record.filename)
    return Response(
        content=record.data,
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quoted}"
            ),
            "Content-Length": str(len(record.data)),
            # Immutable content behind an unguessable token — but keep it
            # private so no shared cache holds a document the token owner
            # later expects to have expired.
            "Cache-Control": "private, max-age=3600",
        },
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/send", response_model=SendMessageResponse)
async def send_message(
    payload: SendMessageRequest,
    session: DBSession,
    current_user: CurrentUser,
) -> SendMessageResponse:
    """Send a user message, run the agent, return the assistant reply."""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    try:
        turn = await run_turn(
            session,
            user_id=current_user.id,
            user_message=payload.message,
            conversation_id=payload.conversation_id,
            source_ids=payload.source_ids,
            image_base64=payload.image_base64,
            image_media_type=payload.image_media_type,
        )
    except RuntimeError:
        # Config errors (missing API key etc.) — surface as 503 so the UI can
        # show a "Zippy is not configured" state. The real detail goes to logs;
        # we never echo internal config text back to the client.
        logger.exception("Zippy turn failed: not configured")
        raise HTTPException(status_code=503, detail="Zippy is not configured")
    except Exception:
        logger.exception("Zippy turn failed")
        raise HTTPException(status_code=500, detail="Zippy failed")

    return _agent_turn_to_response(turn)


@router.get("/conversations", response_model=list[ZippyConversationSummary])
async def list_conversations(
    session: DBSession,
    current_user: CurrentUser,
    limit: int = 30,
) -> list[ZippyConversationSummary]:
    stmt = (
        sm_select(ZippyConversation)
        .where(
            ZippyConversation.user_id == current_user.id,
            ZippyConversation.is_archived.is_(False),
        )
        .order_by(
            ZippyConversation.is_pinned.desc(),
            ZippyConversation.updated_at.desc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    conversations = list(result.scalars().all())

    # Count messages per conversation in a single grouped query for the
    # sidebar — the old per-conversation loop loaded every message row
    # (content + tool_trace JSONB) just to take len() of it.
    counts: dict = {}
    if conversations:
        count_rows = await session.execute(
            sm_select(ZippyMessage.conversation_id, func.count())
            .where(ZippyMessage.conversation_id.in_([c.id for c in conversations]))
            .group_by(ZippyMessage.conversation_id)
        )
        counts = {row[0]: row[1] for row in count_rows.all()}

    summaries: list[ZippyConversationSummary] = []
    for convo in conversations:
        count = counts.get(convo.id, 0)
        summaries.append(
            ZippyConversationSummary(
                id=convo.id,
                title=convo.title,
                summary=convo.summary,
                message_count=count,
                is_pinned=bool(convo.is_pinned),
                created_at=convo.created_at.isoformat() if convo.created_at else "",
                updated_at=convo.updated_at.isoformat() if convo.updated_at else "",
            )
        )
    return summaries


@router.get("/conversations/{conversation_id}", response_model=ZippyConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    session: DBSession,
    current_user: CurrentUser,
) -> ZippyConversationDetail:
    stmt = sm_select(ZippyConversation).where(
        ZippyConversation.id == conversation_id,
        ZippyConversation.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages_stmt = (
        sm_select(ZippyMessage)
        .where(ZippyMessage.conversation_id == conversation_id)
        .order_by(ZippyMessage.created_at.asc())
    )
    messages_result = await session.execute(messages_stmt)
    messages = list(messages_result.scalars().all())

    return ZippyConversationDetail(
        id=convo.id,
        title=convo.title,
        summary=convo.summary,
        is_pinned=bool(convo.is_pinned),
        messages=[_message_to_response(m) for m in messages],
        created_at=convo.created_at.isoformat() if convo.created_at else "",
        updated_at=convo.updated_at.isoformat() if convo.updated_at else "",
    )


class ArchiveRequest(BaseModel):
    is_archived: bool = True


@router.post("/conversations/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: UUID,
    payload: ArchiveRequest,
    session: DBSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    stmt = sm_select(ZippyConversation).where(
        ZippyConversation.id == conversation_id,
        ZippyConversation.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.is_archived = payload.is_archived
    session.add(convo)
    await session.commit()
    return {"id": str(convo.id), "is_archived": convo.is_archived}


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    is_pinned: Optional[bool] = None


@router.patch("/conversations/{conversation_id}", response_model=ZippyConversationSummary)
async def update_conversation(
    conversation_id: UUID,
    payload: UpdateConversationRequest,
    session: DBSession,
    current_user: CurrentUser,
) -> ZippyConversationSummary:
    """Rename and/or pin a conversation. Any omitted field is left as-is."""
    stmt = sm_select(ZippyConversation).where(
        ZippyConversation.id == conversation_id,
        ZippyConversation.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if payload.title is not None:
        cleaned = payload.title.strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        if len(cleaned) > 200:
            raise HTTPException(status_code=400, detail="title too long (max 200 chars)")
        convo.title = cleaned
    if payload.is_pinned is not None:
        convo.is_pinned = bool(payload.is_pinned)

    session.add(convo)
    await session.commit()
    await session.refresh(convo)

    count_stmt = sm_select(ZippyMessage).where(ZippyMessage.conversation_id == convo.id)
    count_result = await session.execute(count_stmt)
    count = len(list(count_result.scalars().all()))

    return ZippyConversationSummary(
        id=convo.id,
        title=convo.title,
        summary=convo.summary,
        message_count=count,
        is_pinned=bool(convo.is_pinned),
        created_at=convo.created_at.isoformat() if convo.created_at else "",
        updated_at=convo.updated_at.isoformat() if convo.updated_at else "",
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    session: DBSession,
    current_user: CurrentUser,
) -> Response:
    """Hard-delete a conversation and all of its messages.

    We chose a true delete (not just `is_archived=True`) because users
    pressing the trash icon expect the row to disappear and not linger as
    hidden state. Messages are removed in the same transaction so we
    never leave orphans.
    """
    stmt = sm_select(ZippyConversation).where(
        ZippyConversation.id == conversation_id,
        ZippyConversation.user_id == current_user.id,
    )
    result = await session.execute(stmt)
    convo = result.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs_stmt = sm_select(ZippyMessage).where(ZippyMessage.conversation_id == convo.id)
    msgs_result = await session.execute(msgs_stmt)
    for msg in msgs_result.scalars().all():
        await session.delete(msg)
    await session.delete(convo)
    await session.commit()
    return Response(status_code=204)


@router.get("/companies", response_model=list[str])
async def list_company_names(
    session: DBSession,
    current_user: CurrentUser,
) -> list[str]:
    """Return the caller's visible company names for fuzzy matching in the Zippy composer.

    Account-scoped like every other company-browse surface: this fed the
    composer the FULL account list to any authenticated user, which leaked the
    names of accounts the caller cannot otherwise see.
    """
    from app.models.company import Company
    from app.repositories.company import company_visibility_filter

    result = await session.execute(
        sm_select(Company.name)
        .where(company_visibility_filter(current_user.id, current_user.is_admin))
        .order_by(Company.name)
    )
    names = [row[0] for row in result.all() if row[0]]
    return names
