"""
One-shot script to delete Saher Ghattas from the CRM.
Run from inside the backend container:

  docker compose exec backend python scripts/remove_saher.py

Audit confirmed 0 deals / companies / contacts / tasks linked to this user.
"""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings


async def run():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Confirm user exists
        result = await conn.execute(
            text("SELECT id, email, name, role FROM users WHERE email ILIKE :q OR name ILIKE :q"),
            {"q": "%saher%"},
        )
        rows = result.fetchall()
        if not rows:
            print("No user matching 'saher' found — nothing to do.")
            return

        print("User(s) found:")
        for row in rows:
            print(f"  id={row[0]}  email={row[1]}  name={row[2]}  role={row[3]}")

        # Delete user aliases first (FK), then the user
        for row in rows:
            uid = row[0]
            email = row[1]

            alias_del = await conn.execute(
                text("DELETE FROM user_aliases WHERE user_id = :uid"),
                {"uid": uid},
            )
            print(f"Deleted {alias_del.rowcount} alias row(s) for {email}")

            workspace_del = await conn.execute(
                text("DELETE FROM workspace_settings WHERE created_by_id = :uid"),
                {"uid": uid},
            )
            print(f"Deleted {workspace_del.rowcount} workspace_settings row(s) for {email}")

            user_del = await conn.execute(
                text("DELETE FROM users WHERE id = :uid"),
                {"uid": uid},
            )
            print(f"Deleted user: {email} ({user_del.rowcount} row)")

    print("\nDone. Saher has been removed from the CRM.")


asyncio.run(run())
