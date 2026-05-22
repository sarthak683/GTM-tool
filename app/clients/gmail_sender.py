from __future__ import annotations

import base64
import html
import logging
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx

from app.config import settings
from app.services.gmail_oauth import GMAIL_SEND_SCOPE, GOOGLE_TOKEN_URL

logger = logging.getLogger(__name__)

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _has_send_scope(token_data: dict | None) -> bool:
    scopes = token_data.get("scopes") if isinstance(token_data, dict) else []
    return GMAIL_SEND_SCOPE in set(scopes or [])


async def _refresh_token_if_needed(token_data: dict) -> dict:
    expiry_str = token_data.get("expiry")
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > datetime.now(timezone.utc) + timedelta(minutes=5):
                return token_data
        except Exception:
            pass

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("No refresh_token available; reconnect the report sender Gmail account.")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    response.raise_for_status()
    payload = response.json()

    updated = dict(token_data)
    updated["token"] = payload["access_token"]
    updated["expiry"] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    ).isoformat()
    if payload.get("scope"):
        updated["scopes"] = payload["scope"].split(" ")
    return updated


def _plain_text_to_html(body: str) -> str:
    escaped = html.escape(body)
    return escaped.replace("\n", "<br>")


def _build_raw_message(
    *,
    from_email: str,
    to: str,
    subject: str,
    body: str,
    from_name: str,
    html_body: str | None = None,
    cc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    include_footer: bool = True,
) -> str:
    message = EmailMessage()
    message["To"] = to
    message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    # Threading headers — when present Gmail keeps the new message in the same
    # conversation, which is the whole point of "reply from CRM."
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    message.set_content(body)
    inner_html = html_body if html_body else _plain_text_to_html(body)
    footer = (
        '<hr style="border:none;border-top:1px solid #e5ebf3;margin:28px 0;">'
        '<p style="font-size:11px;color:#8a98ad;">Sent by Beacon Sales Ops</p>'
        if include_footer
        else ""
    )
    message.add_alternative(
        f"""
        <div style="font-family:Arial,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#24324a;">
          <div style="font-size:15px;line-height:1.65;">{inner_html}</div>
          {footer}
        </div>
        """,
        subtype="html",
    )
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")


async def send_gmail_email(
    *,
    token_data: dict,
    from_email: str,
    to: str,
    subject: str,
    body: str,
    from_name: str = "Beacon Sales Ops",
    html_body: str | None = None,
    cc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    thread_id: str | None = None,
    include_footer: bool = True,
) -> tuple[dict[str, Any], dict]:
    if not _has_send_scope(token_data):
        return (
            {
                "status": "failed",
                "error": "Connected Gmail account is missing gmail.send scope. Reconnect report sender.",
            },
            token_data,
        )

    updated_token = await _refresh_token_if_needed(token_data)
    raw = _build_raw_message(
        from_email=from_email,
        to=to,
        subject=subject,
        body=body,
        from_name=from_name,
        html_body=html_body,
        cc=cc,
        in_reply_to=in_reply_to,
        references=references,
        include_footer=include_footer,
    )

    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        # Gmail keeps a message in an existing thread when threadId is set on
        # the send payload (independent of the In-Reply-To header).
        payload["threadId"] = thread_id

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            GMAIL_SEND_URL,
            headers={
                "Authorization": f"Bearer {updated_token['token']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        logger.error("Gmail send failed: %s %s", response.status_code, response.text[:500])
        return (
            {
                "status": "failed",
                "status_code": response.status_code,
                "error": response.text,
            },
            updated_token,
        )

    payload = response.json()
    return (
        {
            "status": "sent",
            "id": payload.get("id"),
            "thread_id": payload.get("threadId"),
        },
        updated_token,
    )
