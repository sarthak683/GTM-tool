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
        # Strip stray whitespace/newlines — a space or CR pasted into the env/secret
        # would otherwise produce an "Illegal header value" on the X-Api-Key header.
        self.api_key = (api_key or settings.recotap_api_key or "").strip()
        # Set from the latest GET /accounts response so callers can persist it and
        # request only changes next time (incremental pull via lastSync).
        self.last_sync_timestamp: Optional[str] = None

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
            # Recotap stamps every page with an "as of" marker; keep the latest so
            # the caller can store it and pull incrementally next time.
            if data.get("syncTimestamp"):
                self.last_sync_timestamp = data.get("syncTimestamp")
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
        upsert: bool = True,
    ) -> dict[str, Any]:
        """POST /accounts with ``upsert: true`` so an account that already exists
        (matched by domain) is UPDATED rather than rejected. Verified 2026-06-23:
        without the flag Recotap returns status=failed ("Account with domain ...
        already exists ... set upsert: true to update via this endpoint"). HTTP is
        200 even when items fail, so callers must read summary/results."""
        payload: dict[str, Any] = {"accounts": accounts, "upsert": upsert}
        if segment_id:
            payload["segmentId"] = segment_id
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.post(f"{self.base_url}/accounts", headers=self._headers(), json=payload)
            resp.raise_for_status()
            body = resp.json()
        return (body or {}).get("data") or {}

    # Recotap rejects a request carrying more than 100 deals (documented limit on
    # POST /deals). Chunking is the client's job so callers can hand over the
    # whole changed set without knowing the ceiling.
    DEAL_BATCH_LIMIT = 100

    async def push_deals(self, deals: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /deals — upsert by ``externalDealId`` (creates when unknown,
        updates when matched).

        Splits into DEAL_BATCH_LIMIT-sized requests and merges the per-item
        results into one envelope. Like POST /accounts this returns HTTP 200 even
        when individual deals fail, so the caller MUST read ``results[]``; a
        raise_for_status() alone would report a batch of 100 rejections as
        success. A failed batch is recorded as failed items rather than aborting
        the remaining batches — one bad chunk should not strand the rest.
        """
        results: list[dict[str, Any]] = []
        summary = {"total": 0, "upserted": 0, "failed": 0}
        if not deals:
            return {"results": results, "summary": summary}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            for start in range(0, len(deals), self.DEAL_BATCH_LIMIT):
                chunk = deals[start:start + self.DEAL_BATCH_LIMIT]
                try:
                    resp = await http.post(
                        f"{self.base_url}/deals", headers=self._headers(), json={"deals": chunk}
                    )
                    resp.raise_for_status()
                    body = resp.json() or {}
                except Exception as exc:
                    logger.warning(
                        "recotap push_deals: batch %s-%s failed: %s",
                        start, start + len(chunk), str(exc)[:200],
                    )
                    results.extend(
                        {"externalDealId": d.get("externalDealId"), "status": "failed",
                         "error": str(exc)[:200]}
                        for d in chunk
                    )
                    summary["total"] += len(chunk)
                    summary["failed"] += len(chunk)
                    continue

                # The documented envelope is flat ({results, summary}); POST
                # /accounts nests the same shape under "data". Accept both rather
                # than silently reading zero results off the wrong one.
                payload = body.get("data") if isinstance(body.get("data"), dict) else body
                batch_results = payload.get("results") or []
                results.extend(r for r in batch_results if isinstance(r, dict))
                batch_summary = payload.get("summary") or {}
                summary["total"] += int(batch_summary.get("total") or len(chunk))
                summary["upserted"] += int(batch_summary.get("upserted") or 0)
                summary["failed"] += int(batch_summary.get("failed") or 0)

        return {"results": results, "summary": summary}

    async def update_account(self, rtp_aid: str, fields: dict[str, Any]) -> dict[str, Any]:
        """PUT /accounts/{rtp_aid} — used to set tags on an account that already
        exists in Recotap (POST is insert-only and points here on conflict)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.put(f"{self.base_url}/accounts/{rtp_aid}", headers=self._headers(), json=fields)
            resp.raise_for_status()
            return resp.json()
