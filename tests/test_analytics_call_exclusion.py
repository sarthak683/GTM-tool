import unittest
from types import SimpleNamespace
from uuid import UUID

from app.api.v1.endpoints.analytics import _activity_row_is_early_funnel


class ActivityCallExclusionTests(unittest.TestCase):
    def test_excludes_call_for_deal_after_demo_done(self) -> None:
        deal_id = UUID("00000000-0000-0000-0000-000000000001")
        row = SimpleNamespace(deal_id=deal_id, contact_id=None)

        self.assertFalse(
            _activity_row_is_early_funnel(
                row,
                deal_stage_by_id={deal_id: "poc_agreed"},
                contact_company={},
                company_stages={},
                early_funnel_stage_ids={"reprospect", "demo_scheduled", "demo_done"},
            )
        )

    def test_includes_call_for_deal_at_demo_done(self) -> None:
        deal_id = UUID("00000000-0000-0000-0000-000000000002")
        row = SimpleNamespace(deal_id=deal_id, contact_id=None)

        self.assertTrue(
            _activity_row_is_early_funnel(
                row,
                deal_stage_by_id={deal_id: "demo_done"},
                contact_company={},
                company_stages={},
                early_funnel_stage_ids={"reprospect", "demo_scheduled", "demo_done"},
            )
        )

    def test_excludes_call_for_company_with_only_late_stage_deals(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-000000000003")
        company_id = UUID("00000000-0000-0000-0000-000000000004")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertFalse(
            _activity_row_is_early_funnel(
                row,
                deal_stage_by_id={},
                contact_company={contact_id: company_id},
                company_stages={company_id: {"poc_agreed", "poc_wip", "closed_won"}},
                early_funnel_stage_ids={"reprospect", "demo_scheduled", "demo_done"},
            )
        )

    def test_includes_call_for_company_with_early_stage_deals(self) -> None:
        contact_id = UUID("00000000-0000-0000-0000-000000000005")
        company_id = UUID("00000000-0000-0000-0000-000000000006")
        row = SimpleNamespace(deal_id=None, contact_id=contact_id)

        self.assertTrue(
            _activity_row_is_early_funnel(
                row,
                deal_stage_by_id={},
                contact_company={contact_id: company_id},
                company_stages={company_id: {"demo_scheduled", "poc_agreed"}},
                early_funnel_stage_ids={"reprospect", "demo_scheduled", "demo_done"},
            )
        )


if __name__ == "__main__":
    unittest.main()
