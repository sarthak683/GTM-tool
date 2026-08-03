#!/usr/bin/env python3
"""Safely backfill and repair contact timezones from prospect evidence.

The default mode is a read-only dry-run. Missing values are filled only when
the prospect's location or phone produces a timezone. Existing values are repaired
only with ``--repair-mismatches`` and only when all of these are true:

* the phone is international and not NANP (+1), whose owners may relocate;
* the existing and inferred zones have materially different UTC/DST profiles;
* the import did not contain an explicit timezone; and
* a multi-zone Australian number is not replacing an existing Australia zone.

Commits require both an exact dry-run count and a JSON backup path. This makes
the operation fail closed if data changes between review and execution.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.database import AsyncSessionLocal
from app.models.contact import Contact
from app.models.user import User
from app.services.timezone_infer import (
    infer_timezone_from_location,
    infer_timezone_from_phone,
)


_ALIASES = {
    "EST": "America/New_York",
    "CST": "America/Chicago",
    "MST": "America/Denver",
    "PST": "America/Los_Angeles",
    "GMT": "Europe/London",
    "CET": "Europe/Berlin",
    "EET": "Europe/Athens",
    "IST": "Asia/Kolkata",
    "GST": "Asia/Dubai",
    "SGT": "Asia/Singapore",
    "JST": "Asia/Tokyo",
    "AEST": "Australia/Sydney",
}


@dataclass(frozen=True)
class TimezoneChange:
    contact_id: str
    owner: str
    phone: str
    before: str | None
    after: str
    reason: str


def _canonical_timezone(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return _ALIASES.get(cleaned, cleaned) or None


def _offset_profile(zone: str) -> tuple[int | None, ...] | None:
    try:
        tz = ZoneInfo(zone)
    except Exception:
        return None
    offsets: list[int | None] = []
    current_year = datetime.now(timezone.utc).year
    for year in (current_year, current_year + 1):
        for month in range(1, 13):
            stamp = datetime(year, month, 15, 12, tzinfo=timezone.utc)
            offset = stamp.astimezone(tz).utcoffset()
            offsets.append(int(offset.total_seconds()) if offset is not None else None)
    return tuple(offsets)


def _equivalent_calling_zone(left: str, right: str) -> bool:
    if left == right:
        return True
    left_profile = _offset_profile(left)
    right_profile = _offset_profile(right)
    return left_profile is not None and left_profile == right_profile


def _normalized_phone(phone: str | None) -> str:
    return re.sub(r"[^\d+]", "", (phone or "").strip())


def _has_explicit_uploaded_timezone(contact: Contact) -> bool:
    data = contact.enrichment_data
    if not isinstance(data, dict):
        return False
    raw_row = data.get("raw_row")
    if not isinstance(raw_row, dict):
        return False
    for key, value in raw_row.items():
        normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
        if normalized_key in {"timezone", "contacttimezone"} and str(value or "").strip():
            return True
    return False


def _prospect_location(contact: Contact) -> str | None:
    data = contact.enrichment_data
    if not isinstance(data, dict):
        return None

    workbook = data.get("workbook")
    if isinstance(workbook, dict) and str(workbook.get("location") or "").strip():
        return str(workbook["location"]).strip()

    raw_row = data.get("raw_row")
    if not isinstance(raw_row, dict):
        return None

    normalized = {
        re.sub(r"[^a-z]", "", str(key).lower()): value
        for key, value in raw_row.items()
        if str(value or "").strip()
    }
    for key in ("contactlocation", "location"):
        if key in normalized:
            return str(normalized[key]).strip()

    parts = [normalized.get(key) for key in ("city", "state", "country")]
    joined = ", ".join(str(part).strip() for part in parts if part)
    return joined or None


def propose_timezone_change(
    contact: Contact,
    *,
    owner: str = "Unassigned",
    repair_mismatches: bool = False,
    include_nanp_mismatches: bool = False,
) -> tuple[TimezoneChange | None, str | None]:
    location = _prospect_location(contact)
    inferred = infer_timezone_from_location(location) or infer_timezone_from_phone(contact.phone)
    if not inferred:
        return None, "no_location_or_phone_timezone"

    current = _canonical_timezone(contact.timezone)
    if current and _equivalent_calling_zone(current, inferred):
        return None, "already_equivalent"

    if _has_explicit_uploaded_timezone(contact):
        return None, "explicit_upload_timezone"

    phone = _normalized_phone(contact.phone)
    if current:
        if not repair_mismatches:
            return None, "mismatch_repair_disabled"
        if phone.startswith("+1") and not include_nanp_mismatches:
            return None, "nanp_mismatch_review_required"
        if phone.startswith("+61") and current.startswith("Australia/"):
            return None, "australia_zone_already_specific"

    return TimezoneChange(
        contact_id=str(contact.id),
        owner=owner,
        phone=contact.phone or "",
        before=contact.timezone,
        after=inferred,
        reason="phone_conflict" if current else "phone_missing",
    ), None


async def run(
    *,
    commit: bool,
    repair_mismatches: bool,
    include_nanp_mismatches: bool,
    owner_email: str | None,
    expected_count: int | None,
    backup_json: str | None,
) -> int:
    async with AsyncSessionLocal() as session:
        owner_id: UUID | None = None
        if owner_email:
            owner_row = (
                await session.execute(
                    select(User).where(User.email.ilike(owner_email.strip()))
                )
            ).scalar_one_or_none()
            if owner_row is None:
                raise RuntimeError(f"No user found for owner email {owner_email!r}")
            owner_id = owner_row.id

        users = (await session.execute(select(User))).scalars().all()
        user_names = {user.id: user.name for user in users}

        statement = select(Contact)
        if owner_id:
            statement = statement.where(
                or_(Contact.sdr_id == owner_id, Contact.assigned_to_id == owner_id)
            )
        contacts = (await session.execute(statement)).scalars().all()

        changes: list[TimezoneChange] = []
        skipped: Counter[str] = Counter()
        for contact in contacts:
            owner = user_names.get(contact.sdr_id or contact.assigned_to_id, "Unassigned")
            change, skip_reason = propose_timezone_change(
                contact,
                owner=owner,
                repair_mismatches=repair_mismatches,
                include_nanp_mismatches=include_nanp_mismatches,
            )
            if change:
                changes.append(change)
            elif skip_reason:
                skipped[skip_reason] += 1

        reason_counts = Counter(change.reason for change in changes)
        owner_counts = Counter(change.owner for change in changes)

        print(f"Scanned: {len(contacts)} contacts")
        print(f"Proposed changes: {len(changes)}")
        for reason, count in reason_counts.most_common():
            print(f"  {reason:<24} {count}")
        print("Affected owners:")
        for owner, count in owner_counts.most_common():
            print(f"  {owner:<32} {count}")
        print("Skipped safeguards:")
        for reason, count in skipped.most_common():
            print(f"  {reason:<32} {count}")
        if changes:
            print("Sample changes:")
            for change in changes[:25]:
                print(
                    f"  {change.contact_id}  {change.owner:<24} "
                    f"{change.before or '(missing)'} -> {change.after}"
                )

        if not commit:
            print("DRY-RUN - no changes written.")
            return 0

        if expected_count is None:
            raise RuntimeError("--expected-count is required with --commit")
        if expected_count != len(changes):
            raise RuntimeError(
                f"Expected {expected_count} changes, but dry-run now proposes {len(changes)}; aborting"
            )
        if not backup_json:
            raise RuntimeError("--backup-json is required with --commit")

        change_by_id = {UUID(change.contact_id): change for change in changes}
        locked = (
            await session.execute(
                select(Contact)
                .where(Contact.id.in_(change_by_id))
                .with_for_update()
            )
        ).scalars().all()
        if len(locked) != len(changes):
            raise RuntimeError("A proposed contact disappeared before locking; aborting")

        for contact in locked:
            change = change_by_id[contact.id]
            if contact.timezone != change.before:
                raise RuntimeError(
                    f"Contact {contact.id} changed since dry-run; aborting without writes"
                )

        backup = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "owner_email": owner_email,
            "repair_mismatches": repair_mismatches,
            "include_nanp_mismatches": include_nanp_mismatches,
            "change_count": len(changes),
            "changes": [asdict(change) for change in changes],
        }
        backup_path = Path(backup_json)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(backup, indent=2, sort_keys=True), encoding="utf-8")

        for contact in locked:
            contact.timezone = change_by_id[contact.id].after
            session.add(contact)
        await session.commit()
        print(f"Committed {len(changes)} timezone updates.")
        print(f"Backup: {backup_path}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--repair-mismatches", action="store_true")
    parser.add_argument(
        "--include-nanp-mismatches",
        action="store_true",
        help="Also repair existing +1 timezone conflicts; off by default for safety.",
    )
    parser.add_argument("--owner-email")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--backup-json")
    args = parser.parse_args()
    return asyncio.run(
        run(
            commit=args.commit,
            repair_mismatches=args.repair_mismatches,
            include_nanp_mismatches=args.include_nanp_mismatches,
            owner_email=args.owner_email,
            expected_count=args.expected_count,
            backup_json=args.backup_json,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
