"""Zippy chat endpoints — conversations, messages, one-turn sends."""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
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


class ZippyConversationDetail(BaseModel):
    id: UUID
    title: str
    summary: Optional[str] = None
    messages: list[ZippyMessageResponse]
    created_at: str
    updated_at: str


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
    except RuntimeError as exc:
        # Config errors (missing API key etc.) — surface as 503 so the UI can
        # show a "Zippy is not configured" state.
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Zippy turn failed")
        raise HTTPException(status_code=500, detail=f"Zippy failed: {exc}")

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
        .order_by(ZippyConversation.updated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    conversations = list(result.scalars().all())

    # Count messages per conversation in a single follow-up query for the
    # sidebar. N+1 is fine at this scale; we cap limit at 30.
    summaries: list[ZippyConversationSummary] = []
    for convo in conversations:
        count_stmt = sm_select(ZippyMessage).where(ZippyMessage.conversation_id == convo.id)
        count_result = await session.execute(count_stmt)
        count = len(list(count_result.scalars().all()))
        summaries.append(
            ZippyConversationSummary(
                id=convo.id,
                title=convo.title,
                summary=convo.summary,
                message_count=count,
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
