import unittest
from datetime import datetime, timedelta

from app.services.deal_flags import (
    FLAG_GREEN,
    FLAG_RED,
    FLAG_YELLOW,
    FORECAST_BEST_CASE,
    FORECAST_COMMIT,
    FORECAST_PIPELINE,
    GREEN_FRESHNESS_DAYS,
    compute_deal_flags,
)


def _qual(levels: dict[str, int], details: dict[str, dict] | None = None) -> dict:
    return {"meddpicc": levels, "meddpicc_details": details or {}}


def _fresh_detail(summary: str = "validated in writing", days_ago: int = 1) -> dict:
    return {
        "summary": summary,
        "updated_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
    }


class DealFlagsTests(unittest.TestCase):
    def test_empty_qualification_is_all_red_pipeline(self) -> None:
        result = compute_deal_flags(None)
        self.assertEqual(result["forecast_category"], FORECAST_PIPELINE)
        self.assertEqual(result["red_count"], 8)
        for flag in result["flags"].values():
            self.assertEqual(flag, FLAG_RED)

    def test_all_confirmed_with_evidence_is_commit(self) -> None:
        levels = {k: 3 for k in [
            "metrics", "economic_buyer", "decision_criteria", "decision_process",
            "paper_process", "identify_pain", "champion", "competition",
        ]}
        details = {k: _fresh_detail() for k in levels}
        result = compute_deal_flags(_qual(levels, details))
        self.assertEqual(result["forecast_category"], FORECAST_COMMIT)
        self.assertEqual(result["green_count"], 8)

    def test_confirmed_without_evidence_downgrades_to_yellow(self) -> None:
        """A level-3 with no notes is a rep claim, not proof."""
        levels = {"metrics": 3}
        result = compute_deal_flags(_qual(levels))
        self.assertEqual(result["flags"]["metrics"], FLAG_YELLOW)

    def test_stale_green_downgrades_to_yellow(self) -> None:
        levels = {"metrics": 3}
        stale = _fresh_detail(days_ago=GREEN_FRESHNESS_DAYS + 5)
        result = compute_deal_flags(_qual(levels, {"metrics": stale}))
        self.assertEqual(result["flags"]["metrics"], FLAG_YELLOW)

    def test_any_red_forces_pipeline_bucket(self) -> None:
        levels = {k: 3 for k in [
            "metrics", "economic_buyer", "decision_criteria", "decision_process",
            "paper_process", "identify_pain", "champion",
        ]}
        levels["competition"] = 0  # one red
        details = {k: _fresh_detail() for k in levels if levels[k] == 3}
        result = compute_deal_flags(_qual(levels, details))
        self.assertEqual(result["forecast_category"], FORECAST_PIPELINE)
        self.assertIn("Competition", result["flag_blockers"])

    def test_mix_of_green_and_yellow_is_best_case(self) -> None:
        levels = {"metrics": 3, "champion": 2, "competition": 1, "economic_buyer": 3}
        details = {"metrics": _fresh_detail(), "economic_buyer": _fresh_detail()}
        # Other 4 fields default to 0 — that would make it pipeline. Fill them in.
        for k in ["decision_criteria", "decision_process", "paper_process", "identify_pain"]:
            levels[k] = 3
            details[k] = _fresh_detail()
        result = compute_deal_flags(_qual(levels, details))
        self.assertEqual(result["forecast_category"], FORECAST_BEST_CASE)
        self.assertGreater(result["yellow_count"], 0)
        self.assertEqual(result["red_count"], 0)

    def test_level_2_is_yellow(self) -> None:
        result = compute_deal_flags(_qual({"champion": 2}))
        self.assertEqual(result["flags"]["champion"], FLAG_YELLOW)


if __name__ == "__main__":
    unittest.main()
