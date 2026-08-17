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


# ── 4c. Misattached rows are CLASSIFIED, not all called errors ───────────────
#
# The detection matches the audited prod CSVs (192 rows), but only 48 of those
# are genuine misattachments — the other 144 are acquisitions / alternate brands
# filed CORRECTLY under an account that is merely missing the domain as an
# alias. Calling those "misattached" invites an admin to scatter correctly
# consolidated accounts, so cause and recommended action are explicit.


def test_misattached_cause_is_derived_from_domain_ownership(m):
    # Another live account owns the domain → genuinely ambiguous.
    assert m._misattached_cause(uuid4()) == "misattached"
    # Nobody else owns it → the ACCOUNT is missing an alias, nothing is misfiled.
    assert m._misattached_cause(None) == "alias_gap"
    assert set(m.MISATTACHED_CAUSES) == {"alias_gap", "misattached"}
    assert m.MISATTACHED_ACTIONS == {"alias_gap": "add_alias", "misattached": "relink"}


def _mis_row(**over):
    base = dict(
        contact_id=uuid4(),
        contact_first_name="Bill",
        contact_last_name="Reid",
        contact_email="breid@infinityqs.com",
        contact_domain="infinityqs.com",
        contact_sdr_name="Jane",
        current_company_id=uuid4(),
        current_company_name="Advantive",
        current_company_domain="advantive.com",
        company_dominant_domain="advantive.com",
        dominant_contact_count=9,
        suggested_company_id=None,
        suggested_company_name=None,
        suggested_company_domain=None,
        domain_owner_id=None,
        domain_owner_name=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_alias_gap_row_reads_as_an_incomplete_account_not_an_error(m):
    row = m._misattached_row(_mis_row())
    assert row["likely_cause"] == "alias_gap"
    assert row["recommended_action"] == "add_alias"
    assert row["domain_owner_id"] is None
    # The old wording asserted a mismatch for every row; an alias gap must say
    # the ACCOUNT is incomplete, and must not claim the prospect is misfiled.
    ev = row["evidence"]
    assert "No live account owns 'infinityqs.com'" in ev
    assert "alias" in ev and "Advantive" in ev
    assert "misattached" not in ev.lower()


def test_true_misattachment_row_names_the_account_that_owns_the_domain(m):
    owner_id = uuid4()
    row = m._misattached_row(
        _mis_row(
            contact_domain="acme.com",
            suggested_company_id=owner_id,
            suggested_company_name="Acme",
            suggested_company_domain="acme.com",
            domain_owner_id=owner_id,
            domain_owner_name="Acme",
        )
    )
    assert row["likely_cause"] == "misattached"
    assert row["recommended_action"] == "relink"
    assert row["suggested_company_id"] == str(owner_id)
    assert row["domain_owner_id"] == str(owner_id)
    assert "'acme.com' already belongs to the live account Acme" in row["evidence"]


def test_misattached_row_classifies_alias_only_owners_without_a_primary_suggestion(m):
    """A domain claimed only as ANOTHER account's alias is still a real conflict
    (adding it here would 422), even though the primary-domain join finds no
    suggestion — so the cause must not be inferred from suggested_company_id."""
    owner_id = uuid4()
    row = m._misattached_row(
        _mis_row(domain_owner_id=owner_id, domain_owner_name="Globex", suggested_company_id=None)
    )
    assert row["likely_cause"] == "misattached"
    assert row["suggested_company_id"] is None
    assert "Globex" in row["evidence"]


def test_misattached_query_exposes_domain_ownership_over_primary_and_alias(m):
    sql = _sql_literal(m._misattached_stmt())
    # Correlated LIMIT 1 scalar subqueries — NOT a second join, which would
    # duplicate rows (and inflate the audited totals) when two accounts claim
    # the same domain.
    assert sql.count("AS domain_owner_id") == 1 and sql.count("AS domain_owner_name") == 1
    assert sql.count("LIMIT 1") == 2
    # Ownership = primary domain OR an alias in additional_domains.
    assert "coalesce(companies_2.additional_domains, '[]'::jsonb) @> jsonb_build_array(" in sql
    assert "lower(companies_2.domain) = lower(split_part(contacts.email, '@', 2))" in sql
    # Primary-domain owners sort first, so domain_owner agrees with suggested_*.
    assert "ORDER BY CASE WHEN (lower(companies_2.domain)" in sql
    # The original primary-domain suggestion join is untouched.
    assert "LEFT OUTER JOIN companies AS companies_1" in sql


def test_misattached_query_drops_contacts_already_aliased_on_their_account(m):
    """Otherwise the UI's "Add alias" fix would not stick: the rows it resolved
    would reappear the next time the report is run."""
    sql = _sql_literal(m._misattached_stmt())
    assert (
        "NOT (coalesce(companies.additional_domains, '[]'::jsonb) @> jsonb_build_array("
        "lower(split_part(contacts.email, '@', 2))))"
    ) in sql


def test_data_health_reports_per_cause_subtotals_over_the_full_set(m):
    """`alias_gap_total` / `misattached_total` must come from a grouped COUNT
    over the whole statement, not from the (limit-capped) returned rows."""
    src = inspect.getsource(m.get_sourcing_data_health)
    assert "alias_gap_total" in src and "misattached_total" in src
    assert "mis_total = alias_gap_total + misattached_total" in src
    # Subtotals are computed from the statement subquery, before .limit().
    grouped = src.split("mis_sub = ", 1)[1].split("mis_rows = ", 1)[0]
    assert "domain_owner_id.is_(None)" in grouped and "group_by" in grouped
    assert ".limit(" not in grouped


# ── 4d. Buying-journey funnel is scoped like every other sourcing surface ────


def test_recotap_summary_uses_the_shared_filters_and_visibility(m):
    """The funnel used to count RecotapAccount rows with NO company scoping, so
    an SDR owning 12 accounts saw the whole workspace's stage counts."""
    params = inspect.signature(m.recotap_summary).parameters
    assert params["filters"].annotation is m.CompanySourcingFilters
    src = inspect.getsource(m.recotap_summary)
    assert "build_sourced_companies_stmt(" in src
    # The scope predicate is bound once and reused; assert on the property (every
    # counting query is scoped) rather than on one spelling of it, so extracting
    # a local or a helper doesn't fail a guard whose point still holds.
    assert "in_scope = RecotapAccount.company_id.in_(scoped_ids)" in src
    # Every `.where(` inside this endpoint must carry the scope. Missing it is
    # exactly the bug this guard exists for: an unscoped count showed an SDR the
    # whole workspace's funnel.
    where_calls = [chunk for chunk in src.split(".where(")[1:]]
    assert where_calls, "expected the summary to filter its counts"
    for chunk in where_calls:
        assert "in_scope" in chunk[:120], f"unscoped .where( in recotap_summary: {chunk[:120]!r}"
    assert "RecotapAccount.company_id.is_not(None)" not in src
    assert "select(func.count()).select_from(scoped)" in src


def test_recotap_summary_excludes_journey_stage_from_its_own_facet_counts(m):
    """The funnel tiles ARE the journey-stage filter, so applying that filter to
    them would collapse the funnel to the selected stage and zero every tile the
    user might switch to."""
    src = inspect.getsource(m.recotap_summary)
    assert "dataclass_replace(filters, journey_stage=None)" in src


def test_recotap_summary_counts_distinct_accounts_not_recotap_rows(m):
    """A company can carry more than one recotap_accounts row, so counting rows
    made a tile promise more accounts than clicking it actually listed."""
    src = inspect.getsource(m.recotap_summary)
    assert "accounts = func.count(func.distinct(RecotapAccount.company_id))" in src
    # Every aggregate the endpoint selects is that distinct-account counter —
    # never a bare func.count() over recotap rows, and never RecotapAccount.id.
    assert "func.count(RecotapAccount" not in src
    assert "select(RecotapAccount.engagement, accounts)" in src
    # `scored` is counted, never summed from the stage tiles (an account with
    # two rows in different stages would otherwise be double-counted).
    assert "scored += " not in src
    assert "select(accounts).where(" in src
    assert "not_scored" in src


def test_recotap_summary_separates_recotap_stages_from_crm_derived_ones(m):
    """The funnel is badged "Powered by Recotap" but the CRM-derived stage used
    to be written over Recotap's in the same column: in prod all 22 accounts in
    the "Customer" tile were CRM-derived while Recotap reported none."""
    src = inspect.getsource(m.recotap_summary)
    assert "stages_crm" in src and "stages_recotap" in src
    # The tiles count the shared effective-stage expression, which is the same
    # one the list filter matches on — otherwise a tile promises a count the
    # filter it opens cannot reproduce.
    assert "recotap_effective_stage_sql()" in src


def test_recotap_summary_splits_not_scored_by_reason(m):
    """One number, 991, read as "Recotap failed to score 991 accounts". In fact
    829 had no Recotap account at all and only 162 were awaiting a score — a
    coverage problem and a latency problem, merged so the bigger one hid."""
    src = inspect.getsource(m.recotap_summary)
    assert "not_in_recotap" in src and "in_recotap_unscored" in src


def test_recotap_summary_reports_accounts_with_no_intent_score(m):
    """Recotap sends rtp_account_score=0 for an unscored account. That used to
    be rendered as "Cold" — 132 of prod's 418 Cold accounts had no signal."""
    src = inspect.getsource(m.recotap_summary)
    assert "no_intent" in src
    # Counted against the distinct-account population, never subtracted from the
    # chips' sum: two rows on one company can land in two chips, so the sum
    # exceeds the account count and the remainder would go negative.
    assert "RecotapAccount.engagement.is_not(None)" in src


def test_recotap_summary_visibility_matches_the_accounts_list(m, admin_user):
    """Admins keep workspace-wide numbers; a rep is narrowed to owned accounts."""
    from sqlalchemy.dialects import postgresql

    rep = SimpleNamespace(id=uuid4(), role="sdr")
    empty = m.CompanySourcingFilters()

    def _where(user):
        stmt = m.build_sourced_companies_stmt(user, empty).order_by(None)
        return str(stmt.compile(dialect=postgresql.dialect()))

    rep_sql = _where(rep)
    assert "companies.assigned_to_id = " in rep_sql and "companies.sdr_id = " in rep_sql
    admin_sql = _where(admin_user)
    assert "companies.assigned_to_id = " not in admin_sql
    assert "companies.deleted_at IS NULL" in admin_sql


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
