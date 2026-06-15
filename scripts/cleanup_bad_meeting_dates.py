"""
Find and optionally clear malformed meeting scheduled_at values.

Usage:
  python scripts/cleanup_bad_meeting_dates.py
  python scripts/cleanup_bad_meeting_dates.py --commit

This is intentionally conservative. On Postgres, scheduled_at should already be
a timestamp column, so malformed free text should be impossible. The script is
still useful against older/staging data or accidental text-cast imports because
it reports suspicious rows and clears only values that cannot be interpreted as
a real timestamp.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from sqlalchemy import text

from app.database import AsyncSessionLocal


BAD_DATE_PATTERNS = (
    "tl;dv",
    "tldv",
    "recording",
    "transcript",
    "download",
)


def looks_bad(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if any(pattern in lowered for pattern in BAD_DATE_PATTERNS):
        return True
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return False
    except ValueError:
        return True


async def main(commit: bool) -> None:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    """
                    select id, title, scheduled_at::text as scheduled_at_text
                    from meetings
                    where scheduled_at is not null
                    order by updated_at desc
                    """
                )
            )
        ).mappings().all()

        bad_rows = [row for row in rows if looks_bad(row["scheduled_at_text"])]
        if not bad_rows:
            print("No malformed meeting scheduled_at values found.")
            return

        print(f"Found {len(bad_rows)} malformed scheduled_at value(s):")
        for row in bad_rows[:50]:
            print(f"- {row['id']} | {row['title']} | {row['scheduled_at_text']}")
        if len(bad_rows) > 50:
            print(f"...and {len(bad_rows) - 50} more")

        if not commit:
            print("Dry run only. Re-run with --commit to clear these dates.")
            return

        for row in bad_rows:
            await session.execute(
                text("update meetings set scheduled_at = null, updated_at = now() where id = :id"),
                {"id": str(row["id"])},
            )
        await session.commit()
        print(f"Cleared scheduled_at for {len(bad_rows)} meeting(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Actually clear malformed scheduled_at values.")
    args = parser.parse_args()
    asyncio.run(main(commit=args.commit))
