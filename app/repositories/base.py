"""
Generic async CRUD repository.

Every model-specific repository subclasses BaseRepository[ModelT] and gets
get / get_or_raise / list / list_paginated / count / create / update /
save / delete for free.  Specialised queries (JOINs, cascade deletes, etc.)
live in the subclass.

Note: `from __future__ import annotations` is required because the class
defines a method named `list`, which shadows the builtin `list` during class
body execution. Without deferred annotations, `tuple[list[ModelT], int]` in
`list_paginated` would resolve `list` to the method object and crash at import.
"""
from __future__ import annotations

from typing import Generic, Optional, Type, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

ModelT = TypeVar("ModelT", bound=SQLModel)


class BaseRepository(Generic[ModelT]):
    def __init__(self, model: Type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    # ── single-record reads ──────────────────────────────────────────────────

    async def get(self, id: UUID) -> Optional[ModelT]:
        return await self.session.get(self.model, id)

    async def get_or_raise(self, id: UUID) -> ModelT:
        obj = await self.get(id)
        if obj is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError(f"{self.model.__name__} {id} not found")
        return obj

    # ── list / count ─────────────────────────────────────────────────────────

    async def count(self, *filters) -> int:
        stmt = select(func.count()).select_from(self.model)
        for f in filters:
            stmt = stmt.where(f)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list(
        self,
        *filters,
        skip: int = 0,
        limit: int = 50,
        order_by=None,
    ) -> list[ModelT]:
        stmt = select(self.model)
        for f in filters:
            stmt = stmt.where(f)
        if order_by is not None:
            # Accept a tuple/list of clauses so callers can append a unique
            # tiebreaker (e.g. Model.id) — OFFSET pagination over a non-unique
            # sort key repeats/drops rows across pages whenever the planner
            # reorders ties.
            if isinstance(order_by, (list, tuple)):
                stmt = stmt.order_by(*order_by)
            else:
                stmt = stmt.order_by(order_by)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(
        self,
        *filters,
        skip: int = 0,
        limit: int = 50,
        order_by=None,
    ) -> tuple[list[ModelT], int]:
        """Return (items, total_count) for paginated responses."""
        total = await self.count(*filters)
        items = await self.list(*filters, skip=skip, limit=limit, order_by=order_by)
        return items, total

    # ── mutations ────────────────────────────────────────────────────────────

    async def create(self, data: dict) -> ModelT:
        obj = self.model(**data)
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, data: dict) -> ModelT:
        for key, value in data.items():
            setattr(obj, key, value)
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def save(self, obj: ModelT) -> ModelT:
        """Persist an already-modified object."""
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.commit()
