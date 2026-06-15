"""
Apollo.io client — company enrichment and contact search.

Real mode: uses Hunter's Company Enrichment API (same key) when
APOLLO_API_KEY is empty but HUNTER_API_KEY is set.
Mock mode: returns Faker data when both keys are empty.
"""
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _parse_hunter_size(size_str: Optional[str]) -> Optional[int]:
    """
    Convert Hunter's employee-range string to an integer (upper bound).

    Examples: "51-200" → 200, "1001-5000" → 5000, "10001+" → 10001
    Returning the upper bound gives companies the benefit of the doubt in ICP scoring.
    """
    if not size_str:
        return None
    if "+" in size_str:
        try:
            return int(size_str.replace("+", "").strip())
        except ValueError:
            return None
    parts = size_str.split("-")
    if len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    try:
        return int(size_str)
    except ValueError:
        return None


class ApolloClient:
    def __init__(self) -> None:
        self.api_key = settings.APOLLO_API_KEY
        self.hunter_key = settings.HUNTER_API_KEY
        # Use Hunter as real source if Apollo key is absent but Hunter key exists
        self.mock = not self.api_key and not self.hunter_key

    async def enrich_company(self, domain: str) -> Optional[dict]:
        """Return firmographic data for a domain."""
        if self.mock:
            return None

        if self.hunter_key and not self.api_key:
            return await self._enrich_via_hunter(domain)

        # Real Apollo call (when APOLLO_API_KEY is set)
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(
                    "https://api.apollo.io/v1/organizations/enrich",
                    headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
                    json={"api_key": self.api_key, "domain": domain},
                )
                if resp.status_code == 422:
                    logger.warning("Apollo organizations/enrich 422 for %s: %s", domain, resp.text[:300])
                    return None
                resp.raise_for_status()
                org = resp.json().get("organization", {})
                if not org:
                    logger.info("Apollo returned empty organization for %s", domain)
                    return None
                return {
                    "name": org.get("name"),
                    "industry": org.get("industry"),
                    "employee_count": org.get("estimated_num_employees"),
                    "funding_stage": org.get("latest_funding_stage"),
                    "has_dap": False,
                    "dap_tool": None,
                }
            except httpx.HTTPStatusError as exc:
                logger.warning("Apollo organizations/enrich %s for %s: %s", exc.response.status_code, domain, exc.response.text[:300])
                return None
            except httpx.TimeoutException:
                logger.warning("Apollo organizations/enrich timed out for %s", domain)
                return None

    async def find_contacts(self, domain: str, limit: int = 5) -> list:
        """Return a list of contacts at the domain."""
        if self.mock:
            return []
        return []

    async def search_people(
        self,
        domain: str,
        limit: int = 5,
        titles: list[str] | None = None,
        seniorities: list[str] | None = None,
    ) -> list[dict]:
        """
        Search for contacts at a domain using Apollo people search endpoints.

        Tries endpoints in order of preference, falling back if one returns 403:
          1. mixed_people/search (best, searches global DB)
          2. people/search (alternative global search)
          3. contacts/search (searches saved Apollo CRM contacts only)

        Credit-conservative: limits results and uses targeted filters.
        """
        if self.mock:
            return []

        if not self.api_key:
            return []

        import httpx
        import logging
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

        # ── Attempt 1: mixed_people/search ──────────────────────────────────
        body: dict = {
            "api_key": self.api_key,
            "q_organization_domains": domain,
            "page": 1,
            "per_page": min(limit, 25),
        }
        if titles:
            body["person_titles"] = titles
        if seniorities:
            body["person_seniorities"] = seniorities

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    "https://api.apollo.io/v1/mixed_people/search",
                    headers=headers, json=body,
                )
                if resp.status_code == 403:
                    logger.info("mixed_people/search not enabled, trying people/search")
                else:
                    resp.raise_for_status()
                    people = resp.json().get("people", [])
                    return self._normalize_people(people)
            except httpx.HTTPStatusError:
                logger.info("mixed_people/search failed, trying fallback")

            # ── Attempt 2: people/search ────────────────────────────────────
            try:
                resp = await client.post(
                    "https://api.apollo.io/v1/people/search",
                    headers=headers, json=body,
                )
                if resp.status_code == 403:
                    logger.info("people/search not enabled, trying contacts/search")
                else:
                    resp.raise_for_status()
                    people = resp.json().get("people", [])
                    return self._normalize_people(people)
            except httpx.HTTPStatusError:
                logger.info("people/search failed, trying contacts/search")

            # ── Attempt 3: contacts/search (saved CRM contacts) ─────────────
            try:
                contacts_body: dict = {
                    "api_key": self.api_key,
                    "q_organization_domains": domain,
                    "page": 1,
                    "per_page": min(limit, 25),
                }
                resp = await client.post(
                    "https://api.apollo.io/v1/contacts/search",
                    headers=headers, json=contacts_body,
                )
                if resp.status_code == 403:
                    logger.warning("No Apollo people search endpoint is enabled")
                    return []
                resp.raise_for_status()
                contacts = resp.json().get("contacts", [])
                return self._normalize_people(contacts)
            except httpx.HTTPStatusError:
                logger.warning("All Apollo people search endpoints failed")
                return []

    def _normalize_people(self, people: list) -> list[dict]:
        """Normalize Apollo people/contacts response into a standard format."""
        return [
            {
                "first_name": p.get("first_name") or "",
                "last_name": p.get("last_name") or "",
                "email": p.get("email") or None,
                "title": p.get("title") or None,
                "seniority": p.get("seniority") or None,
                "linkedin_url": p.get("linkedin_url") or None,
                "phone": p.get("phone_numbers", [{}])[0].get("sanitized_number") if p.get("phone_numbers") else None,
                "organization_name": p.get("organization", {}).get("name") if p.get("organization") else None,
            }
            for p in people
        ]

    async def enrich_person(self, email: str = "", first_name: str = "", last_name: str = "", domain: str = "") -> dict | None:
        """
        Single-contact enrichment via Apollo people/match.
        Used by re-enrich for individual contacts.
        """
        if self.mock:
            return None

        if not self.api_key:
            return None

        import httpx

        body: dict = {"api_key": self.api_key}
        if email:
            body["email"] = email
        if first_name:
            body["first_name"] = first_name
        if last_name:
            body["last_name"] = last_name
        if domain:
            body["organization_domain"] = domain

        person = None
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    "https://api.apollo.io/v1/people/match",
                    headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
                    json=body,
                )
                if resp.status_code == 404:
                    person = None
                else:
                    resp.raise_for_status()
                    person = resp.json().get("person")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 422:
                    logger.info("Apollo people/match returned 422 for %s; falling back to people search", email or f"{first_name} {last_name}".strip())
                else:
                    raise

        result = {
            "first_name": person.get("first_name", "") if person else first_name,
            "last_name": person.get("last_name", "") if person else last_name,
            "email": person.get("email") if person else (email or None),
            "title": person.get("title") if person else None,
            "seniority": person.get("seniority") if person else None,
            "linkedin_url": person.get("linkedin_url") if person else None,
            "phone": person.get("phone_numbers", [{}])[0].get("sanitized_number") if person and person.get("phone_numbers") else None,
            "headline": person.get("headline") if person else None,
            "employment_history": person.get("employment_history", [])[:3] if person else [],
        }

        if result.get("phone"):
            return result

        fallback = await self._find_person_phone_via_search(
            domain=domain,
            email=email or result.get("email") or "",
            first_name=first_name or result.get("first_name") or "",
            last_name=last_name or result.get("last_name") or "",
            title=result.get("title") or "",
            seniority=result.get("seniority") or "",
            linkedin_url=result.get("linkedin_url") or "",
        )
        if fallback:
            result["phone"] = fallback.get("phone") or result.get("phone")
            result["email"] = result.get("email") or fallback.get("email")
            result["title"] = result.get("title") or fallback.get("title")
            result["seniority"] = result.get("seniority") or fallback.get("seniority")
            result["linkedin_url"] = result.get("linkedin_url") or fallback.get("linkedin_url")

        return result if any(result.values()) else None

    async def _find_person_phone_via_search(
        self,
        *,
        domain: str,
        email: str,
        first_name: str,
        last_name: str,
        title: str,
        seniority: str,
        linkedin_url: str,
    ) -> dict | None:
        """
        Apollo people/match often returns the right person but omits phone.
        Fall back to people search for the same domain and choose the best hit.
        """
        if not domain or not self.api_key:
            return None

        narrowed = await self.search_people(
            domain=domain,
            limit=25,
            titles=[title] if title else None,
            seniorities=[seniority] if seniority else None,
        )
        best = self._pick_best_person_match(
            narrowed,
            email=email,
            first_name=first_name,
            last_name=last_name,
            linkedin_url=linkedin_url,
        )
        if best and best.get("phone"):
            return best

        broader = await self.search_people(domain=domain, limit=25)
        return self._pick_best_person_match(
            broader,
            email=email,
            first_name=first_name,
            last_name=last_name,
            linkedin_url=linkedin_url,
        )

    def _pick_best_person_match(
        self,
        people: list[dict],
        *,
        email: str,
        first_name: str,
        last_name: str,
        linkedin_url: str,
    ) -> dict | None:
        normalized_email = (email or "").strip().lower()
        normalized_first = (first_name or "").strip().lower()
        normalized_last = (last_name or "").strip().lower()
        normalized_linkedin = (linkedin_url or "").strip().lower()

        for person in people:
            if normalized_email and (person.get("email") or "").strip().lower() == normalized_email:
                return person

        for person in people:
            if normalized_linkedin and (person.get("linkedin_url") or "").strip().lower() == normalized_linkedin:
                return person

        for person in people:
            if (
                normalized_first
                and normalized_last
                and (person.get("first_name") or "").strip().lower() == normalized_first
                and (person.get("last_name") or "").strip().lower() == normalized_last
            ):
                return person

        return None

    # ── Hunter fallback ────────────────────────────────────────────────────────

    async def _enrich_via_hunter(self, domain: str) -> Optional[dict]:
        """Call Hunter's /companies/find endpoint for firmographics."""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/companies/find",
                params={"domain": domain, "api_key": self.hunter_key},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json().get("data", {})

        if not data:
            return None

        return {
            "name": data.get("name"),
            "industry": data.get("industry"),
            "vertical": None,
            "employee_count": _parse_hunter_size(data.get("size")),
            "arr_estimate": None,
            "funding_stage": data.get("funding_stage"),
            "has_dap": False,
            "dap_tool": None,
        }
