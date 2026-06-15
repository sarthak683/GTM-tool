"""
Aircall API client — v1/v2 REST API.

Auth: HTTP Basic — base64(api_id:api_token) in Authorization header.
Rate limit: 60 requests/minute per company.

Key capabilities used:
  - List numbers + users (for config/setup)
  - Initiate outbound call via API (fallback to SDK)
  - Register webhooks idempotently
  - Fetch call details (recording URL, duration, outcome)
  - Add notes/tags to calls (sync CRM notes back to Aircall)
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_V1 = "https://api.aircall.io/v1"
_BASE_V2 = "https://api.aircall.io/v2"


class AircallError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Aircall API error {status_code}: {detail}")


class AircallClient:
    """
    Async Aircall REST client.

    Usage:
        client = AircallClient()
        if client.is_mock:
            # API credentials not configured
            ...
        numbers = await client.list_numbers()
    """

    def __init__(self) -> None:
        self.api_id = settings.AIRCALL_API_ID
        self.api_token = settings.AIRCALL_API_TOKEN
        self.is_mock = not (self.api_id and self.api_token)
        if self.is_mock:
            logger.warning("AircallClient: credentials not set — running in mock mode")

    def _headers(self) -> dict:
        credentials = base64.b64encode(
            f"{self.api_id}:{self.api_token}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        base: str = _BASE_V1,
        json: Any = None,
        params: dict | None = None,
        timeout: int = 20,
    ) -> Any:
        if self.is_mock:
            logger.warning("AircallClient mock — skipping %s %s", method, path)
            return None

        url = f"{base}{path}"
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.request(
                method, url,
                headers=self._headers(),
                json=json,
                params=params,
            )

        if not resp.is_success:
            raise AircallError(resp.status_code, resp.text[:500])

        if resp.status_code == 204 or not resp.content:
            return {}

        try:
            return resp.json()
        except Exception:
            return resp.text

    # ── Numbers ───────────────────────────────────────────────────────────────

    async def list_numbers(self) -> list[dict]:
        """List all phone numbers in the workspace."""
        result = await self._request("GET", "/numbers")
        if result is None:
            return []
        return result.get("numbers", [])

    async def get_number_by_digits(self, digits: str) -> dict | None:
        """Find a number object by its E.164 digits (e.g. '+16504666677')."""
        clean = digits.replace(" ", "").replace("-", "")
        numbers = await self.list_numbers()
        for n in numbers:
            if n.get("digits", "").replace(" ", "") == clean:
                return n
        return None

    # ── Users / Agents ────────────────────────────────────────────────────────

    async def list_users(self) -> list[dict]:
        """List all agents in the workspace (uses v2)."""
        result = await self._request("GET", "/users", base=_BASE_V2)
        if result is None:
            return []
        return result.get("users", [])

    async def get_user_by_email(self, email: str) -> dict | None:
        """Find an Aircall user by their email address."""
        users = await self.list_users()
        for u in users:
            if u.get("email", "").lower() == email.lower():
                return u
        return None

    async def list_user_availabilities(self) -> list[dict]:
        """Get availability status for all agents."""
        result = await self._request("GET", "/users/availabilities")
        if result is None:
            return []
        return result.get("users", [])

    # ── Calls ─────────────────────────────────────────────────────────────────

    async def initiate_call(
        self,
        user_id: int,
        number_id: int,
        to: str,
    ) -> bool:
        """
        Programmatically initiate an outbound call from a user's Aircall app.
        The agent's phone rings first, then it calls `to`.

        Returns True on success, False on failure.
        Requires Aircall Desktop app to be running for the user.
        """
        try:
            await self._request(
                "POST",
                f"/users/{user_id}/calls",
                json={"number_id": number_id, "to": to},
            )
            logger.info("AircallClient: initiated call from user %s to %s", user_id, to)
            return True
        except AircallError as e:
            logger.warning("AircallClient: initiate_call failed: %s", e)
            return False

    async def get_call(self, call_id: int) -> dict | None:
        """Fetch full call details including recording URL."""
        return await self._request("GET", f"/calls/{call_id}")

    async def get_call_transcription(self, call_id: int, mode: str = "async") -> dict | None:
        """Fetch the transcription for a call when Aircall AI is available."""
        return await self._request(
            "GET",
            f"/calls/{call_id}/transcription",
            params={"mode": mode},
        )

    async def get_call_summary(self, call_id: int) -> dict | None:
        """Fetch the generated summary for a call when Aircall AI is available."""
        return await self._request("GET", f"/calls/{call_id}/summary")

    async def get_call_topics(self, call_id: int) -> dict | None:
        """Fetch the detected key topics for a call when Aircall AI is available."""
        return await self._request("GET", f"/calls/{call_id}/topics")

    async def get_call_action_items(self, call_id: int) -> dict | None:
        """Fetch the generated action items for a call when Aircall AI is available."""
        return await self._request("GET", f"/calls/{call_id}/action_items")

    async def get_call_sentiments(self, call_id: int) -> dict | None:
        """Fetch the detected sentiments for a call when Aircall AI is available."""
        return await self._request("GET", f"/calls/{call_id}/sentiments")

    async def add_call_note(self, call_id: int, text: str) -> dict | None:
        """Add a comment/note to a call — syncs CRM note back to Aircall."""
        return await self._request(
            "POST", f"/calls/{call_id}/comments",
            json={"content": text},
        )

    async def tag_call(self, call_id: int, tag_id: int) -> dict | None:
        """Tag a call with a given tag ID."""
        return await self._request(
            "POST", f"/calls/{call_id}/tags",
            json={"tag_id": tag_id},
        )

    async def list_recent_calls(self, limit: int = 20) -> list[dict]:
        """Fetch recent calls from the workspace."""
        result = await self._request(
            "GET", "/calls",
            params={"order": "desc", "per_page": limit},
        )
        if result is None:
            return []
        return result.get("calls", [])

    # ── Webhooks ──────────────────────────────────────────────────────────────

    async def list_webhooks(self) -> list[dict]:
        result = await self._request("GET", "/webhooks")
        if result is None:
            return []
        return result.get("webhooks", [])

    async def register_webhook(self, url: str, events: list[str]) -> dict | None:
        """Register a webhook for the given event types."""
        payload = {
            "webhook": {
                "url": url,
                "events": events,
                "active": True,
            }
        }
        result = await self._request("POST", "/webhooks", json=payload)
        if result:
            logger.info("AircallClient: registered webhook %s", url)
        return result

    async def ensure_webhook(self, url: str, events: list[str]) -> dict | None:
        """
        Idempotent webhook registration — skips if the URL is already registered.
        Safe to call on every app startup.
        """
        existing = await self.list_webhooks()
        for hook in existing:
            if hook.get("url") == url:
                logger.info("AircallClient: webhook already registered for %s", url)
                return hook
        return await self.register_webhook(url, events)

    # ── Contacts (sync CRM contacts → Aircall for screen-pop) ────────────────

    async def create_contact(
        self,
        first_name: str,
        last_name: str,
        phone: str,
        email: str = "",
        company_name: str = "",
    ) -> dict | None:
        """Create a contact in Aircall so they show by name on inbound calls."""
        payload: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "phone_numbers": [{"value": phone, "label": "work"}],
        }
        if email:
            payload["emails"] = [{"value": email, "label": "work"}]
        if company_name:
            payload["company_name"] = company_name

        return await self._request("POST", "/contacts", json=payload)
