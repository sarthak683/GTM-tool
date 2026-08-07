import unittest
from types import SimpleNamespace
from uuid import UUID

from app.api.v1.endpoints.analytics import (
    _activity_row_is_early_funnel,
    _advanced_stage_ids,
)

EARLY = {"reprospect", "demo_scheduled", "demo_done"}
ADVANCED = {
    "qualified_lead",
    "poc_agreed",
    "poc_wip",
    "poc_done",
    "commercial_negotiation",
    "msa_review",
    "closed_won",
}

STAGE_SETTINGS = [
    {"id": "reprospect", "group": "active"},
    {"id": "nurture", "group": "closed"},
    {"id": "demo_scheduled", "group": "active"},
    {"id": "demo_done", "group": "active"},
    {"id": "qualified_lead", "group": "active"},
    {"id": "poc_agreed", "group": "active"},
    {"id": "poc_wip", "group": "active"},
    {"id": "poc_done", "group": "active"},
    {"id": "commercial_negotiation", "group": "active"},
    {"id": "msa_review", "group": "active"},
    {"id": "closed_won", "group": "closed"},
    {"id": "backlog", "group": "closed"},
    {"id": "cold", "group": "closed"},
    {"id": "closed_lost", "group": "closed"},
]


def gate(row, *, deal_stage_by_id=None, contact_company=None, company_stages=None):
    return _activity_row_is_early_funnel(
        row,
        deal_stage_by_id=deal_stage_by_id or {},
        contact_company=contact_company or {},
        company_stages=company_stages or {},
        early_funnel_stage_ids=EARLY,
        advanced_stage_ids=ADVANCED,
    )


class AdvancedStageSetTests(unittest.TestCase):
    def test_only_post_demo_active_stages_plus_closed_won(self) -> None:
        self.assertEqual(_advanced_stage_ids(STAGE_SETTINGS), ADVANCED)

    def test_closed_and_parked_stages_are_not_advanced(self) -> None:
        """The bug this whole gate had: `stage_order` drops every closed-group
        stage before slicing, so an allowlist treated `cold` exactly like
        `poc_wip`. These are re-prospecting targets, not progressed accounts."""
        advanced = _advanced_stage_ids(STAGE_SETTINGS)
        for stage in ("cold", "backlog", "nurture", "closed_lost"):
            self.assertNotIn(stage, advanced)

    def test_unknown_stage_ids_are_not_advanced(self) -> None:
        """A blocklist fails open — a stage someone adds later still counts."""
        self.assertNotIn("brand_new_stage", _advanced_stage_ids(STAGE_SETTINGS))


class ActivityCallExclusionTests(unittest.TestCase):
    def test_excludes_call_for_deal_after_demo_done(self) -> None:
        deal_id = UUID("00000000-0000-0000-0000-000000000001")
        row = SimpleNamespace(deal_id=deal_id, contact_id=None)

        self.assertFalse(gate(row, deal_stage_by_id={deal_id: "poc_agreed"}))

    def test_includes_call_for_deal_at_demo_done(self) -> None:
        deal_id = UUID("00000000-0000-0000-0000-000000000002")
        row = SimpleNamespace(deal_id=deal_id, contact_id=None)

        self.assertTrue(gate(row, deal_stage_by_id={deal_id: "demo_done"}))

    def test_includes_call_for_deal_on_a_cold_account(self) -> None:
        """Mahesh, 2026-08-06: 116 of 317 weekly calls disappeared from Sales
        Analytics while the daily call report showed all of them. 113 were on
        `cold`/`backlog`/`nurture` accounts he was re-prospecting."""
        deal_id = UUID("00000000-0000-0000-0000-000000000007")
        row = SimpleNamespace(deal_id=deal_id, contact_id=None)

        for stage in ("cold", "backlog", "nurture", "closed_lost", "not_a_fit"):
            with self.subTest(stage=stage):
                self.assertTrue(gate(row, deal_stage_by_id={deal_id: stage}))

    def test_excludes_call_for_company_with_only_late_stage_deals(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-000000000003")
        company_id = UUID("00000000-0000-0000-0000-000000000004")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertFalse(
            gate(
                row,
                contact_company={contact_id: company_id},
                company_stages={company_id: {"poc_agreed", "poc_wip", "closed_won"}},
            )
        )

    def test_includes_call_for_company_with_early_stage_deals(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-000000000005")
        company_id = UUID("00000000-0000-0000-0000-000000000006")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertTrue(
            gate(
                row,
                contact_company={contact_id: company_id},
                company_stages={company_id: {"demo_scheduled", "poc_agreed"}},
            )
        )

    def test_includes_call_for_company_whose_deals_are_all_cold(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-000000000008")
        company_id = UUID("00000000-0000-0000-0000-000000000009")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertTrue(
            gate(
                row,
                contact_company={contact_id: company_id},
                company_stages={company_id: {"cold", "backlog"}},
            )
        )

    def test_includes_call_when_a_late_account_still_has_something_early(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-00000000000a")
        company_id = UUID("00000000-0000-0000-0000-00000000000b")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertTrue(
            gate(
                row,
                contact_company={contact_id: company_id},
                company_stages={company_id: {"poc_wip", "reprospect"}},
            )
        )

    def test_includes_call_on_a_fresh_prospect_with_no_deal(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-00000000000c")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertTrue(gate(row, contact_company={contact_id: None}))

    def test_includes_call_for_unknown_deal(self) -> None:
        row = SimpleNamespace(
            deal_id=UUID("00000000-0000-0000-0000-00000000000d"), contact_id=None
        )

        self.assertTrue(gate(row))


if __name__ == "__main__":
    unittest.main()
