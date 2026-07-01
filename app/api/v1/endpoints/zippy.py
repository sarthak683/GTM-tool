"""Zippy chat endpoints — conversations, messages, one-turn sends."""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select as sm_select

from app.core.dependencies import CurrentUser, DBSession
from app.models.zippy import (
    ZippyConversation,
    ZippyMessage,
)
from app.services.zippy_agent import AgentTurn, run_turn

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
    """Return all company names for fuzzy matching in the Zippy composer."""
    from app.models.company import Company

    result = await session.execute(
        sm_select(Company.name).order_by(Company.name)
    )
    names = [row[0] for row in result.all() if row[0]]
    return names
