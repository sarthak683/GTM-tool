"""Bulk account reassignment from an uploaded file.

The rules under test are the ones prior script-based migrations got wrong:
blank cells must not unassign anyone, and rep names must never fuzzy-match.
"""
import unittest
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services.assignment_upload import (
    UNASSIGN_TOKENS,
    CompanyIndex,
    RepResolver,
    _slot_from_cell,
    normalize_domain,
    normalize_name,
)


def user(name, email, uid=None):
    return SimpleNamespace(id=uid or uuid4(), name=name, email=email, is_active=True)


def company(name, domain, ae=None, sdr=None):
    return SimpleNamespace(
        id=uuid4(), name=name, domain=domain, assigned_to_id=ae, sdr_id=sdr
    )


MAHESH = user("Mahesh Pothula", "mahesh@beacon.li")
PRAVALIKA = user("Pravalika Jamalpur", "pravalika@beacon.li")
# Two active users sharing a first name — the collision that mis-assigned 90 of
# 251 accounts when the old importer fell back to first-name matching.
MAHESH_K = user("Mahesh Kumar", "mahesh.kumar@beacon.li")


class NormalizationTests(unittest.TestCase):
    def test_domain_normalization_strips_scheme_www_and_path(self):
        for raw in (
            "https://www.Ordway.com/pricing",
            "http://ordway.com",
            "  WWW.Ordway.com  ",
            "ordway.com/",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_domain(raw), "ordway.com")

    def test_blank_domain_normalizes_to_empty(self):
        self.assertEqual(normalize_domain(None), "")
        self.assertEqual(normalize_domain("   "), "")

    def test_name_normalization_collapses_whitespace_and_case(self):
        self.assertEqual(normalize_name("  Command   ALKON "), "command alkon")


class RepResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = RepResolver([MAHESH, PRAVALIKA, MAHESH_K])

    def test_email_resolves(self):
        found, err = self.resolver.resolve("Mahesh@Beacon.li")
        self.assertIs(found, MAHESH)
        self.assertIsNone(err)

    def test_exact_full_name_resolves(self):
        found, err = self.resolver.resolve("pravalika jamalpur")
        self.assertIs(found, PRAVALIKA)
        self.assertIsNone(err)

    def test_first_name_never_resolves(self):
        """The regression that matters: a bare first name must NOT pick a rep."""
        found, err = self.resolver.resolve("Mahesh")
        self.assertIsNone(found)
        self.assertIn("exact full name", err)

    def test_unknown_email_is_an_error_not_a_silent_skip(self):
        found, err = self.resolver.resolve("nobody@beacon.li")
        self.assertIsNone(found)
        self.assertIn("No active user with email", err)

    def test_empty_cell_is_neither_match_nor_error(self):
        found, err = self.resolver.resolve("   ")
        self.assertIsNone(found)
        self.assertIsNone(err)

    def test_ambiguous_full_name_refuses_to_guess(self):
        resolver = RepResolver([user("Sam Rep", "sam1@beacon.li"), user("Sam Rep", "sam2@beacon.li")])
        found, err = resolver.resolve("Sam Rep")
        self.assertIsNone(found)
        self.assertIn("matches 2 active users", err)


class CompanyMatchingTests(unittest.TestCase):
    def setUp(self):
        self.ordway = company("Ordway Labs", "ordway.com")
        self.alkon = company("Command Alkon", "commandalkon.com")
        self.index = CompanyIndex([self.ordway, self.alkon])

    def test_domain_match_wins(self):
        found, err = self.index.match("https://www.ordway.com", "Totally Different Name")
        self.assertIs(found, self.ordway)
        self.assertIsNone(err)

    def test_name_match_used_when_no_domain(self):
        found, err = self.index.match("", "command alkon")
        self.assertIs(found, self.alkon)
        self.assertIsNone(err)

    def test_unmatched_row_returns_nothing_rather_than_a_guess(self):
        found, err = self.index.match("nope.com", "Nope Inc")
        self.assertIsNone(found)
        self.assertIsNone(err)

    def test_duplicate_names_are_reported_not_guessed(self):
        dupe_a = company("GreytHR - Impl Studio", "greythr.com")
        dupe_b = company("GreytHR - Impl Studio", "")
        index = CompanyIndex([dupe_a, dupe_b])
        found, err = index.match("", "GreytHR - Impl Studio")
        self.assertIsNone(found)
        self.assertIn("add a domain column", err)


class SlotSemanticsTests(unittest.TestCase):
    """A blank cell must leave the slot alone; only an explicit word clears it."""

    def setUp(self):
        self.resolver = RepResolver([MAHESH, PRAVALIKA])

    def test_blank_cell_requests_nothing(self):
        slot, err = _slot_from_cell("", self.resolver)
        self.assertFalse(slot.requested)
        self.assertFalse(slot.unassign)
        self.assertIsNone(err)

    def test_whitespace_cell_requests_nothing(self):
        slot, _ = _slot_from_cell("   ", self.resolver)
        self.assertFalse(slot.requested)

    def test_unassign_keyword_clears_the_slot(self):
        for token in UNASSIGN_TOKENS:
            with self.subTest(token=token):
                slot, err = _slot_from_cell(token.upper(), self.resolver)
                self.assertTrue(slot.requested)
                self.assertTrue(slot.unassign)
                self.assertIsNone(err)

    def test_named_rep_sets_the_slot(self):
        slot, err = _slot_from_cell("mahesh@beacon.li", self.resolver)
        self.assertTrue(slot.requested)
        self.assertFalse(slot.unassign)
        self.assertIs(slot.user, MAHESH)
        self.assertIsNone(err)

    def test_unresolvable_rep_surfaces_an_error_and_assigns_nobody(self):
        slot, err = _slot_from_cell("Mahesh", self.resolver)
        self.assertTrue(slot.requested)
        self.assertIsNone(slot.user)
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
