from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.gmail_oauth import GOOGLE_TOKEN_URL

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_DOC_ID_RE = re.compile(r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")


def extract_google_doc_links(text: str | None) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(match.group(0) for match in GOOGLE_DOC_ID_RE.finditer(text)))


def _extract_doc_id(url: str) -> str | None:
    match = GOOGLE_DOC_ID_RE.search(url or "")
    return match.group(1) if match else None


async def _refresh_token_if_needed(token_data: dict, client_id: str, client_secret: str) -> dict:
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
        raise ValueError("No refresh_token available")

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        new_tokens = resp.json()

    updated = dict(token_data)
    updated["token"] = new_tokens["access_token"]
    updated["expiry"] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(new_tokens.get("expires_in", 3600)))
    ).isoformat()
    return updated


async def fetch_google_doc_context(
    text: str | None,
    *,
    token_data: dict | None,
    client_id: str,
    client_secret: str,
    max_docs: int = 2,
    max_chars_per_doc: int = 2500,
) -> tuple[list[dict[str, str]], dict | None]:
    links = extract_google_doc_links(text)
    if not links or not token_data:
        return [], token_data

    granted_scopes = token_data.get("scopes", [])
    if isinstance(granted_scopes, list):
        has_drive_scope = any(DRIVE_SCOPE in scope for scope in granted_scopes)
    else:
        has_drive_scope = DRIVE_SCOPE in str(granted_scopes)
    if not has_drive_scope:
        return [], token_data

    updated_token = await _refresh_token_if_needed(token_data, client_id, client_secret)
    access_token = updated_token.get("token")
    if not access_token:
        return [], updated_token

    contexts: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=20) as http:
        for url in links[:max_docs]:
            doc_id = _extract_doc_id(url)
            if not doc_id:
                continue
            try:
                export_resp = await http.get(
                    f"https://www.googleapis.com/drive/v3/files/{doc_id}/export",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"mimeType": "text/plain"},
                )
                if export_resp.status_code in {403, 404}:
                    continue
                export_resp.raise_for_status()
                doc_text = export_resp.text.strip()
                if not doc_text:
                    continue

                meta_resp = await http.get(
                    f"https://www.googleapis.com/drive/v3/files/{doc_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"fields": "name"},
                )
                title = "Google Doc transcript"
                if meta_resp.is_success:
                    title = str((meta_resp.json() or {}).get("name") or title)

                contexts.append(
                    {
                        "url": url,
                        "title": title,
                        "text": doc_text[:max_chars_per_doc],
                    }
                )
            except Exception as exc:
                logger.warning("google_docs: failed to fetch transcript doc %s: %s", doc_id, exc)
                continue

    return contexts, updated_token
