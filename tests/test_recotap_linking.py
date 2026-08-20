"""Joining a Recotap account to the Beacon company it belongs to.

The integration keyed this on the domain string alone, and the two systems
disagree about domains constantly — Recotap holds Ironclad as ``ironcladapp.com``
against the CRM's ``ironclad.com``, Manhattan Associates as ``manh.com`` against
``manhattanassociates.com``. In production that left 116 of 605 Recotap accounts
unlinked and therefore invisible in Account Sourcing, including the single
highest-scoring account in the tenant and the only one at Opportunity.

These pin the two pieces that make the link safe: what counts as the same name,
and what one company's several rows collapse into.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from app.models.recotap import RecotapAccount
from app.services.recotap import (
    _external_company_id,
    _merge_rows,
    normalize_company_name,
)


class TestNormalizeCompanyName:
    def test_case_and_whitespace_are_not_a_difference(self):
        """Recotap sent "Manhattan Associates"; our own push wrote it back
        lowercased. Same account."""
        assert normalize_company_name("Manhattan Associates") == normalize_company_name(
            "  manhattan   associates "
        )

    def test_trailing_punctuation_is_dropped(self):
        assert normalize_company_name("Fabtech Technologies Pvt. Ltd.") == "fabtech technologies pvt. ltd"

    def test_none_and_blank_are_empty(self):
        assert normalize_company_name(None) == ""
        assert normalize_company_name("   ") == ""

    def test_suffixes_are_NOT_stripped(self):
        """Deliberately conservative. "Acme" and "Acme Inc" are different keys,
        because a wrong link puts one account's buying intent on another
        account — worse than leaving it orphaned."""
        assert normalize_company_name("Acme Inc") != normalize_company_name("Acme")


class TestExternalCompanyId:
    def test_valid_uuid_for_a_live_company(self):
        cid = uuid4()
        assert _external_company_id(str(cid), {cid}) == cid

    def test_uuid_of_a_deleted_company_is_rejected(self):
        """Recotap keeps echoing an externalId long after the company is gone;
        resurrecting the link would attach signal to a trashed account."""
        assert _external_company_id(str(uuid4()), {uuid4()}) is None

    @pytest.mark.parametrize("raw", [None, "", "   ", "not-a-uuid", "12345"])
    def test_junk_is_tolerated(self, raw):
        """externalId is free text on Recotap's side — it must never raise."""
        assert _external_company_id(raw, {uuid4()}) is None


def row(**kwargs) -> RecotapAccount:
    base = {"domain": "acme.com", "company_id": uuid4()}
    base.update(kwargs)
    return RecotapAccount(**base)


class TestMergeRows:
    def test_single_row_is_returned_untouched(self):
        r = row(score=10)
        assert _merge_rows([r]) is r

    def test_the_scored_row_supplies_the_score(self):
        """The real prod pair: Recotap's own manh.com carries the intent, while
        the account our push created under our guessed domain carries none.
        Reading either alone loses half the account."""
        merged = _merge_rows([
            row(domain="manhattanassociates.com", score=0, crm_journey_stage="Aware"),
            row(domain="manh.com", score=52, journey_stage="Aware", rtp_aid="rtp-1"),
        ])
        assert merged.score == 52
        assert merged.journey_stage == "Aware"
        assert merged.crm_journey_stage == "Aware"

    def test_engagement_is_rederived_from_the_merged_score(self):
        """Not copied from either row: the stub's score of 0 derives to None and
        52 derives to Warm, and the merged account is Warm."""
        merged = _merge_rows([
            row(domain="a.com", score=0, engagement=None),
            row(domain="b.com", score=52, engagement="Warm"),
        ])
        assert merged.engagement == "Warm"

    def test_most_advanced_stage_wins(self):
        merged = _merge_rows([
            row(domain="a.com", journey_stage="Unaware"),
            row(domain="b.com", journey_stage="Opportunity"),
        ])
        assert merged.journey_stage == "Opportunity"

    def test_intent_subscores_take_the_maximum(self):
        merged = _merge_rows([
            row(domain="a.com", website_intent_score=3, g2_intent_score=None),
            row(domain="b.com", website_intent_score=None, g2_intent_score=40),
        ])
        assert (merged.website_intent_score, merged.g2_intent_score) == (3, 40)

    def test_rtp_aid_survives_from_whichever_row_has_one(self):
        merged = _merge_rows([row(domain="a.com"), row(domain="b.com", rtp_aid="rtp-9")])
        assert merged.rtp_aid == "rtp-9"

    def test_latest_activity_date_wins(self):
        merged = _merge_rows([
            row(domain="a.com", last_account_date=datetime(2026, 1, 1)),
            row(domain="b.com", last_account_date=datetime(2026, 8, 19)),
        ])
        assert merged.last_account_date == datetime(2026, 8, 19)

    def test_a_stageless_pair_stays_stageless(self):
        merged = _merge_rows([row(domain="a.com"), row(domain="b.com")])
        assert merged.journey_stage is None
        assert merged.engagement is None
