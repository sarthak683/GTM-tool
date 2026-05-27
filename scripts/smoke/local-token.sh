#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

docker compose exec -T backend python - <<'PY'
import asyncio
from sqlmodel import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.auth import create_access_token


async def main() -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.is_active == True).order_by(User.email)  # noqa: E712
            )
        ).scalars().first()
        if not user:
            raise SystemExit("No active local user found. Sign in or seed users first.")
        print(f"email={user.email}")
        print(create_access_token(user.id, user.role))


asyncio.run(main())
PY

