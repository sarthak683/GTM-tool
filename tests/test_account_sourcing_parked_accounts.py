"""Parked accounts must drop out of the Account Sourcing browse view.

``INACTIVE_ACCOUNT_STATUSES`` documents itself as the definition every surface
shares, and claimed the account list hides these from default views. It did
not: ``company_visibility_filter`` early-returns for admins **before** its
disabled-status gate, and the list's status filter only ever ADDS statuses, so
an admin's default view carried every parked account with no way to exclude
them — 316 of 972 rows in production, a third of the list, permanently.

The prospecting list already gates every role this way, so this is the sourcing
list catching up rather than a new rule.

Escape hatches that must survive, because losing an account to a list that
cannot search it has bitten this app before (the ClickUp
``hidden_from_account_sourcing`` flag, repaired by migration 119):

* filtering FOR a disabled status — reviewing parked accounts is legitimate
* an explicit id selection
* any search term — a parked account stays findable by name/domain

Pure-logic tests: they compile the statement against the PostgreSQL dialect and
read the SQL, no database needed (same philosophy as
``tests/test_account_sourcing_filters.py``).
"""
from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.api.v1.endpoints import account_sourcing as m


def _compiled(**filter_kwargs):
    stmt = m.build_sourced_companies_stmt(
        SimpleNamespace(id=uuid4(), role="admin"),
        m.CompanySourcingFilters(**filter_kwargs),
    )
    return stmt.compile(dialect=postgresql.dialect())


# The gate compiles to:
#   (companies.account_status IS NULL OR (companies.account_status NOT IN (...)))
# The statuses themselves are an *expanding* bind, rendered as a postcompile
# placeholder and absent from `compiled.params`, so match the shape rather than
# the values. An admin's `visible_to` contributes no account_status predicate
# of its own (it early-returns), so any match here is this gate.
_GATE = re.compile(
    r"account_status is null or \(?companies\.account_status not in", re.I
)


def _has_parked_gate(compiled) -> bool:
    return bool(_GATE.search(str(compiled)))


def test_default_browse_hides_parked_accounts_even_for_an_admin():
    assert _has_parked_gate(_compiled()), (
        "the default sourcing list still returns not_a_fit/dnd accounts — the "
        "production bug this guards (316 of 972 rows for an admin)"
    )


def test_gate_is_null_safe():
    """``NULL NOT IN (...)`` is NULL, which is falsy — an un-set
    account_status must not be filtered out along with the parked ones."""
    sql = str(_compiled()).lower()
    assert "account_status is null" in sql, (
        "gate must OR in an IS NULL branch, or every account with no status "
        "disappears from the list"
    )


def test_filtering_for_a_parked_status_shows_them():
    """Reviewing parked accounts is the whole point of that filter; applying
    the gate as well would return an empty list."""
    assert not _has_parked_gate(_compiled(account_status="not_a_fit"))


def test_filtering_for_dnd_also_bypasses_the_gate():
    assert not _has_parked_gate(_compiled(account_status="dnd"))


def test_an_active_status_filter_keeps_the_gate():
    """Asking for `in_progress` is still a browse view — parked stays hidden."""
    assert _has_parked_gate(_compiled(account_status="in_progress"))


def test_a_search_term_still_finds_a_parked_account():
    """Re-enabling an account must not require knowing its URL."""
    assert not _has_parked_gate(_compiled(q="niva")), (
        "search must bypass the gate — a parked account has to stay findable"
    )


def test_an_explicit_id_selection_bypasses_the_gate():
    assert not _has_parked_gate(_compiled(company_ids=str(uuid4())))


def test_gate_lives_in_the_shared_statement_builder():
    """List, summary, CSV export and the filter-wide bulk-assign all build
    here, so the gate covers all four — the point of the shared builder."""
    source = inspect.getsource(m.build_sourced_companies_stmt)
    assert "INACTIVE_ACCOUNT_STATUSES" in source
    assert "include_disabled" in source


def test_contract_comment_no_longer_overclaims():
    """The comment on INACTIVE_ACCOUNT_STATUSES sent this audit down a wrong
    path by asserting guarantees that were not implemented."""
    import app.models.company as company_module

    src = inspect.getsource(company_module)
    head = src[: src.index("INACTIVE_ACCOUNT_STATUSES = ")]
    # It must call out the admin early-return, which is the actual trap.
    assert "early-return" in head, "the admin early-return must stay documented"
    # And must not blanket-claim every reminder job skips parked accounts —
    # deal next-step reminders deliberately do not.
    assert "reminder jobs skip them" not in head
