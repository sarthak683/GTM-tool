"""Event tags on prospects and accounts (e.g. "CS Summit, London").

Event names are free text from upload sheets, so they are normalized here:
whitespace collapsed, and matched case-insensitively against events that
already exist so "cs summit, london" and "CS Summit, London" never become two
filter options. Event names routinely contain commas, so several events in
one cell are separated by ";" (or "|" / newlines) — never by a comma.
"""
from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.contact import Contact

_SPLIT_RE = re.compile(r"[;|\n\r]+")
_WS_RE = re.compile(r"\s+")


def clean_event(name: str | None) -> str:
    return _WS_RE.sub(" ", (name or "").strip())


def parse_events(raw: str | None) -> list[str]:
    """Split a cell into distinct cleaned event names, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for part in _SPLIT_RE.split(raw or ""):
        event = clean_event(part)
        if event and event.lower() not in seen:
            seen.add(event.lower())
            out.append(event)
    return out


async def known_events(session: AsyncSession) -> dict[str, str]:
    """lower-case -> canonical spelling for every event already in the CRM."""
    contact_events = select(func.unnest(Contact.events).label("event"))
    company_events = select(func.unnest(Company.events).label("event"))
    rows = (await session.execute(union(contact_events, company_events))).all()
    return {clean_event(r[0]).lower(): clean_event(r[0]) for r in rows if clean_event(r[0])}


def canonicalize(events: Iterable[str], known: dict[str, str]) -> list[str]:
    """Reuse an existing spelling when one matches case-insensitively, and
    remember new spellings in `known` so later rows in the same upload agree."""
    out: list[str] = []
    for event in events:
        key = event.lower()
        canonical = known.setdefault(key, event)
        if canonical not in out:
            out.append(canonical)
    return out


def merge_events(existing: Iterable[str] | None, new: Iterable[str]) -> list[str]:
    """Existing tags plus any new ones (case-insensitive), nothing removed."""
    merged = list(existing or [])
    have = {e.lower() for e in merged}
    for event in new:
        if event.lower() not in have:
            merged.append(event)
            have.add(event.lower())
    return merged
