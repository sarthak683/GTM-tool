"""Mailbox selection when a rep has more than one connected inbox.

Reps send manual outreach from their alternate sending domains
(@beaconli.com / @beaconli.co) as well as their @beacon.li login, so each of
those mailboxes must be connected in its own right for those emails to be
ingested and counted. The schema always allowed it — unique on
(user_id, email_address) — but the read paths assumed one inbox per user.
"""
from uuid import uuid4

from app.services.email_connections import (
    all_connections_stmt,
    connection_for_mailbox_stmt,
    primary_connection_stmt,
)


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_primary_selector_is_bounded_to_one_row():
    """The bug this prevents: scalar_one_or_none() raising MultipleResultsFound
    the moment a rep connects a second mailbox."""
    sql = _sql(primary_connection_stmt(uuid4()))
    assert "LIMIT 1" in sql.upper()


def test_primary_selector_is_deterministic():
    """Without an explicit order, 'the' connection depends on row order — so a
    rep's outbound identity could change between requests."""
    sql = _sql(primary_connection_stmt(uuid4())).upper()
    assert "ORDER BY" in sql
    assert "CONNECTED_AT" in sql
    # id tie-break so two mailboxes connected in the same instant still order.
    assert sql.count("ASC") >= 2


def test_primary_prefers_earliest_connected():
    """Earliest = the rep's original @beacon.li login; alternates are added
    later. Ordering by newest would make the agent send as an alternate."""
    sql = _sql(primary_connection_stmt(uuid4())).upper()
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "DESC" not in order_clause


def test_primary_can_require_active():
    # Assert on the WHERE predicate, not the column list — is_active is selected
    # either way, so a bare substring check would always pass.
    assert "is_active = true" in _sql(primary_connection_stmt(uuid4(), active_only=True)).lower()
    assert "is_active = true" not in _sql(primary_connection_stmt(uuid4())).lower()


def test_all_connections_is_not_limited():
    """Ingestion must fan out across every mailbox, not just the primary."""
    sql = _sql(all_connections_stmt(uuid4())).upper()
    assert "LIMIT" not in sql
    assert "ORDER BY" in sql


def test_all_connections_defaults_to_active_only():
    assert "is_active = true" in _sql(all_connections_stmt(uuid4())).lower()
    assert "is_active = true" not in _sql(all_connections_stmt(uuid4(), active_only=False)).lower()


def test_mailbox_selector_keys_on_both_user_and_address():
    """THE core fix: matching on user_id alone made a second connect OVERWRITE
    the rep's existing mailbox, silently unsyncing their primary inbox."""
    uid = uuid4()
    sql = _sql(connection_for_mailbox_stmt(uid, "sipra@beaconli.com"))
    # SQLAlchemy renders UUIDs dashless in literal binds.
    assert uid.hex in sql.replace("-", "")
    assert "sipra@beaconli.com" in sql
    assert sql.upper().count("AND") >= 1


def test_mailbox_selector_normalises_the_address():
    """Google can hand back mixed case / padding; the unique index is on the
    stored value, so a stray variant would insert a duplicate row instead of
    updating the existing one."""
    sql = _sql(connection_for_mailbox_stmt(uuid4(), "  Sipra@BeaconLi.COM  "))
    assert "sipra@beaconli.com" in sql
    assert "Sipra@BeaconLi.COM" not in sql
