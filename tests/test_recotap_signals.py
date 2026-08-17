"""Recotap signal derivation — the arithmetic behind the Account Sourcing tiles.

These are pure-function tests over the two derivations the Buying Journey band
reads: engagement from ``rtp_account_score``, and the effective journey stage
from the Recotap/CRM pair. Both had defects that were only visible as a wrong
number on a dashboard, which is the hardest kind to notice.
"""
from __future__ import annotations

import pytest

from app.models.recotap import RecotapAccount
from app.services.recotap import (
    _engagement_for,
    crm_journey_stage,
    effective_journey_stage,
    is_pushable_domain,
    normalize_domain,
)


class TestEngagementFromScore:
    def test_zero_score_is_not_cold(self):
        """Recotap sends 0 for an account it has not scored yet.

        Mapping that to "Cold" put 132 signal-less accounts into the Cold chip in
        production (418 displayed against 286 real) and let the funnel claim
        measured intent it did not have.
        """
        assert _engagement_for(0) is None

    def test_missing_score_is_not_cold(self):
        assert _engagement_for(None) is None

    @pytest.mark.parametrize(
        "score,expected",
        [(1, "Cold"), (44, "Cold"), (45, "Warm"), (71, "Warm"), (72, "Hot"), (100, "Hot")],
    )
    def test_thresholds(self, score, expected):
        assert _engagement_for(score) == expected

    def test_score_above_documented_range_is_hot_not_dropped(self):
        """Recotap documents 0-100 but production carries scores up to 188.
        Those are the hottest accounts we have; they must not fall through."""
        assert _engagement_for(188) == "Hot"

    def test_negative_score_has_no_engagement(self):
        assert _engagement_for(-5) is None


class TestEffectiveJourneyStage:
    def test_crm_stage_wins_over_recotap(self):
        """A live deal is direct evidence of where the account is; Recotap's
        stage is inferred from ad/web/G2 intent."""
        row = RecotapAccount(domain="acme.com", journey_stage="Unaware", crm_journey_stage="Customer")
        assert effective_journey_stage(row) == ("Customer", "crm")

    def test_recotap_stage_used_when_no_deal(self):
        row = RecotapAccount(domain="acme.com", journey_stage="Aware", crm_journey_stage=None)
        assert effective_journey_stage(row) == ("Aware", "recotap")

    def test_source_is_reported_so_the_ui_can_stop_crediting_recotap_for_crm_data(self):
        """The funnel carries a "Powered by Recotap" badge. Before the split,
        all 22 accounts in its "Customer" tile were CRM-derived while Recotap
        itself reported zero Customers."""
        row = RecotapAccount(domain="acme.com", journey_stage=None, crm_journey_stage="Opportunity")
        stage, source = effective_journey_stage(row)
        assert (stage, source) == ("Opportunity", "crm")

    def test_no_stage_at_all(self):
        row = RecotapAccount(domain="acme.com")
        assert effective_journey_stage(row) == (None, None)

    def test_empty_string_is_not_a_stage(self):
        row = RecotapAccount(domain="acme.com", journey_stage="", crm_journey_stage="")
        assert effective_journey_stage(row) == (None, None)


class TestCrmJourneyStageMapping:
    def test_most_advanced_stage_wins(self):
        assert crm_journey_stage(["demo_scheduled", "poc_wip", "qualified_lead"]) == "Consideration"

    def test_closed_won_is_customer(self):
        assert crm_journey_stage(["demo_done", "closed_won"]) == "Customer"

    def test_terminal_stages_map_to_nothing(self):
        """closed_lost / not_a_fit / churned are not journey positions — an
        account that lost is not further along than one that never engaged."""
        assert crm_journey_stage(["closed_lost", "not_a_fit", "churned"]) is None

    def test_no_deals(self):
        assert crm_journey_stage([]) is None


class TestPushableDomainGuard:
    @pytest.mark.parametrize(
        "domain",
        ["vistex.unknown", "peak3.unknown", "charles-river-development.unknown", "98364117736", "foo.local"],
    )
    def test_placeholder_domains_are_rejected(self, domain):
        """sync_crm_journey used to create recotap_accounts rows for these,
        producing five rows in prod that double-counted their company in the
        funnel under a domain Recotap can never match."""
        assert is_pushable_domain(domain) is False

    @pytest.mark.parametrize("domain", ["crd.com", "vistex.com", "hexalog.in", "sub.example.co.uk"])
    def test_real_domains_pass(self, domain):
        assert is_pushable_domain(domain) is True

    def test_normalization_strips_scheme_www_and_path(self):
        assert normalize_domain("https://www.Acme.com/pricing") == "acme.com"
