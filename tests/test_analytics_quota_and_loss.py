"""Pure-logic tests for the sales-dashboard quota payload and the win/loss
reason bucketing.

No database, Redis, or app lifespan is touched: ``_build_quota_state`` reads a
plain analytics-settings dict and ``_loss_reason_key`` normalizes strings, so
both are tested as deterministic helpers (same pattern as
``test_analytics_window.py``).
"""
import unittest

from app.api.v1.endpoints.analytics import (
    LOSS_REASON_KEYS,
    _build_quota_state,
    _loss_reason_key,
)


class BuildQuotaStateTests(unittest.TestCase):
    def test_unconfigured_when_no_targets_exist(self) -> None:
        for settings in (None, {}, {"weekly_targets": {}, "monthly_targets": {}}):
            quota = _build_quota_state(settings)
            self.assertFalse(quota.configured)
            self.assertEqual(quota.title, "Quota setup required")
            self.assertIsNone(quota.weekly_targets)
            self.assertIsNone(quota.team_weekly_targets)

    def test_unconfigured_when_all_targets_are_zero(self) -> None:
        quota = _build_quota_state({"weekly_targets": {"sdr": {"calls_connected": 0}}})
        self.assertFalse(quota.configured)

    def test_configured_exposes_maps_and_team_sums(self) -> None:
        quota = _build_quota_state(
            {
                "weekly_targets": {
                    "sdr": {"calls_connected": 200, "demos_booked": 5},
                    "ae": {"calls_connected": 100, "demos_done": 3},
                },
                "monthly_targets": {"sdr": {"calls_connected": 800}},
            }
        )
        self.assertTrue(quota.configured)
        self.assertEqual(quota.weekly_targets["sdr"]["calls_connected"], 200.0)
        # Team target per metric = sum of that metric across every key.
        self.assertEqual(quota.team_weekly_targets["calls_connected"], 300.0)
        self.assertEqual(quota.team_weekly_targets["demos_booked"], 5.0)
        self.assertEqual(quota.team_weekly_targets["demos_done"], 3.0)
        self.assertEqual(quota.team_monthly_targets["calls_connected"], 800.0)

    def test_non_numeric_and_malformed_entries_are_dropped(self) -> None:
        quota = _build_quota_state(
            {
                "weekly_targets": {
                    "sdr": {"calls_connected": "150", "notes": "n/a"},
                    "bogus": "not-a-dict",
                },
                "monthly_targets": "not-a-dict",
            }
        )
        # "150" coerces; "n/a" and the malformed branches are skipped silently.
        self.assertTrue(quota.configured)
        self.assertEqual(quota.weekly_targets, {"sdr": {"calls_connected": 150.0}})
        self.assertEqual(quota.monthly_targets, {})


class LossReasonKeyTests(unittest.TestCase):
    def test_enum_values_pass_through(self) -> None:
        for reason in LOSS_REASON_KEYS:
            self.assertEqual(_loss_reason_key(reason, None), reason)

    def test_stage_history_reason_wins_over_qualification(self) -> None:
        self.assertEqual(
            _loss_reason_key("pricing", {"close_reason": "timing"}), "pricing"
        )

    def test_falls_back_to_qualification_close_reason(self) -> None:
        self.assertEqual(_loss_reason_key(None, {"close_reason": "budget"}), "budget")
        self.assertEqual(_loss_reason_key("  ", {"close_reason": "Timing"}), "timing")

    def test_unknown_or_missing_reasons_bucket_as_unspecified(self) -> None:
        self.assertEqual(_loss_reason_key(None, None), "unspecified")
        self.assertEqual(_loss_reason_key("", {}), "unspecified")
        self.assertEqual(_loss_reason_key("they ghosted us", None), "unspecified")
        self.assertEqual(_loss_reason_key(None, {"close_reason": "free text"}), "unspecified")
        self.assertEqual(_loss_reason_key(None, "not-a-dict"), "unspecified")


if __name__ == "__main__":
    unittest.main()
