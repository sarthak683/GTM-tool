"""
One-shot: compute chunk embeddings for any sales_resource that has none.

Run inside the web container:
    docker compose exec web python scripts/backfill_resource_embeddings.py

Safe to re-run — only touches rows where `chunks` is empty or chunks lack
embeddings. Idempotent.
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import select

from app.database import async_session
from app.models.sales_resource import SalesResource
from app.services.knowledge_context import build_chunks_for_resource

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill")


def _needs_backfill(chunks: list | None) -> bool:
    if not chunks:
        return True
    # Treat any chunk missing a non-empty embedding as needing a backfill.
    return any(not c.get("embedding") for c in chunks)


async def main() -> None:
    async with async_session() as session:
        result = await session.execute(select(SalesResource))
        resources = list(result.scalars().all())

        todo = [r for r in resources if _needs_backfill(r.chunks)]
        log.info(f"{len(todo)}/{len(resources)} resources need embedding backfill")

        for r in todo:
            log.info(f"  → embedding '{r.title}' ({len(r.content)} chars)")
            r.chunks = await build_chunks_for_resource(r.title, r.description, r.content)
            session.add(r)

        await session.commit()
        log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
