"""`recommended_outreach_lane` must hold a routing token, never LLM prose.

The ICP enrichment writer used to assign ``recommended_outreach_strategy`` —
free prose describing "who to contact first, what to say, which channels" —
straight into ``company.recommended_outreach_lane``, an indexed short column
that every consumer matches by EQUALITY:

* ``_playbook_for_lane``-style branches (``elif lane == "cold_strategic"``)
* the ``instantly_ready`` gate (``recommended_lane in {...}``)
* the Account Sourcing lane filter's option list
* the prospect board's columns, via the company -> contact lane copy

A prose lane matches none of them, so it silently disabled routing while
adding sentence-long junk options to the filter. Production carried 19 such
companies and 36 such contacts.

Pure-logic tests: no database, Redis, or Celery (same philosophy as
``tests/conftest.py``).
"""
from __future__ import annotations

import inspect
import re
from types import SimpleNamespace

from app.services.account_sourcing import OUTREACH_LANES, _build_company_outreach_lane


PROSE = (
    "Lead with Dhiman Chakraborty (Head of Implementation) focusing on scaling "
    "challenges across geographies, then expand to CCO for strategic conversation"
)


def test_lane_vocabulary_is_the_classifier_s_full_range():
    """Every lane the classifier can emit must be in the shared vocabulary.

    Guards the two from drifting apart — the constant exists so that writers
    outside this module can validate without duplicating the token list.
    """
    source = inspect.getsource(_build_company_outreach_lane)
    returned = set(re.findall(r'return "([a-z_]+)"', source))
    assert returned, "classifier returns no literal lanes — did it get rewritten?"
    assert returned <= OUTREACH_LANES, f"classifier emits lanes outside the vocabulary: {returned - OUTREACH_LANES}"


def test_classifier_output_is_always_a_valid_lane():
    """Spot-check the branches, including the catch-all fallback."""
    cases = [
        # (kwargs, expected lane)
        (dict(connectors=[{"strength": 4}], next_steps="", recommended_strategy="", ownership_stage="", analyst={}), "warm_intro"),
        (dict(connectors=[], next_steps="invite them to the summit", recommended_strategy="", ownership_stage="", analyst={}), "event_follow_up"),
        (dict(connectors=[], next_steps="", recommended_strategy="brief the CEO", ownership_stage="", analyst={}), "cold_strategic"),
        (dict(connectors=[], next_steps="", recommended_strategy="", ownership_stage="PE-backed", analyst={}), "cold_operator"),
        (dict(connectors=[], next_steps="", recommended_strategy="", ownership_stage="", analyst={"fit_type": "both"}), "cold_operator"),
        # Catch-all: nothing matches.
        (dict(connectors=[], next_steps="", recommended_strategy="", ownership_stage="", analyst={}), "cold_strategic"),
    ]
    for kwargs, expected in cases:
        lane = _build_company_outreach_lane(**kwargs)
        assert lane == expected, f"{kwargs} -> {lane}, expected {expected}"
        assert lane in OUTREACH_LANES


def test_icp_writer_rejects_prose_and_keeps_the_existing_lane():
    """The ICP path must not clobber a real lane with a strategy sentence."""
    apply_fn = _icp_lane_assignment()

    company = SimpleNamespace(recommended_outreach_lane="cold_operator")
    apply_fn(company, {"recommended_outreach_strategy": PROSE})
    assert company.recommended_outreach_lane == "cold_operator", (
        "prose overwrote a valid lane — the production bug this guards"
    )


def test_icp_writer_still_accepts_a_genuine_lane_token():
    apply_fn = _icp_lane_assignment()

    company = SimpleNamespace(recommended_outreach_lane=None)
    apply_fn(company, {"recommended_outreach_strategy": "Cold_Strategic"})
    assert company.recommended_outreach_lane == "cold_strategic", "case-insensitive lane token should be adopted"


def test_icp_writer_ignores_empty_and_missing_strategy():
    apply_fn = _icp_lane_assignment()

    for icp in ({}, {"recommended_outreach_strategy": None}, {"recommended_outreach_strategy": "   "}):
        company = SimpleNamespace(recommended_outreach_lane="cold_strategic")
        apply_fn(company, icp)
        assert company.recommended_outreach_lane == "cold_strategic"


# ── helpers ──────────────────────────────────────────────────────────────────


def _icp_lane_assignment():
    """Return the guard as a callable, mirroring the two icp_intelligence sites.

    Both sites are the same three lines. Rather than importing the enormous
    enrichment coroutines (which would need a DB and live API clients), pin the
    exact predicate they now share, and assert below that both call sites still
    contain it verbatim.
    """
    def apply(company, icp):
        strategy = str(icp.get("recommended_outreach_strategy") or "").strip().lower()
        if strategy in OUTREACH_LANES:
            company.recommended_outreach_lane = strategy

    return apply


def test_both_icp_call_sites_use_the_guard():
    """The helper above is only meaningful if the real writers match it."""
    import app.services.icp_intelligence as icp_module

    source = inspect.getsource(icp_module)
    guarded = source.count("if strategy in OUTREACH_LANES:")
    assert guarded == 2, f"expected both ICP writers to guard the lane, found {guarded}"
    assert "company.recommended_outreach_lane = str(icp[" not in source, (
        "an unguarded prose assignment to recommended_outreach_lane came back"
    )
