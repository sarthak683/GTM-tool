"""
Aircall endpoints — used by the frontend to:
  - Fetch available numbers + users (for config)
  - Get Aircall user ID for the logged-in rep (needed for SDK init)
  - Initiate a call via API (fallback when SDK click-to-dial isn't available)
  - Register the webhook on startup
"""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.clients.aircall import AircallClient, AircallError
from app.config import settings
from app.core.dependencies import CurrentUser

router = APIRouter(prefix="/aircall", tags=["aircall"])


class InitiateCallRequest(BaseModel):
    to: str               # E.164 phone number to call
    user_id: int          # Aircall user/agent ID
    number_id: int        # Aircall number ID to call from


# ── Config endpoint — called on frontend load ─────────────────────────────────

@router.get("/config")
async def get_aircall_config(_user: CurrentUser) -> dict[str, Any]:
    """
    Return the Aircall workspace config needed by the frontend:
    - List of numbers (id + digits + name)
    - List of users/agents (id + name + email)
    - Default number (from env)
    - Whether Aircall is configured
    """
    client = AircallClient()

    if client.is_mock:
        return {
            "configured": False,
            "numbers": [],
            "users": [],
            "default_number": None,
        }

    try:
        numbers, users = await _fetch_config(client)
        default_number = next(
            (n for n in numbers if n.get("digits", "").replace(" ", "") == settings.AIRCALL_DEFAULT_NUMBER.replace(" ", "")),
            numbers[0] if numbers else None,
        )
        return {
            "configured": True,
            "numbers": [{"id": n["id"], "digits": n["digits"], "name": n.get("name", "")} for n in numbers],
            "users": [{"id": u["id"], "name": u.get("name", ""), "email": u.get("email", "")} for u in users],
            "default_number": default_number,
        }
    except AircallError as e:
        raise HTTPException(status_code=502, detail=f"Aircall API error: {e.detail}")


async def _fetch_config(client: AircallClient):
    import asyncio
    numbers, users = await asyncio.gather(
        client.list_numbers(),
        client.list_users(),
    )
    return numbers, users


# ── User lookup — match CRM rep email to Aircall user ID ─────────────────────

@router.get("/user-by-email")
async def get_aircall_user(email: str, _user: CurrentUser) -> dict[str, Any]:
    """
    Given a rep's email, return their Aircall user ID.
    The frontend uses this to init the Aircall Everywhere SDK with the correct user.
    """
    client = AircallClient()
    if client.is_mock:
        return {"found": False}

    try:
        user = await client.get_user_by_email(email)
        if not user:
            return {"found": False}
        return {
            "found": True,
            "aircall_user_id": user["id"],
            "name": user.get("name", ""),
            "availability": user.get("availability", {}).get("availability_status", "unknown"),
        }
    except AircallError as e:
        raise HTTPException(status_code=502, detail=e.detail)


# ── Availabilities ─────────────────────────────────────────────────────────────

@router.get("/availabilities")
async def get_availabilities(_user: CurrentUser) -> list[dict]:
    """
    Return availability status for all agents.
    Used to show green/red dots on rep avatars.
    """
    client = AircallClient()
    if client.is_mock:
        return []
    try:
        return await client.list_user_availabilities()
    except AircallError:
        return []


# ── Initiate call (API fallback) ───────────────────────────────────────────────

@router.post("/call")
async def initiate_call(body: InitiateCallRequest, _user: CurrentUser) -> dict[str, Any]:
    """
    Trigger an outbound call via the Aircall API.
    This is a fallback — the primary path is the Aircall Everywhere SDK
    which handles calling directly from the browser via WebRTC.

    Requires the agent to have the Aircall Desktop app running.
    """
    client = AircallClient()
    if client.is_mock:
        return {"success": False, "reason": "Aircall not configured"}

    try:
        success = await client.initiate_call(
            user_id=body.user_id,
            number_id=body.number_id,
            to=body.to,
        )
        return {"success": success}
    except AircallError as e:
        raise HTTPException(status_code=502, detail=e.detail)


# ── Webhook registration (called on backend startup) ──────────────────────────

@router.post("/register-webhook")
async def register_webhook(_user: CurrentUser) -> dict[str, Any]:
    """
    Idempotently register the CRM webhook URL with Aircall.
    Safe to call multiple times — won't create duplicates.
    """
    if not settings.AIRCALL_WEBHOOK_URL:
        return {"status": "skipped", "reason": "AIRCALL_WEBHOOK_URL not set"}

    client = AircallClient()
    if client.is_mock:
        return {"status": "skipped", "reason": "Aircall not configured"}

    events = [
        "call.created",
        "call.answered",
        "call.ended",
        "call.missed",
        "call.voicemail_left",
        "call.commented",
        "call.tagged",
        "call.comm_assets_generated",
        "summary.created",
        "topics.created",
        "transcription.created",
        "action_item.created",
        "sentiment.created",
    ]

    try:
        result = await client.ensure_webhook(settings.AIRCALL_WEBHOOK_URL, events)
        return {"status": "ok", "webhook": result}
    except AircallError as e:
        return {"status": "error", "detail": e.detail}
