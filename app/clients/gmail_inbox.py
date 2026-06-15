"""
Gmail shared inbox client — polls for new emails via Gmail API.

Design:
  - Uses OAuth2 with offline refresh token (one-time browser consent).
  - Tracks last-processed historyId in Redis for efficient incremental sync.
  - Returns parsed EmailMessage dicts ready for matching → activity creation.

One-time setup:
  python -m app.clients.gmail_inbox
"""
from __future__ import annotations

import base64
import email.utils
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings
from app.services.gmail_oauth import GMAIL_SCOPE, GOOGLE_TOKEN_URL

logger = logging.getLogger(__name__)

# Scopes: read-only access to the shared inbox
SCOPES = [GMAIL_SCOPE]


@dataclass
class EmailMessage:
    """Parsed email from the shared inbox."""
    message_id: str          # RFC Message-ID header (globally unique)
    gmail_id: str            # Gmail's internal message ID
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    body_text: str = ""      # Plain text body (truncated to 3000 chars)
    date: str = ""           # RFC 2822 date string
    thread_id: str = ""


def _extract_addrs(header_value: str) -> list[str]:
    """Extract all email addresses from a To/Cc header value."""
    if not header_value:
        return []
    # email.utils.getaddresses handles "Name <addr>, Name2 <addr2>" etc.
    pairs = email.utils.getaddresses([header_value])
    return [addr.lower().strip() for _name, addr in pairs if addr]


def _serialize_credentials(creds) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "scopes": list(creds.scopes or SCOPES),
        "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
    }


def _get_credentials(token_payload: Optional[dict] = None):
    """Load or refresh OAuth2 credentials from token file."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(settings.GMAIL_TOKEN_JSON)
    creds = None
    updated_token_payload = None

    if token_payload:
        info = {
            "token": token_payload.get("token"),
            "refresh_token": token_payload.get("refresh_token"),
            "scopes": token_payload.get("scopes") or SCOPES,
            "expiry": token_payload.get("expiry"),
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
            "token_uri": GOOGLE_TOKEN_URL,
        }
        creds = Credentials.from_authorized_user_info(info, SCOPES)
    elif token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if token_payload is not None:
            updated_token_payload = _serialize_credentials(creds)
        else:
            token_path.write_text(creds.to_json())

    if not creds or not creds.valid:
        return None, updated_token_payload

    return creds, updated_token_payload


def _build_service(token_payload: Optional[dict] = None):
    """Build Gmail API service client."""
    from googleapiclient.discovery import build

    creds, updated_token_payload = _get_credentials(token_payload)
    if not creds:
        return None, updated_token_payload
    return build("gmail", "v1", credentials=creds, cache_discovery=False), updated_token_payload


class GmailInboxClient:
    """Polls a Gmail shared inbox for new messages."""

    def __init__(self, inbox: Optional[str] = None, token_payload: Optional[dict] = None) -> None:
        self.inbox = inbox or settings.GMAIL_SHARED_INBOX
        self.token_payload = token_payload
        self.updated_token_payload: Optional[dict] = None
        self.enabled = bool(self.inbox and (self.token_payload or settings.GMAIL_TOKEN_JSON))

    def fetch_new_messages(self, after_epoch: int, max_results: int = 50) -> list[EmailMessage]:
        """
        Fetch messages received after `after_epoch` (unix timestamp).

        Uses Gmail search query `after:EPOCH` which is simple, reliable,
        and doesn't require historyId management. Returns newest first.
        """
        if not self.enabled:
            logger.debug("Gmail sync disabled — no inbox configured")
            return []

        service, updated_token_payload = _build_service(self.token_payload)
        if updated_token_payload:
            self.updated_token_payload = updated_token_payload
        if not service:
            logger.warning("Gmail credentials not valid — run setup first")
            return []

        try:
            query = f"after:{after_epoch}"
            results = service.users().messages().list(
                userId="me", q=query, maxResults=max_results,
            ).execute()

            msg_ids = results.get("messages", [])
            if not msg_ids:
                return []

            messages: list[EmailMessage] = []
            for msg_ref in msg_ids:
                parsed = self._fetch_message(service, msg_ref["id"])
                if parsed:
                    messages.append(parsed)

            logger.info(f"Gmail sync: fetched {len(messages)} new messages")
            return messages

        except Exception as e:
            logger.error(f"Gmail fetch failed: {e}")
            return []

    def _fetch_message(self, service, gmail_id: str) -> Optional[EmailMessage]:
        """Fetch and parse a single Gmail message."""
        try:
            msg = service.users().messages().get(
                userId="me", id=gmail_id, format="full",
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

            message_id = headers.get("message-id", gmail_id)
            subject = headers.get("subject", "(no subject)")
            from_raw = headers.get("from", "")
            to_raw = headers.get("to", "")
            cc_raw = headers.get("cc", "")
            date_raw = headers.get("date", "")

            # Parse sender
            from_pairs = email.utils.getaddresses([from_raw])
            from_name = from_pairs[0][0] if from_pairs else ""
            from_addr = from_pairs[0][1].lower() if from_pairs else ""

            # Extract body text
            body_text = self._extract_body(msg.get("payload", {}))

            return EmailMessage(
                message_id=message_id,
                gmail_id=gmail_id,
                subject=subject,
                from_addr=from_addr,
                from_name=from_name,
                to_addrs=_extract_addrs(to_raw),
                cc_addrs=_extract_addrs(cc_raw),
                body_text=body_text[:3000],  # Truncate for cost efficiency
                date=date_raw,
                thread_id=msg.get("threadId", ""),
            )
        except Exception as e:
            logger.error(f"Failed to parse message {gmail_id}: {e}")
            return None

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract plain text body from Gmail message payload."""
        mime_type = payload.get("mimeType", "")

        # Direct text/plain part
        if mime_type == "text/plain" and "body" in payload:
            data = payload["body"].get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Multipart — recurse through parts, prefer text/plain
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Fallback: try text/html → strip tags
        for part in parts:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html).strip()

        # Nested multipart
        for part in parts:
            nested = self._extract_body(part)
            if nested:
                return nested

        return ""


# ── One-time setup (run: python -m app.clients.gmail_inbox) ─────────────────

def _run_oauth_flow():
    """Interactive OAuth2 consent flow — run once to generate token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = Path(settings.GMAIL_CREDENTIALS_JSON)
    if not creds_path.exists():
        print(f"ERROR: Credentials file not found at {creds_path}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = Path(settings.GMAIL_TOKEN_JSON)
    token_path.write_text(creds.to_json())
    print(f"Token saved to {token_path}")
    print("Gmail sync is now configured!")


if __name__ == "__main__":
    _run_oauth_flow()
