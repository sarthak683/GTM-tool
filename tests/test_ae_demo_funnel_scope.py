"""Scope of the AE demo funnel.

The AE Leaderboard's demo pills used to require `sdr_id == assigned_to_id`, so an
AE only got credit for demos on deals they had ALSO sourced themselves. Every
demo handed over by an SDR — most of an AE's work — was invisible on their row.

Pravalika, 2026-08-07: ran 4 demos in 7 days, her row showed 1. The two she
raised, Ordway Labs and Command Alkon, were sourced by Mahesh and Jacob.

The SDR leaderboard still credits `deal.sdr_id` for the same transition. That is
not double counting — the two tables measure two different jobs.
"""
import unittest
from uuid import UUID

from sqlalchemy import select

from app.api.v1.endpoints.analytics import _ae_demo_funnel_deal_conditions
from app.models.deal import Deal

REP_ID = UUID("e02a316e-5e62-41d9-98d9-c90448621916")


def _compiled(rep_id=None) -> str:
    stmt = select(Deal.id).where(*_ae_demo_funnel_deal_conditions(rep_id))
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class AeDemoFunnelScopeTests(unittest.TestCase):
    def test_scope_never_constrains_the_sourcing_sdr(self) -> None:
        """The whole defect in one assertion: an AE's demo credit must not
        depend on who sourced the deal."""
        for rep_id in (None, REP_ID):
            with self.subTest(rep_id=rep_id):
                self.assertNotIn("sdr_id", _compiled(rep_id))

    def test_workspace_scope_requires_an_owning_ae(self) -> None:
        sql = _compiled()
        self.assertIn("assigned_to_id IS NOT NULL", sql)

    def test_rep_scope_narrows_to_that_ae(self) -> None:
        sql = _compiled(REP_ID)
        self.assertIn("assigned_to_id =", sql)
        # SQLAlchemy renders UUID literals without dashes.
        self.assertIn(REP_ID.hex, sql.replace("-", ""))

    def test_rep_scope_is_the_workspace_scope_plus_one_condition(self) -> None:
        """Dashboard aggregate and drilldown must stay in lockstep — the pill and
        the list it opens are the same query with one extra predicate."""
        self.assertEqual(
            len(_ae_demo_funnel_deal_conditions(REP_ID)),
            len(_ae_demo_funnel_deal_conditions()) + 1,
        )


if __name__ == "__main__":
    unittest.main()
