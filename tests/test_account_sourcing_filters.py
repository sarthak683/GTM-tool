"""Account Sourcing shared-filter / sort / data-health contract tests.

Pure-logic tests: they compile SQLAlchemy statements against the PostgreSQL
dialect and inspect endpoint signatures — no database, Redis, or Celery needed
(same philosophy as ``tests/conftest.py``).

What is pinned down here:

1. The list, summary, and CSV export endpoints (and the filter-wide company
   bulk-assign in ``assignments.py``) all consume the SAME
   ``CompanySourcingFilters`` dependency — the regression this guards against
   is the export drifting back to its own smaller parameter set / different
   base population.
2. ``build_sourced_companies_stmt`` applies the account-sourcing base
   visibility (NOT the old ``sourcing_batch_id IS NOT NULL`` export base) and
   the new ``batch_id`` filter.
3. ``apply_company_sort`` — accepted keys, default ordering, NULLS placement,
   stable id tiebreaker, and 422 on junk input.
4. The three data-health statements keep the semantics of the audited prod
   queries in scripts/prod-repair/sourcing-repair-2026-08-16.sh.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def m():
    import app.api.v1.endpoints.account_sourcing as module

    return module


@pytest.fixture()
def admin_user():
    return SimpleNamespace(id=uuid4(), role="admin")


def _compiled(stmt):
    from sqlalchemy.dialects import postgresql

    return stmt.compile(dialect=postgresql.dialect())


def _sql(stmt) -> str:
    """Compiled SQL with bind PLACEHOLDERS (works for statements carrying JSONB
    binds, which have no literal renderer)."""
    return str(_compiled(stmt))


def _sql_literal(stmt) -> str:
    """Compiled SQL with bind VALUES inlined — used for the data-health
    statements so the assertions can read the actual predicates. Note psycopg
    doubles the ``%`` in LIKE patterns during literal rendering."""
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ── 1. Shared dependency wiring (anti-drift) ─────────────────────────────────


def test_list_summary_export_and_by_filter_share_the_filter_dependency(m):
    import app.api.v1.endpoints.assignments as assignments

    for fn in (
        m.list_sourced_companies,
        m.get_sourced_company_summary,
        m.export_sourced_companies,
        assignments.bulk_assign_companies_by_filter,
    ):
        params = inspect.signature(fn).parameters
        assert "filters" in params, f"{fn.__name__} lost the shared filters dependency"
        assert params["filters"].annotation is m.CompanySourcingFilters, (
            f"{fn.__name__} no longer uses CompanySourcingFilters — list/export/"
            "bulk-assign populations can drift again"
        )


def test_list_and_export_both_accept_sort_params(m):
    for fn in (m.list_sourced_companies, m.export_sourced_companies):
        params = inspect.signature(fn).parameters
        assert "sort" in params and "order" in params, (
            f"{fn.__name__} must accept sort/order so a sorted export matches the list"
        )


# ── 2. Statement builder ─────────────────────────────────────────────────────


def test_base_visibility_is_the_account_sourcing_set(m, admin_user):
    compiled = _compiled(
        m.build_sourced_companies_stmt(admin_user, m.CompanySourcingFilters())
    )
    sql, values = str(compiled), list(compiled.params.values())
    # The account-sourcing base admits placeholder accounts and accounts with
    # deals — not just batch imports (the old export-only base). The JSONB
    # predicates travel as bind params, hence the params check.
    assert "companies.sourcing_batch_id IS NOT NULL" in sql
    assert {"prospect_import_placeholder": {}} in values
    assert "EXISTS (SELECT deals.id" in sql
    # ClickUp rows hidden from Account Sourcing stay hidden (NULL-safe).
    assert "companies.enrichment_sources IS NULL OR NOT" in sql
    assert {"clickup_import": {"hidden_from_account_sourcing": True}} in values
    # Soft-deleted companies are excluded for everyone.
    assert "companies.deleted_at IS NULL" in sql


def test_batch_id_filter_is_applied(m, admin_user):
    batch_id = uuid4()
    compiled = _compiled(
        m.build_sourced_companies_stmt(
            admin_user, m.CompanySourcingFilters(batch_id=batch_id)
        )
    )
    assert "companies.sourcing_batch_id = " in str(compiled)
    assert batch_id in compiled.params.values()


def test_full_filter_set_is_applied(m, admin_user):
    filters = m.CompanySourcingFilters(
        q="acme",
        icp_tier="hot",
        disposition="interested",
        account_status="in_progress",
        recommended_outreach_lane="warm_intro",
        assigned_rep="Jane Doe",
        assigned_rep_email="jane@beacon.li",
        prospects_min=2,
        prospects_max=9,
        company_ids=str(uuid4()),
    )
    compiled = _compiled(m.build_sourced_companies_stmt(admin_user, filters))
    sql, values = str(compiled), list(compiled.params.values())
    assert "companies.name ILIKE" in sql and "%acme%" in values
    assert "companies.icp_tier IN" in sql
    assert "companies.disposition IN" in sql
    assert "companies.account_status IN" in sql
    assert "companies.recommended_outreach_lane IN" in sql
    assert "companies.assigned_rep = " in sql and "Jane Doe" in values
    assert "companies.assigned_rep_email = " in sql and "jane@beacon.li" in values
    assert "coalesce(anon_1.cnt" in sql and 2 in values and 9 in values
    assert "companies.id IN" in sql


def test_non_admin_visibility_is_ownership_gated(m):
    rep = SimpleNamespace(id=uuid4(), role="sdr")
    compiled = _compiled(m.build_sourced_companies_stmt(rep, m.CompanySourcingFilters()))
    sql = str(compiled)
    assert "companies.assigned_to_id = " in sql and rep.id in compiled.params.values()
    # Default lists hide parked (not_a_fit/dnd) accounts for non-admins…
    assert "companies.account_status" in sql
    # …but explicitly filtering for a disabled status lifts that hiding so an
    # owner can review/re-enable their parked accounts.
    lifted = _sql(
        m.build_sourced_companies_stmt(
            rep, m.CompanySourcingFilters(account_status="not_a_fit")
        )
    )
    assert "companies.account_status NOT IN" not in lifted


def test_owner_unassigned_sentinel(m, admin_user):
    uid = uuid4()
    sql = _sql(
        m.build_sourced_companies_stmt(
            admin_user, m.CompanySourcingFilters(owner_id=f"{uid},__unassigned__")
        )
    )
    assert "companies.assigned_to_id IN" in sql
    assert "companies.assigned_to_id IS NULL AND companies.sdr_id IS NULL" in sql


# ── 3. Sort contract ─────────────────────────────────────────────────────────


def test_sort_keys_contract(m):
    assert m.COMPANY_SORT_KEYS == (
        "created_at",
        "name",
        "icp_score",
        "prospect_count",
        "enriched_at",
    )


def test_default_sort_preserves_historical_ordering(m, admin_user):
    base = m.build_sourced_companies_stmt(admin_user, m.CompanySourcingFilters())
    sql = _sql(m.apply_company_sort(base, None, None))
    assert sql.rstrip().endswith("ORDER BY companies.created_at DESC, companies.id DESC")


@pytest.mark.parametrize(
    "sort,order,fragment",
    [
        ("icp_score", None, "companies.icp_score DESC NULLS LAST, companies.id DESC"),
        ("icp_score", "asc", "companies.icp_score ASC NULLS LAST, companies.id DESC"),
        ("enriched_at", "desc", "companies.enriched_at DESC NULLS LAST, companies.id DESC"),
        # asc puts NULLs FIRST on purpose: it is the "unenriched first" view.
        ("enriched_at", "asc", "companies.enriched_at ASC NULLS FIRST, companies.id DESC"),
        ("name", None, "ORDER BY lower(companies.name) ASC, companies.id DESC"),
        ("name", "desc", "ORDER BY lower(companies.name) DESC, companies.id DESC"),
        ("created_at", "asc", "ORDER BY companies.created_at ASC, companies.id DESC"),
    ],
)
def test_sort_variants(m, admin_user, sort, order, fragment):
    base = m.build_sourced_companies_stmt(admin_user, m.CompanySourcingFilters())
    assert fragment in _sql(m.apply_company_sort(base, sort, order))


def test_prospect_count_sort_uses_live_contact_count(m, admin_user):
    base = m.build_sourced_companies_stmt(admin_user, m.CompanySourcingFilters())
    sql = _sql(m.apply_company_sort(base, "prospect_count", None))
    assert "SELECT count(contacts.id)" in sql
    assert "contacts.company_id = companies.id" in sql
    assert sql.rstrip().endswith("DESC, companies.id DESC")


@pytest.mark.parametrize("sort,order", [("bogus", None), ("name", "sideways"), ("priority", None)])
def test_invalid_sort_inputs_are_422(m, admin_user, sort, order):
    from fastapi import HTTPException

    base = m.build_sourced_companies_stmt(admin_user, m.CompanySourcingFilters())
    with pytest.raises(HTTPException) as exc:
        m.apply_company_sort(base, sort, order)
    assert exc.value.status_code == 422


# ── 4. Data-health queries (ported prod-repair semantics) ────────────────────


def test_data_health_freemail_list_matches_the_prod_script(m):
    # scripts/prod-repair/sourcing-repair-2026-08-16.sh $FREEMAIL, verbatim.
    assert set(m._DATA_HEALTH_FREEMAIL) == {
        "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
        "aol.com", "proton.me", "protonmail.com", "live.com", "msn.com",
        "googlemail.com", "rediffmail.com", "qq.com", "yandex.com", "ymail.com",
        "me.com",
    }


def test_sdr_conflict_query_semantics(m):
    sql = _sql_literal(m._sdr_conflict_stmt())
    assert "contacts.sdr_id IS NOT NULL" in sql
    assert "companies.sdr_id IS NOT NULL" in sql
    assert "contacts.sdr_id != companies.sdr_id" in sql
    assert "companies.deleted_at IS NULL" in sql
    assert "count(contacts.id)" in sql and "GROUP BY" in sql


def test_misattached_query_semantics(m):
    sql = _sql_literal(m._misattached_stmt())
    # dominant domain per account, ALL domains counted (freemail included, like
    # the script), needing >= 3 contacts of evidence.
    assert "row_number() OVER (PARTITION BY contacts.company_id ORDER BY count(*) DESC)" in sql
    assert "dom.n >= 3" in sql
    # neither the dominant domain, a subdomain of it, nor the account's own
    # normalized domain; freemail contacts excluded from flagging.
    assert "lower(split_part(contacts.email, '@', 2)) != dom.dominant" in sql
    assert "NOT LIKE concat('%%.', dom.dominant)" in sql
    assert (
        "regexp_replace(split_part(regexp_replace(lower(coalesce(companies.domain, '')), "
        "'^https?://', ''), '/', 1), '^www.', '')"
    ) in sql
    assert "proton.me" in sql and "rediffmail.com" in sql
    # suggested home = live company whose domain equals the email domain.
    assert "LEFT OUTER JOIN companies AS companies_1" in sql
    assert "companies_1.deleted_at IS NULL" in sql
    assert "companies.deleted_at IS NULL" in sql


def test_domain_correction_query_semantics(m):
    sql = _sql_literal(m._domain_correction_stmt())
    # placeholder accounts are the script's AUTO path — excluded from review.
    assert "NOT LIKE '%%.unknown'" in sql
    # dominant computed over freemail-EXCLUDED corporate domains only.
    assert "gmail.com" in sql and "NOT IN" in sql
    assert "sum(" in sql  # evidence_count = total corporate contacts
    assert "CASE WHEN" in sql  # rn=1 dominant pick
    assert "EXISTS" in sql  # suggested_domain_taken collision flag
    # dominant disagrees with, and is not a subdomain of, the current domain.
    assert "stats.dominant != regexp_replace(" in sql
    assert "NOT LIKE concat('%%.', regexp_replace(" in sql
    assert "companies.deleted_at IS NULL" in sql


# ── 4b. Import owner-resolution failures surface in the batch error log ──────


def test_owner_resolution_error_message_shape(m):
    msg = m.owner_resolution_error_message("SDR", "jane@x.com", ["Acme"])
    assert msg.startswith("SDR 'jane@x.com' did not match an active user")
    assert "left unassigned on 1 account(s): Acme" in msg
    assert "more" not in msg


def test_owner_resolution_error_message_aggregates_accounts(m):
    names = [f"Acct {i}" for i in range(1, 9)]
    msg = m.owner_resolution_error_message("AE", "Jane Doe", names)
    # One bounded row per unresolved cell: a running count, the first few
    # affected accounts, and a "+N more" tail instead of one row per CSV line.
    assert "left unassigned on 8 account(s)" in msg
    assert "Acct 1, Acct 2, Acct 3, Acct 4, Acct 5" in msg
    assert "(+3 more)" in msg
    assert "Acct 6" not in msg
    assert m.OWNER_ERROR_NAMES_SHOWN == 5


def test_owner_resolution_failures_do_not_inflate_failed_rows(m):
    """An unresolvable owner cell leaves the slot empty — the ACCOUNT still
    imports, so it must not be counted as a failed/skipped row."""
    src = inspect.getsource(m._process_uploaded_rows)
    resolver = src.split("def _resolve_user(", 1)[1].split("\n    async def ", 1)[0]
    assert "_record_owner_resolution_failure(" in resolver
    # The resolver body must not touch either counter.
    assert "failed +=" not in resolver and "skipped +=" not in resolver
    # failed_rows/skipped_rows keep tracking only the real per-row outcomes.
    assert "progress_batch.failed_rows = failed" in src
    assert "progress_batch.skipped_rows = skipped" in src
    assert "progress_batch.error_log = errors if errors else None" in src


def test_batch_error_rows_keep_the_shared_name_error_shape(m):
    """enrichment.py also writes error_log — the shape must stay consistent."""
    src = inspect.getsource(m._process_uploaded_rows)
    assert '{"name": row_name, "error": message}' in src
    assert 'errors.append({"name": name, "error": str(e)})' in src


def test_data_health_route_is_admin_only(m):
    """The endpoint's auth dependency must be AdminUser (403 for non-admins)."""
    import app.core.dependencies as deps

    sig = inspect.signature(m.get_sourcing_data_health)
    assert sig.parameters["_admin"].annotation is deps.AdminUser


# ── 5. Auth gates over the wire (no DB touched — rejected pre-query) ─────────


AUTH_REJECTED = {401, 403}


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/account-sourcing/data-health"),
        ("GET", "/api/v1/account-sourcing/companies"),
        ("GET", "/api/v1/account-sourcing/export"),
        ("GET", "/api/v1/account-sourcing/summary"),
        ("PATCH", "/api/v1/assignments/companies/by-filter"),
    ],
)
def test_new_and_changed_routes_require_auth(client, method, path):
    resp = client.request(method, path)
    assert resp.status_code in AUTH_REJECTED, (
        f"{method} {path} must reject unauthenticated requests, got {resp.status_code}"
    )
