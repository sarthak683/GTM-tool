"""Deal activity attribution — the rule that decides what counts as a touch.

Regression cover for the production defect found on 2026-08-15: reps log calls
against a *contact*, so only 24 of 13,139 calls in the previous 60 days carried
an ``Activity.deal_id``. Everything asking "when was this deal last worked?"
filtered on that column alone and therefore saw almost none of the real work —
deals went red while a rep was actively calling them, and the deal timeline
rendered with the calls missing.

These tests compile the predicates to SQL rather than executing them: the suite
has no database (see tests/conftest.py), and the defect was in *which rows the
query asks for*, which is visible in the compiled statement.
"""
import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.activity import Activity
from app.services.deal_activity import (
    ENGAGEMENT_ACTIVITY_TYPES,
    deal_activity_condition,
    engagement_only,
)


def compile_where(condition) -> str:
    stmt = select(Activity.id).where(condition)
    return str(stmt.compile(dialect=postgresql.dialect()))


class DealActivityConditionTests(unittest.TestCase):
    def test_matches_activities_carrying_the_deal_id(self):
        sql = compile_where(deal_activity_condition(uuid4()))
        self.assertIn("activities.deal_id", sql)

    def test_also_reaches_activities_through_linked_stakeholders(self):
        """The actual bug: a call logged on a contact linked to the deal.

        Without this arm the condition degenerates to the old behaviour, which
        is what made the deal timeline and deal health near-blind to calls.
        """
        sql = compile_where(deal_activity_condition(uuid4()))
        self.assertIn("deal_contacts", sql)
        self.assertIn("activities.contact_id", sql)

    def test_arms_are_ored_not_anded(self):
        """An activity needs to satisfy either arm, not both.

        ANDing them would match only activities that already carry the deal_id
        *and* belong to a linked contact — strictly fewer rows than before,
        turning a widening fix into a silent narrowing one.
        """
        sql = compile_where(deal_activity_condition(uuid4()))
        self.assertIn("OR", sql.upper())

    def test_stakeholder_arm_is_a_subquery_so_rows_are_not_duplicated(self):
        """A JOIN against deal_contacts would repeat an activity once per link.

        302 production contacts belong to more than one deal, so a join would
        have inflated activity counts on exactly the busiest accounts.
        """
        sql = compile_where(deal_activity_condition(uuid4()))
        self.assertIn("IN (SELECT", sql.upper().replace("\n", " "))


class EngagementTypeTests(unittest.TestCase):
    def test_human_touches_are_included(self):
        for kind in ("call", "email", "linkedin", "whatsapp", "meeting"):
            self.assertIn(kind, ENGAGEMENT_ACTIVITY_TYPES)

    def test_system_bookkeeping_is_excluded(self):
        """field_change / stage_change are written *about* a deal, not on it.

        2,388 of the 8,324 production activities carrying a deal_id were these.
        Counting them as engagement makes an untouched deal look worked.
        """
        for kind in (
            "field_change",
            "stage_change",
            "deal_created",
            "contact_linked",
            "qualification_update",
        ):
            self.assertNotIn(kind, ENGAGEMENT_ACTIVITY_TYPES)

    def test_engagement_filter_names_the_type_column(self):
        sql = compile_where(engagement_only())
        self.assertIn("activities.type", sql)


if __name__ == "__main__":
    unittest.main()
