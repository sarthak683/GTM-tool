"""
Hunter.io client — email pattern and company enrichment data.

Real mode: calls Hunter API v2 when HUNTER_API_KEY is set.
Mock mode: returns Faker data when key is empty.
"""
import random
from typing import Optional

import httpx

from app.config import settings

_BASE = "https://api.hunter.io/v2"


class HunterClient:
    def __init__(self) -> None:
        self.api_key = settings.HUNTER_API_KEY
        self.mock = not self.api_key

    async def domain_search(self, domain: str) -> Optional[dict]:
        """Return email pattern, contacts, and deliverability signals for a domain."""
        if self.mock:
            return None

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BASE}/domain-search",
                params={"domain": domain, "api_key": self.api_key, "limit": 10},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})

            # Return full contact objects so the orchestrator can create Contact rows
            contacts = [
                {
                    "email": e.get("value"),
                    "first_name": e.get("first_name") or "",
                    "last_name": e.get("last_name") or "",
                    "title": e.get("position"),
                    "linkedin_url": e.get("linkedin"),
                    "confidence": e.get("confidence", 0),
                }
                for e in data.get("emails", [])
                if e.get("value")
            ]

            return {
                "domain": domain,
                "pattern": data.get("pattern", ""),
                "emails_found": data.get("emails_count", 0),
                "contacts": contacts,
                "organization": data.get("organization", ""),
            }

    async def domain_search_rich(self, domain: str) -> Optional[dict]:
        """Like domain_search but includes seniority, department, and phone for each contact."""
        if self.mock:
            return None

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BASE}/domain-search",
                params={"domain": domain, "api_key": self.api_key, "limit": 10},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})

            contacts = [
                {
                    "email": e.get("value"),
                    "first_name": e.get("first_name") or "",
                    "last_name": e.get("last_name") or "",
                    "title": e.get("position"),
                    "seniority": e.get("seniority"),
                    "department": e.get("department"),
                    "linkedin_url": e.get("linkedin"),
                    "phone_number": e.get("phone_number"),
                    "confidence": e.get("confidence", 0),
                }
                for e in data.get("emails", [])
                if e.get("value")
            ]

            return {
                "domain": domain,
                "pattern": data.get("pattern", ""),
                "emails_found": data.get("emails_count", 0),
                "contacts": contacts,
                "organization": data.get("organization", ""),
            }

    async def company_enrichment(self, domain: str) -> Optional[dict]:
        """Return firmographic data for a domain using Hunter's company endpoint."""
        if self.mock:
            return None

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BASE}/companies/find",
                params={"domain": domain, "api_key": self.api_key},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("data", {})

    async def verify_email(self, email: str) -> Optional[dict]:
        """Check if an email address is deliverable."""
        if self.mock:
            return {"email": email, "result": "deliverable", "score": random.randint(70, 99)}

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BASE}/email-verifier",
                params={"email": email, "api_key": self.api_key},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "email": email,
                "result": data.get("result", "unknown"),
                "score": data.get("score", 0),
            }

