from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class TldvError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TldvClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = (api_key or settings.TLDV_API_KEY or "").strip()
        self.base_url = (base_url or settings.TLDV_API_BASE or "https://pasta.tldv.io/v1alpha1").rstrip("/")
        self.mock = not bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise TldvError("tl;dv API key is not configured")
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        follow_redirects: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=45, follow_redirects=follow_redirects) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
            )
        if response.status_code >= 400:
            try:
                payload = response.json()
                message = payload.get("message") or payload.get("name") or response.text
            except Exception:
                message = response.text
            raise TldvError(message or f"tl;dv request failed: {response.status_code}", status_code=response.status_code)
        if response.status_code == 302:
            return response.headers.get("Location")
        if not response.content:
            return None
        return response.json()

    async def health(self) -> Any:
        return await self._request("GET", "/health")

    async def list_meetings(self, *, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        return await self._request("GET", "/meetings", params={"page": page, "pageSize": page_size})

    async def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/meetings/{meeting_id}")

    async def get_transcript(self, meeting_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/meetings/{meeting_id}/transcript")

    async def get_highlights(self, meeting_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/meetings/{meeting_id}/highlights")

    async def get_recording_download_url(self, meeting_id: str) -> str | None:
        location = await self._request(
            "GET",
            f"/meetings/{meeting_id}/download",
            follow_redirects=False,
        )
        return location if isinstance(location, str) else None
