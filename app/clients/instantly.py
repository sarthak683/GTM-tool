"""
Instantly.ai client — v2 API.

Handles campaign creation (with sequence steps), lead management,
email/reply fetching (Unibox), and webhook registration.

API reference: https://developer.instantly.ai
Auth: Authorization: Bearer <INSTANTLY_API_KEY>

NOTE: All endpoints are under https://api.instantly.ai/api/v2
Verify exact payload shapes against the latest Instantly docs if responses
return 422 — they iterate on their API frequently.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import uuid4

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.instantly.ai/api/v2"

# ── Delay unit constants (as Instantly expects them) ──────────────────────────
DELAY_DAYS = "days"
DELAY_HOURS = "hours"
DELAY_MINUTES = "minutes"

# ── Lead label constants (mirrors Instantly UI) ───────────────────────────────
LABEL_LEAD = "Lead"
LABEL_INTERESTED = "Interested"
LABEL_MEETING_BOOKED = "Meeting Booked"
LABEL_MEETING_COMPLETE = "Meeting Complete"
LABEL_WON = "Won"
LABEL_NOT_INTERESTED = "Not Interested"
LABEL_OUT_OF_OFFICE = "Out of Office"
LABEL_WRONG_PERSON = "Wrong Person"
LABEL_LOST = "Lost"

# ── Webhook event constants ───────────────────────────────────────────────────
EVENT_EMAIL_SENT = "email_sent"
EVENT_EMAIL_OPENED = "email_opened"
EVENT_EMAIL_CLICKED = "email_link_clicked"
EVENT_EMAIL_BOUNCED = "email_bounced"
EVENT_REPLY_RECEIVED = "reply_received"
EVENT_LEAD_UNSUBSCRIBED = "lead_unsubscribed"
EVENT_CAMPAIGN_COMPLETED = "campaign_completed"
EVENT_INTERESTED = "lead_interested"
EVENT_NOT_INTERESTED = "lead_not_interested"
EVENT_MEETING_BOOKED = "lead_meeting_booked"


class InstantlyError(Exception):
    """Raised when Instantly API returns an error response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Instantly API error {status_code}: {detail}")


class InstantlyClient:
    """
    Async client for Instantly.ai v2 API.

    Usage:
        client = InstantlyClient()
        if client.is_mock:
            # API key not configured — all methods return safe defaults
            ...
        campaign = await client.create_campaign(name="Q1 Outreach", ...)
    """

    def __init__(self) -> None:
        self.api_key = settings.INSTANTLY_API_KEY
        self.is_mock = not self.api_key
        if self.is_mock:
            logger.warning("InstantlyClient: INSTANTLY_API_KEY not set — running in mock mode")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        timeout: int = 30,
    ) -> Any:
        """Execute an authenticated request to Instantly v2 API."""
        if self.is_mock:
            logger.warning("InstantlyClient mock — skipping %s %s", method, path)
            return None

        url = f"{_BASE}{path}"
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params,
            )

        if not resp.is_success:
            raise InstantlyError(resp.status_code, resp.text[:500])

        try:
            return resp.json()
        except Exception:
            return resp.text

    # ── Campaigns ─────────────────────────────────────────────────────────────

    async def create_campaign(
        self,
        *,
        name: str,
        sending_accounts: list[str],
        steps: list[dict],
        stop_on_reply: bool = True,
        track_opens: bool = True,
        track_links: bool = False,
        daily_limit: int = 35,
        min_gap_minutes: int = 9,
        random_extra_minutes: int = 5,
        stop_for_company_on_reply: bool = False,
        timezone: str = "America/Detroit",
    ) -> dict | None:
        """
        Create a campaign with sequence steps.

        steps format (one dict per email step):
        [
            {
                "subject": "Subject line",
                "body": "Email body HTML or plain text",
                "delay_value": 0,
                "delay_unit": "Days",   # Days | Hours | Minutes
                "variants": []          # Optional A/B variants
            },
            ...
        ]

        Instantly sequence payload wraps steps in a sequences array.
        Each step can have multiple variants for A/B testing.
        """
        # Build Instantly sequence steps format
        sequence_steps = []
        for i, step in enumerate(steps):
            # Primary variant is always included
            variants = [{"subject": step["subject"], "body": step["body"]}]
            # Append any additional A/B variants
            for variant in step.get("variants", []):
                variants.append({"subject": variant.get("subject", step["subject"]), "body": variant["body"]})

            step_payload: dict[str, Any] = {
                "type": "email",
                "delay": step.get("delay_value", 0 if i == 0 else 3),
                "variants": variants,
            }
            # Only include delay_unit if explicitly provided (Instantly defaults to days)
            if "delay_unit" in step:
                step_payload["delay_unit"] = step["delay_unit"]
            sequence_steps.append(step_payload)

        payload = {
            "name": name,
            "email_list": sending_accounts,
            "sequences": [{"steps": sequence_steps}],
            "stop_on_reply": stop_on_reply,
            "open_tracking": track_opens,
            "link_tracking": track_links,
            "daily_limit": daily_limit,
            "daily_max_leads": daily_limit,
            "email_gap": min_gap_minutes,
            "random_wait_max": random_extra_minutes,
            "stop_for_company": stop_for_company_on_reply,
            "campaign_schedule": {
                "schedules": [
                    {
                        "name": "Default",
                        "timezone": timezone,
                        "days": {
                            "0": True,   # Monday
                            "1": True,   # Tuesday
                            "2": True,   # Wednesday
                            "3": True,   # Thursday
                            "4": True,   # Friday
                            "5": False,  # Saturday
                            "6": False,  # Sunday
                        },
                        "timing": {
                            "from": "09:00",
                            "to": "17:00",
                        },
                    }
                ],
            },
        }

        if self.is_mock:
            campaign_id = f"mock-campaign-{uuid4()}"
            logger.warning("InstantlyClient mock — created campaign '%s' id=%s", name, campaign_id)
            return {"id": campaign_id, "name": name, "status": 0, **payload}

        result = await self._request("POST", "/campaigns", json=payload)
        if result:
            logger.info("InstantlyClient: created campaign '%s' id=%s", name, result.get("id"))
        return result

    async def get_campaign(self, campaign_id: str) -> dict | None:
        """Get campaign details and analytics."""
        if self.is_mock:
            return {"id": campaign_id, "status": 1}
        return await self._request("GET", f"/campaigns/{campaign_id}")

    async def list_campaigns(self, limit: int = 100) -> list[dict]:
        """List all workspace campaigns."""
        result = await self._request("GET", "/campaigns", params={"limit": limit})
        if result is None:
            return []
        return result if isinstance(result, list) else result.get("items", [])

    async def activate_campaign(self, campaign_id: str) -> dict | None:
        """Activate (launch) a campaign."""
        if self.is_mock:
            logger.warning("InstantlyClient mock — activated campaign %s", campaign_id)
            return {"id": campaign_id, "status": 1}
        result = await self._request("POST", f"/campaigns/{campaign_id}/activate", json={})
        if result:
            logger.info("InstantlyClient: activated campaign %s", campaign_id)
        return result

    async def pause_campaign(self, campaign_id: str) -> dict | None:
        """Pause a running campaign."""
        if self.is_mock:
            logger.warning("InstantlyClient mock — paused campaign %s", campaign_id)
            return {"id": campaign_id, "status": 2}
        return await self._request("POST", f"/campaigns/{campaign_id}/pause", json={})

    # ── Leads ─────────────────────────────────────────────────────────────────

    async def add_lead(
        self,
        *,
        campaign_id: str,
        email: str,
        first_name: str = "",
        last_name: str = "",
        company_name: str = "",
        job_title: str = "",
        linkedin_url: str = "",
        custom_variables: dict | None = None,
    ) -> dict | None:
        """
        Add a single lead to a campaign.

        custom_variables: any extra key-value pairs that map to {{variableName}}
        template tags in your email steps.
        """
        payload: dict[str, Any] = {
            "campaign": campaign_id,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company_name": company_name,
        }

        # Instantly v2: personalization is a flat string, not a nested object
        # Custom variables are passed as top-level keys
        if job_title:
            payload["job_title"] = job_title
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url
        if custom_variables:
            payload["custom_variables"] = custom_variables

        if self.is_mock:
            logger.warning("InstantlyClient mock — added lead %s to campaign %s", email, campaign_id)
            return {"id": f"mock-lead-{uuid4()}", **payload}

        result = await self._request("POST", "/leads", json=payload)
        if result:
            logger.info("InstantlyClient: added lead %s to campaign %s", email, campaign_id)
        return result

    async def add_leads_bulk(
        self,
        *,
        campaign_id: Optional[str] = None,
        list_id: Optional[str] = None,
        leads: list[dict],
    ) -> dict | None:
        """
        Add up to 1000 leads in bulk to a campaign or list.
        
        Each lead dict should have keys: email, first_name, last_name, company_name,
        job_title, linkedin_url, and any custom_variables.
        
        Requires campaign_id XOR list_id (not both).
        """
        payload: dict[str, Any] = {"leads": leads}
        if campaign_id:
            payload["campaign_id"] = campaign_id
        if list_id:
            payload["list_id"] = list_id

        if self.is_mock:
            logger.warning("InstantlyClient mock — bulk added %d leads", len(leads))
            return {
                "status": "success",
                "total_sent": len(leads),
                "leads_uploaded": len(leads),
                "created_leads": [
                    {"id": f"mock-lead-{uuid4()}", "email": lead.get("email"), "index": i}
                    for i, lead in enumerate(leads)
                ],
            }

        result = await self._request("POST", "/leads/add", json=payload)
        if result:
            logger.info("InstantlyClient: bulk added %d leads", len(leads))
        return result

    async def get_lead(self, email: str, campaign_id: str) -> dict | None:
        """Get a lead's status within a campaign."""
        return await self._request(
            "GET", "/leads",
            params={"email": email, "campaign_id": campaign_id, "limit": 1}
        )

    async def update_lead_label(
        self, email: str, campaign_id: str, label: str
    ) -> dict | None:
        """Update a lead's status label (e.g., Interested, Meeting Booked)."""
        return await self._request(
            "PATCH", "/leads",
            json={"email": email, "campaign_id": campaign_id, "label": label},
        )

    # ── Unibox / Emails ───────────────────────────────────────────────────────

    async def list_emails(
        self,
        *,
        campaign_id: Optional[str] = None,
        lead_email: Optional[str] = None,
        email_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Fetch emails from Instantly Unibox.
        Filter by campaign or specific lead email.
        """
        params: dict[str, Any] = {"limit": limit}
        if campaign_id:
            params["campaign_id"] = campaign_id
        if lead_email:
            params["lead"] = lead_email
        if email_type:
            params["email_type"] = email_type

        result = await self._request("GET", "/emails", params=params)
        if result is None:
            return []
        return result if isinstance(result, list) else result.get("items", [])

    async def get_email(self, email_id: str) -> dict | None:
        """Get a specific email/thread by ID."""
        return await self._request("GET", f"/emails/{email_id}")

    async def get_reply_thread(self, lead_email: str, campaign_id: str) -> list[dict]:
        """Fetch inbound reply emails for a specific lead in a campaign."""
        rows = await self.list_emails(
            campaign_id=campaign_id,
            lead_email=lead_email,
            email_type="received",
            limit=50,
        )
        lead = (lead_email or "").strip().lower()
        replies: list[dict] = []
        for row in rows:
            row_lead = str(row.get("lead") or "").strip().lower()
            if lead and row_lead and row_lead != lead:
                continue
            # Instantly ue_type: 1 campaign sent, 2 received, 3 sent, 4 scheduled.
            if row.get("ue_type") not in (None, 2):
                continue
            body = row.get("body") if isinstance(row.get("body"), dict) else {}
            replies.append({
                "id": row.get("id"),
                "subject": row.get("subject"),
                "body": body.get("text") or body.get("html") or row.get("content_preview") or "",
                "html_body": body.get("html"),
                "from_email": row.get("from_address_email"),
                "to_email": row.get("to_address_email_list"),
                "lead_email": row.get("lead"),
                "created_at": row.get("timestamp_email") or row.get("timestamp_created"),
                "timestamp": row.get("timestamp_email") or row.get("timestamp_created"),
                "thread_id": row.get("thread_id"),
                "is_auto_reply": bool(row.get("is_auto_reply")),
                "ue_type": row.get("ue_type"),
            })
        return replies

    # ── Webhooks ──────────────────────────────────────────────────────────────

    async def register_webhook(
        self,
        url: str,
        event_types: list[str],
        *,
        secret_header: Optional[str] = None,
    ) -> dict | None:
        """
        Register webhook URLs for specific event types.
        Instantly v2 requires one event_type per webhook registration.
        This method registers a webhook for each event type and returns
        the result of the last registration.
        """
        last_result = None
        for event_type in event_types:
            payload: dict[str, Any] = {
                "target_hook_url": url,
                "event_type": event_type,
            }
            if secret_header:
                payload["add_header"] = True
                payload["header_value"] = secret_header

            result = await self._request("POST", "/webhooks", json=payload)
            if result:
                logger.info("InstantlyClient: registered webhook %s for event %s", url, event_type)
                last_result = result
        return last_result

    async def list_webhooks(self) -> list[dict]:
        """List all registered workspace webhooks."""
        result = await self._request("GET", "/webhooks")
        if result is None:
            return []
        return result if isinstance(result, list) else result.get("items", [])

    async def delete_webhook(self, webhook_id: str) -> bool:
        """Remove a registered webhook."""
        result = await self._request("DELETE", f"/webhooks/{webhook_id}")
        return result is not None

    async def ensure_webhook(self, url: str, event_types: list[str]) -> dict | None:
        """
        Idempotent webhook registration — checks each event type individually
        to avoid re-registering while ensuring all requested event types are covered.
        """
        existing = await self.list_webhooks()
        existing_events: set[str] = set()
        for hook in existing:
            stored_url = hook.get("target_hook_url") or hook.get("webhook_url") or hook.get("url") or ""
            if stored_url == url:
                stored_event = hook.get("event_type") or hook.get("event_types") or ""
                if isinstance(stored_event, list):
                    existing_events.update(stored_event)
                elif isinstance(stored_event, str):
                    existing_events.add(stored_event)

        missing = [e for e in event_types if e not in existing_events]
        if not missing:
            logger.info("InstantlyClient: all %d event types already registered for %s", len(event_types), url)
            return existing[0] if existing else None

        logger.info("InstantlyClient: registering %d missing event types for %s", len(missing), url)
        return await self.register_webhook(url, missing)

    # ── Leads (bulk listing) ───────────────────────────────────────────────────

    async def list_leads(
        self,
        *,
        campaign_id: Optional[str] = None,
        limit: int = 100,
        starting_after: Optional[str] = None,
        filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict | None:
        """
        List leads in a campaign or workspace (POST endpoint per Instantly v2).

        Returns: {"items": [...leads...], "next_starting_after": "cursor"} or None
        """
        payload: dict[str, Any] = {"limit": limit}
        if campaign_id:
            payload["campaign"] = campaign_id
        if starting_after:
            payload["starting_after"] = starting_after
        if filter:
            payload["filter"] = filter
        if search:
            payload["search"] = search

        result = await self._request("POST", "/leads/list", json=payload)
        if result is None:
            return None
        return result

    # ── Campaign analytics ─────────────────────────────────────────────────────

    async def get_campaign_analytics(
        self,
        *,
        campaign_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict] | None:
        """
        Get analytics for one or all campaigns.
        Specify campaign_id for a single campaign, or leave empty for all.
        """
        params: dict[str, Any] = {}
        if campaign_id:
            params["id"] = campaign_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        result = await self._request("GET", "/campaigns/analytics", params=params)
        if result is None:
            return None
        return result if isinstance(result, list) else [result]

    async def search_campaigns_by_lead_email(
        self, email: str
    ) -> list[dict]:
        """
        Search for campaigns that contain a specific lead email.
        Useful for finding already-running campaigns to link to CRM contacts.
        """
        result = await self._request(
            "GET", "/campaigns/search-by-contact",
            params={"search": email},
        )
        if result is None:
            return []
        return result if isinstance(result, list) else result.get("items", [])
