"""Recotap API client.

Thin async wrapper over the Recotap ABM API. Handles X-Api-Key auth, env-based
base URL (mind the hyphen — sandbox reco-tap.com vs prod recotap.com), the four
different response envelopes (§8.2), and keyset pagination (paginate on
hasNextPage only — nextCursor is non-null even on the last page).

Verified contract: docs/RECOTAP_INTEGRATION.md §8.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 25.0


class RecotapClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.base_url = (base_url or settings.recotap_base_url).rstrip("/")
        self.api_key = api_key or settings.recotap_api_key

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.get(f"{self.base_url}{path}", headers=self._headers(), params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def get_journey_stages(self) -> list[str]:
        """Returns the journey-stage labels. Tolerates both the bare-array and the
        enveloped {data:[...]} shapes (the sandbox has returned both)."""
        body = await self._get("/journey-stages")
        if isinstance(body, list):
            return [str(x) for x in body]
        data = body.get("data") if isinstance(body, dict) else None
        return [str(x) for x in data] if isinstance(data, list) else []

    async def get_accounts(
        self,
        *,
        limit: int = 100,
        last_sync: Optional[str] = None,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Paginated account list. Double-nested envelope: rows live at data.data[].
        Loops on hasNextPage (never on nextCursor — it stays populated on the last page)."""
        out: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            if last_sync:
                params["lastSync"] = last_sync
            body = await self._get("/accounts", params=params)
            data = (body or {}).get("data") or {}
            rows = data.get("data") or []
            out.extend([r for r in rows if isinstance(r, dict)])
            if not data.get("hasNextPage"):
                break
            cursor = data.get("nextCursor")
            if not cursor:
                break
        return out

    async def push_accounts(
        self,
        accounts: list[dict[str, Any]],
        segment_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /accounts — insert-only; per-item status created/failed. HTTP is 200
        even when items fail, so callers must read summary/results."""
        payload: dict[str, Any] = {"accounts": accounts}
        if segment_id:
            payload["segmentId"] = segment_id
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.post(f"{self.base_url}/accounts", headers=self._headers(), json=payload)
            resp.raise_for_status()
            body = resp.json()
        return (body or {}).get("data") or {}

    async def update_account(self, rtp_aid: str, fields: dict[str, Any]) -> dict[str, Any]:
        """PUT /accounts/{rtp_aid} — used to set tags on an account that already
        exists in Recotap (POST is insert-only and points here on conflict)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.put(f"{self.base_url}/accounts/{rtp_aid}", headers=self._headers(), json=fields)
            resp.raise_for_status()
            return resp.json()
