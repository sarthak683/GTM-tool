"""
Single source of truth for "internal" email domains.

Meetings whose attendees are all internal should be dropped at sync time
and kept off the CRM. Contacts with internal domains are excluded from
prospect candidates. Admins can edit the list via workspace settings.
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import WorkspaceSettings


def _normalize_domain(value: str | None) -> str:
    domain = (value or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


async def get_internal_domains(session: AsyncSession) -> set[str]:
    row = (
        await session.execute(select(WorkspaceSettings).where(WorkspaceSettings.id == 1))
    ).scalar_one_or_none()
    domains: set[str] = set()
    if row and isinstance(row.internal_domains, list):
        for d in row.internal_domains:
            normalized = _normalize_domain(str(d) if d is not None else "")
            if normalized:
                domains.add(normalized)
    if not domains:
        # Defensive fallback if the settings row somehow has no value.
        domains.add("beacon.li")
    return domains


def is_internal_only(attendees: Iterable[dict[str, Any]], internal_domains: set[str]) -> bool:
    """
    True iff every attendee with a usable email belongs to an internal domain.
    Meetings with *no* external attendee emails are treated as internal-only
    because there is no one on the customer side to attribute them to.
    """
    found_external = False
    found_any_email = False
    for attendee in attendees:
        email = str(attendee.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        found_any_email = True
        domain = _normalize_domain(email.split("@", 1)[1])
        if domain and domain not in internal_domains:
            found_external = True
            break
    if not found_any_email:
        return True
    return not found_external
