"""Execute badge predicates against live, missing, deleted, and hidden entities."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import sqlite

from app.api.v1.endpoints.tasks import get_task_count


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "sdr", "ae"])
async def test_badge_matches_workspace_entity_visibility_and_never_writes(role):
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE tasks(id TEXT, assigned_to_id TEXT, status TEXT, entity_type TEXT, entity_id TEXT);
        CREATE TABLE companies(id TEXT, assigned_to_id TEXT, sdr_id TEXT, deleted_at TEXT);
        CREATE TABLE contacts(id TEXT, company_id TEXT, assigned_to_id TEXT, sdr_id TEXT);
        CREATE TABLE deals(id TEXT, company_id TEXT, assigned_to_id TEXT, deleted_at TEXT);
    """)
    uid, other = uuid4(), uuid4()
    owned, foreign, removed_company = uuid4(), uuid4(), uuid4()
    live_deal, deleted_deal, live_contact, hidden_contact = (uuid4() for _ in range(4))
    db.executemany("INSERT INTO companies VALUES(?,?,?,?)", [
        (owned.hex, uid.hex, None, None),
        (foreign.hex, other.hex, None, None),
        (removed_company.hex, uid.hex, None, "2026-01-01"),
    ])
    db.executemany("INSERT INTO deals VALUES(?,?,?,?)", [
        (live_deal.hex, owned.hex, uid.hex, None),
        (deleted_deal.hex, owned.hex, uid.hex, "2026-01-01"),
    ])
    db.executemany("INSERT INTO contacts VALUES(?,?,?,?)", [
        (live_contact.hex, owned.hex, uid.hex, None),
        (hidden_contact.hex, foreign.hex, uid.hex, None),
    ])
    for kind, eid in [("company", owned), ("contact", live_contact), ("deal", live_deal),
                      ("company", foreign), ("contact", hidden_contact),
                      ("company", removed_company), ("deal", deleted_deal),
                      ("deal", uuid4()), ("contact", uuid4())]:
        db.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", (uuid4().hex, uid.hex, "open", kind, eid.hex))
    # Closed and teammate-owned tasks cannot add to the badge either.
    db.executemany("INSERT INTO tasks VALUES(?,?,?,?,?)", [
        (uuid4().hex, uid.hex, "completed", "deal", live_deal.hex),
        (uuid4().hex, other.hex, "open", "deal", live_deal.hex),
    ])
    async def execute(statement):
        sql = str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
        value = db.execute(sql).fetchone()[0]
        return SimpleNamespace(scalar_one=lambda: value)
    session = SimpleNamespace(execute=AsyncMock(side_effect=execute), get=AsyncMock(return_value=None),
                              commit=AsyncMock(), add=AsyncMock())
    user = SimpleNamespace(id=uid, role=role, is_admin=role == "admin")
    assert await get_task_count(session, user) == {"open": 5}
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_awaited()
    db.close()
