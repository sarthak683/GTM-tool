"""Gmail API client — thin async wrapper for thread search + content fetch.

Used by Zippy's PoC Kickoff generator (and any future tool that needs to
read the AE's inbox to ground a document). Auth model mirrors
``app/clients/google_drive.py``: every public function takes an optional
``user_id`` and pulls the matching ``UserEmailConnection`` row to source
the OAuth token, with a refresh hop through ``_refresh_token_if_needed``
so we never hand out an expired access token.

Two operations are exposed:

  * ``search_threads`` — list-style results with subject/from/date/snippet,
    enough metadata for Zippy to show the AE which threads it found before
    actually reading any of them.
  * ``get_thread_content`` — full decoded payload of a single thread,
    flattened into one ``full_text`` block ready to feed to Claude.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Optional

import httpx
from sqlmodel import select as sm_select

from app.clients.gmail_inbox import _get_credentials
from app.database import AsyncSessionLocal as async_session
from app.models.user_email_connection import UserEmailConnection

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


async def _get_gmail_token(user_id: Optional[str] = None) -> tuple[str, dict]:
    """Get a valid Gmail access token for the user.

    Picks the most-recent active connection for ``user_id`` (or any active
    connection if ``user_id`` is None). Raises if the user has no Gmail
    connection at all — callers turn that into a tool error so the agent
    can ask the user to reconnect.
    """
    async with async_session() as session:
        stmt = sm_select(UserEmailConnection).where(
            UserEmailConnection.is_active == True,  # noqa: E712
        )
        if user_id:
            stmt = stmt.where(UserEmailConnection.user_id == user_id)
        stmt = stmt.order_by(UserEmailConnection.connected_at.desc())
        result = await session.execute(stmt.limit(1))
        connection = result.scalar_one_or_none()

    if not connection:
        raise RuntimeError("No active Gmail connection found.")

    # Delegate to the same helper the personal-email-sync task uses —
    # google.auth handles refresh + scope validation correctly, whereas
    # our raw-httpx refresher mishandles the token shape across calls.
    creds, updated_payload = _get_credentials(connection.token_data)
    if not creds or not creds.valid:
        raise RuntimeError(
            "Gmail credentials invalid. User must reconnect Google in "
            "Settings > Integrations."
        )

    # Persist refreshed token so we don't re-refresh on every call.
    if updated_payload:
        async with async_session() as session:
            stmt = sm_select(UserEmailConnection).where(
                UserEmailConnection.id == connection.id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                row.token_data = updated_payload
                await session.commit()
        connection.token_data = updated_payload

    return creds.token, connection.token_data


async def search_threads(
    *,
    query: str,
    page_size: int = 5,
    user_id: Optional[str] = None,
) -> list[dict]:
    """Search Gmail and return thread summaries.

    Two-step flow because the list endpoint only returns thread IDs: we
    list, then GET each thread with ``format=metadata`` to pull the
    Subject/From/Date headers + snippet. This costs (page_size + 1)
    round-trips, but page_size is small by design (≤10).
    """
    access_token, _ = await _get_gmail_token(user_id)

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(
            f"{GMAIL_API_BASE}/users/me/threads",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": query, "maxResults": page_size},
        )

    if resp.status_code != 200:
        logger.warning(
            "Gmail thread search failed (status %s): %s",
            resp.status_code, resp.text[:300],
        )
        return []

    threads_raw = resp.json().get("threads", [])
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as http:
        for t in threads_raw:
            thread_resp = await http.get(
                f"{GMAIL_API_BASE}/users/me/threads/{t['id']}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            )
            if thread_resp.status_code != 200:
                continue
            td = thread_resp.json()
            messages = td.get("messages", [])
            headers: dict[str, str] = {}
            if messages:
                for h in messages[0].get("payload", {}).get("headers", []):
                    headers[h["name"]] = h["value"]
            results.append({
                "id": t["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "sender": headers.get("From", "unknown"),
                "date": headers.get("Date", ""),
                "snippet": td.get("snippet", ""),
                "message_count": len(messages),
            })

    return results


async def get_thread_content(
    *,
    thread_id: str,
    user_id: Optional[str] = None,
) -> dict:
    """Fetch full decoded content of a Gmail thread.

    Walks every message in the thread, prefers ``text/plain`` parts, and
    falls back to stripping HTML when only ``text/html`` is available.
    Returns a dict with per-message details plus a ``full_text`` blob
    ready to drop into a Claude prompt.
    """
    access_token, _ = await _get_gmail_token(user_id)

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(
            f"{GMAIL_API_BASE}/users/me/threads/{thread_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )

    if resp.status_code != 200:
        logger.warning(
            "Gmail thread fetch failed (status %s): %s",
            resp.status_code, resp.text[:300],
        )
        return {}

    td = resp.json()
    messages_out: list[dict] = []

    def decode_body(payload: dict) -> str:
        """Recursively extract plain text from a message payload.

        Gmail nests parts arbitrarily (multipart/alternative within
        multipart/mixed within multipart/related, etc.), so we recurse
        until we hit a text part. Plain text wins; HTML is a fallback
        because tag-stripping can flatten formatting in unhelpful ways.
        """
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data + "==").decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    return ""
        if mime.startswith("multipart"):
            for part in payload.get("parts", []):
                text = decode_body(part)
                if text:
                    return text
        # Fallback: HTML → strip tags. Lossy but better than nothing when
        # an email is sent HTML-only (common for marketing-tool replies).
        if mime == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                try:
                    html = base64.urlsafe_b64decode(data + "==").decode(
                        "utf-8", errors="replace"
                    )
                    return re.sub(r"<[^>]+>", " ", html).strip()
                except Exception:
                    return ""
        return ""

    for msg in td.get("messages", []):
        headers: dict[str, str] = {}
        for h in msg.get("payload", {}).get("headers", []):
            headers[h["name"]] = h["value"]
        body = decode_body(msg.get("payload", {}))
        messages_out.append({
            "id": msg["id"],
            "sender": headers.get("From", "unknown"),
            "date": headers.get("Date", ""),
            "subject": headers.get("Subject", ""),
            "body": body,
        })

    # Concatenate all messages into one block for feeding to Claude.
    # Per-message header banner gives the LLM enough structure to attribute
    # statements ("AE said X on date Y") without us having to do that work.
    full_text_parts = []
    for m in messages_out:
        full_text_parts.append(
            f"--- Email from {m['sender']} on {m['date']} ---\n"
            f"Subject: {m['subject']}\n\n"
            f"{m['body']}\n"
        )

    return {
        "id": thread_id,
        "subject": messages_out[0]["subject"] if messages_out else "",
        "messages": messages_out,
        "full_text": "\n\n".join(full_text_parts),
    }
