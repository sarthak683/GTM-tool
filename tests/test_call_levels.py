"""L1/L2/L3 call classification (Sales Lifecycle SOP stage 04).

The SOP states two rules, not one ladder: L2 is defined by *group size* and L3
by *seniority*. A lone SVP is L3, not L1. These tests pin that precedence, the
title-matching traps that real prod titles create, and — most importantly — the
honesty requirement: only 3.4% of external attendees in production carry a
title, so the classifier must not report a confident answer it cannot support.
"""
from __future__ import annotations

import pytest

from app.services.call_levels import (
    CALL_LEVELS,
    classify_call_level,
    is_director_band,
    is_svp_or_above,
    normalize_call_level,
)
from app.services.internal_domains import external_attendees


def att(email: str, title: str | None = None, name: str | None = None) -> dict:
    return {"email": email, "title": title, "name": name or email.split("@")[0]}


class TestSeniorityMatching:
    """Titles taken verbatim from production."""

    @pytest.mark.parametrize(
        "title",
        [
            "Senior Vice President/ Delivery Director",
            "EVP, Head of Global Delivery",
            "Chief Delivery Officer Biometrics Services",
            "Chief Customer and Business Officer",
            "President",
            "Founder & CEO",
            "Sr. Vice President, Operations",
        ],
    )
    def test_svp_and_above(self, title):
        assert is_svp_or_above(title) is True
        assert is_director_band(title) is False, "an SVP is not in the Director band"

    @pytest.mark.parametrize(
        "title",
        [
            "AVP, Global Services/Global Delivery",
            "VP, Implementation",
            "Vice President, Delivery",
            "Senior Director - Regional Head Professional Services UKI & MEA",
            "Associate Director Client Services",
            "Head of Professional Services, Americas",
            "Sr. Director of Supply Chain PMO",
        ],
    )
    def test_director_band_is_not_promoted_to_l3(self, title):
        assert is_svp_or_above(title) is False
        assert is_director_band(title) is True

    def test_avp_does_not_read_as_vp(self):
        """`\\bvp\\b` must not match inside "AVP" — an Associate VP is not an
        SVP, and treating it as one would send the AE into brand-vision mode
        for a working-level call."""
        assert is_svp_or_above("AVP, Global Services") is False

    def test_vice_president_does_not_read_as_president(self):
        """The trap that breaks naive matching: "President" is a substring of
        "Vice President", so a plain contains-check promotes every VP to L3."""
        assert is_svp_or_above("Vice President, Implementation") is False
        assert is_svp_or_above("President") is True

    @pytest.mark.parametrize("title", ["Implementation Manager", "Analyst", "", None])
    def test_neither_band(self, title):
        assert is_svp_or_above(title) is False
        assert is_director_band(title) is False


class TestSopRules:
    def test_solo_director_is_l1(self):
        s = classify_call_level([att("a@acme.com", "VP, Implementation")])
        assert s.level == "L1" and s.confidence == "high"

    def test_two_attendees_is_l2(self):
        s = classify_call_level([
            att("a@acme.com", "Director, Delivery"),
            att("b@acme.com", "Implementation Manager"),
        ])
        assert s.level == "L2" and s.confidence == "high"

    def test_svp_beats_group_size_even_alone(self):
        """This is the rule a single ladder would get wrong: one person on the
        invite, but an SVP, so it is L3 and not L1."""
        s = classify_call_level([att("a@acme.com", "Senior Vice President, Delivery")])
        assert s.level == "L3"
        assert s.external_count == 1

    def test_svp_in_a_group_is_l3_not_l2(self):
        s = classify_call_level([
            att("a@acme.com", "Director, Delivery"),
            att("b@acme.com", "Chief Technology Officer"),
            att("c@acme.com", "Implementation Manager"),
        ])
        assert s.level == "L3"
        assert any("Chief Technology Officer" in x for x in s.senior_attendees)

    def test_no_external_attendees_is_not_a_client_call(self):
        s = classify_call_level([])
        assert s.level is None
        assert "internal" in s.rationale.lower()


class TestHonestyAboutUnknownTitles:
    """Only 3.4% of production attendees carry a title. A classifier that hides
    that would send an AE into an exec audience prepared for a working session."""

    def test_group_with_unknown_titles_is_low_confidence(self):
        s = classify_call_level([att("a@acme.com"), att("b@acme.com")])
        assert s.level == "L2"
        assert s.confidence == "low"
        assert "cannot be ruled out" in s.rationale

    def test_group_with_all_titles_known_is_high_confidence(self):
        s = classify_call_level([
            att("a@acme.com", "Director, Delivery"),
            att("b@acme.com", "Implementation Manager"),
        ])
        assert s.confidence == "high"

    def test_solo_unknown_title_is_low_confidence(self):
        """A solo attendee with no title could be an SVP, which would be L3."""
        s = classify_call_level([att("a@acme.com")])
        assert s.level == "L1" and s.confidence == "low"

    def test_partial_titles_still_low_confidence(self):
        s = classify_call_level([
            att("a@acme.com", "Director, Delivery"),
            att("b@acme.com"),
        ])
        assert s.confidence == "low"
        assert s.titles_known == 1 and s.external_count == 2

    def test_a_known_svp_is_high_confidence_even_with_other_titles_missing(self):
        """Once an SVP is confirmed present, the unknowns cannot change the
        answer — L3 is already the ceiling."""
        s = classify_call_level([
            att("a@acme.com", "Chief Operating Officer"),
            att("b@acme.com"),
        ])
        assert s.level == "L3" and s.confidence == "high"


class TestExternalAttendeeFiltering:
    INTERNAL = {"beacon.li"}

    def test_internal_reps_are_not_counted(self):
        """Every call has a Beacon rep on it. Counting them would push every
        genuine 1:1 into "2+ attendees" and misclassify it as L2."""
        attendees = [att("ae@beacon.li"), att("prospect@acme.com", "VP, Delivery")]
        externals = external_attendees(attendees, self.INTERNAL)
        assert len(externals) == 1
        assert classify_call_level(externals).level == "L1"

    def test_attendee_without_an_email_is_dropped_not_assumed_external(self):
        attendees = [att("prospect@acme.com", "VP, Delivery"), {"name": "Unknown Person"}]
        externals = external_attendees(attendees, self.INTERNAL)
        assert len(externals) == 1
        assert classify_call_level(externals).level == "L1"

    def test_all_internal_yields_no_client_call(self):
        externals = external_attendees([att("a@beacon.li"), att("b@beacon.li")], self.INTERNAL)
        assert classify_call_level(externals).level is None


class TestNormalization:
    @pytest.mark.parametrize("raw,expected", [("l2", "L2"), (" L3 ", "L3"), ("L1", "L1")])
    def test_accepts_loose_input(self, raw, expected):
        assert normalize_call_level(raw) == expected

    @pytest.mark.parametrize("raw", ["L4", "", None, "high", "l 2"])
    def test_rejects_anything_else(self, raw):
        assert normalize_call_level(raw) is None

    def test_levels_constant(self):
        assert CALL_LEVELS == ("L1", "L2", "L3")


class TestRationaleIsActionable:
    def test_l1_rationale_carries_the_sop_next_step(self):
        s = classify_call_level([att("a@acme.com", "VP, Implementation")])
        assert "Demo Deep Dive" in s.rationale

    def test_l2_rationale_carries_the_sop_next_step(self):
        s = classify_call_level([
            att("a@acme.com", "Director, Delivery"),
            att("b@acme.com", "Implementation Manager"),
        ])
        assert "deep dive" in s.rationale.lower()

    def test_l3_rationale_names_who_triggered_it(self):
        s = classify_call_level([att("jane@acme.com", "EVP, Global Delivery", name="Jane Doe")])
        assert "Jane Doe" in s.rationale and "EVP" in s.rationale
