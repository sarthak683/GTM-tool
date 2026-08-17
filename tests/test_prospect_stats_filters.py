"""GET /contacts/stats + KPI tile filters — pure-logic drift guards.

The Prospecting KPI tiles used to be computed client-side from the visible
50-row page. They now come from ``ContactRepository.aggregate_engagement_stats``
and tile clicks apply real server filters (``emails_opened`` /
``linkedin_active`` / ``meetings_booked`` on ``ContactFilters``). These tests
pin the invariants that keep the tiles honest WITHOUT a database:

1. every ``ContactFilters`` field is accepted by BOTH repo entry points, so
   ``**filters.as_repo_kwargs()`` can never TypeError after someone adds a
   filter to one side only;
2. the stats aggregate reuses the list's WHERE clause verbatim (same
   ``_filtered_statements`` builder) — tile counts and tile-click results are
   the same population by construction;
3. the tile predicates match the frontend tile semantics they replaced;
4. the ``/stats`` route is registered before ``/{contact_id}`` so "stats" is
   never parsed as a UUID.
"""
from dataclasses import fields as dataclass_fields
from inspect import signature
from types import SimpleNamespace

from app.api.v1.endpoints.contacts import ContactFilters
from app.api.v1.endpoints.contacts import router as contacts_router
from app.repositories.contact import (
    ContactRepository,
    emails_opened_clause,
    linkedin_active_clause,
    meetings_booked_clause,
)


def _repo() -> ContactRepository:
    # Statement building never touches the session, so None is fine here.
    return ContactRepository(session=None)


def _compiled(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


# ── 1. ContactFilters ↔ repository signature parity ────────────────────────

def test_every_contact_filter_is_accepted_by_list_and_stats():
    filter_keys = [f.name for f in dataclass_fields(ContactFilters)]
    list_params = signature(ContactRepository.list_with_company_name).parameters
    stats_params = signature(ContactRepository.aggregate_engagement_stats).parameters
    for key in filter_keys:
        assert key in list_params, f"list_with_company_name is missing filter {key!r}"
        assert key in stats_params, f"aggregate_engagement_stats is missing filter {key!r}"


def test_tile_filters_exist_on_contact_filters():
    filter_keys = {f.name for f in dataclass_fields(ContactFilters)}
    assert {"emails_opened", "linkedin_active", "meetings_booked"} <= filter_keys


# ── 2. stats aggregate shares the list's WHERE clause ──────────────────────

async def test_stats_where_clause_matches_count_statement():
    executed = {}

    async def _execute(stmt):
        executed["stmt"] = stmt
        return SimpleNamespace(
            one=lambda: SimpleNamespace(
                total=7, emails_opened=3, linkedin_active=2, meetings_booked=1
            )
        )

    repo = ContactRepository(session=SimpleNamespace(execute=_execute))
    kwargs = dict(
        prospect_only=True,
        restrict_to_owner_id="9c1f6f5e-0000-4000-8000-000000000001",
        restrict_to_role="sdr",
        call_disposition="connected_not_interested",
        emails_opened=True,
    )
    stats = await repo.aggregate_engagement_stats(**kwargs)

    assert stats == {
        "total": 7,
        "emails_opened": 3,
        "linkedin_active": 2,
        "meetings_booked": 1,
    }

    # The executed aggregate must carry EXACTLY the WHERE the count/list use.
    _base, count_stmt, _rank = repo._filtered_statements(**kwargs)
    assert str(executed["stmt"].whereclause) == str(count_stmt.whereclause)
    # One row, four aggregates: total + three conditional (CASE WHEN) counts.
    sql = str(executed["stmt"])
    assert sql.count("CASE WHEN") == 3
    assert "count(" in sql


def test_tile_filters_apply_to_both_list_and_count_statements():
    repo = _repo()
    base_stmt, count_stmt, _rank = repo._filtered_statements(
        emails_opened=True, linkedin_active=True, meetings_booked=True
    )
    for stmt in (base_stmt, count_stmt):
        where_sql = str(stmt.whereclause)
        assert "email_open_count" in where_sql
        assert "linkedin_status" in where_sql
        assert "sequence_status" in where_sql


def test_tile_filters_default_to_noop():
    repo = _repo()
    _base, with_none, _r1 = repo._filtered_statements()
    _base2, with_false, _r2 = repo._filtered_statements(
        emails_opened=False, linkedin_active=False, meetings_booked=False
    )
    # False/absent are both no-ops — there is no inverse tile.
    assert str(with_none.whereclause) == str(with_false.whereclause)


# ── 3. tile predicates mirror the frontend tile semantics ──────────────────

def test_emails_opened_clause_semantics():
    # Frontend tile: (email_open_count ?? 0) > 0
    assert _compiled(emails_opened_clause()) == "contacts.email_open_count > 0"


def test_linkedin_active_clause_semantics():
    # Frontend tile: !!linkedin_status && linkedin_status !== "none"
    sql = _compiled(linkedin_active_clause())
    assert "contacts.linkedin_status IS NOT NULL" in sql
    assert "contacts.linkedin_status != ''" in sql
    assert "contacts.linkedin_status != 'none'" in sql


def test_meetings_booked_clause_semantics():
    # Frontend tile: sequence_status === "meeting_booked"
    assert _compiled(meetings_booked_clause()) == "contacts.sequence_status = 'meeting_booked'"


# ── 4. route ordering ──────────────────────────────────────────────────────

def test_stats_route_registered_before_contact_id_route():
    paths = [route.path for route in contacts_router.routes]
    assert "/contacts/stats" in paths
    assert paths.index("/contacts/stats") < paths.index("/contacts/{contact_id}")
