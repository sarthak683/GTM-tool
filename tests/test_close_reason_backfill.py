"""Structuring historical free-text close reasons — safety first.

This is the only code in the release that rewrites existing production data, so
what is tested here is mostly what it must NOT do: not overwrite prose that
carries commitments, not re-decide rows the enum already answered, not guess at
text it cannot place, and not mutate the JSONB in place (which persists nothing).
"""
from __future__ import annotations

import pytest

from app.services.close_reason_backfill import is_enum_value, propose_close_reason
from app.services.deal_stage_history import CLOSE_REASONS


class TestNewReasonsExist:
    def test_build_in_house_is_its_own_reason(self):
        """Distinct from lost_to_competitor: losing to a vendor and the prospect
        building it themselves are different losses with different responses."""
        assert "built_in_house" in CLOSE_REASONS
        assert "lost_to_competitor" in CLOSE_REASONS

    def test_the_sops_two_rca_exit_points_are_recordable(self):
        assert "poc_failed" in CLOSE_REASONS
        assert "terms_not_agreed" in CLOSE_REASONS


class TestMatchingRealProductionText:
    """Every string below is verbatim from the production `close_reason` column."""

    @pytest.mark.parametrize(
        "text",
        [
            "built internally",
            "They have already build internally and not looking to proceed this us.",
            "Building Internally",
            "Building Intrenaly. 7-8weeks internal team has asked for. Reconnect on 22 sep'26",
            "build vs buy",
            "They are trying to build it in-house. We will revisit this after 6 months.",
        ],
    )
    def test_genuine_build_in_house_losses_are_matched(self, text):
        assert propose_close_reason(text) == "built_in_house"

    @pytest.mark.parametrize(
        "text",
        [
            "No due to Internal issues",
            "they said they dont want to proceed - internal priorities",
        ],
    )
    def test_prospect_side_internal_issues_are_NOT_build_in_house(self, text):
        """An earlier, looser `%intern%` pattern captured both of these. Neither
        is a build-vs-buy loss — requiring the build/built stem excludes them."""
        assert propose_close_reason(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "Parked",
            "No requirement now",
            "Meet with them in October",
            "Catering to SMBs",
            "waiting on the docs. move back when its ready for next steps",
            "",
            None,
        ],
    )
    def test_unplaceable_text_is_left_alone(self, text):
        """Not forced into "other": a wrong reason gets counted, a missing one
        does not."""
        assert propose_close_reason(text) is None

    def test_the_misspelling_still_matches(self):
        """"Building Intrenaly" — the stem match has to survive typos, because
        this is prose a rep typed under time pressure."""
        assert propose_close_reason("Building Intrenaly") == "built_in_house"


class TestEnumDetection:
    def test_recognises_values_the_dropdown_already_wrote(self):
        assert is_enum_value("built_in_house") is True
        assert is_enum_value("other") is True

    def test_free_text_is_not_an_enum_value(self):
        assert is_enum_value("They are trying to build it in-house.") is False
        assert is_enum_value("") is False
        assert is_enum_value(None) is False


class TestBackfillSafetyProperties:
    """Asserted against the source, because these are properties of how the
    function is written rather than of any one input."""

    def test_dry_run_defaults_to_true(self):
        import inspect

        from app.services.close_reason_backfill import backfill_close_reasons

        assert inspect.signature(backfill_close_reasons).parameters["dry_run"].default is True

    def test_builds_a_new_dict_rather_than_mutating_the_jsonb(self):
        """In-place mutation of a plain JSONB column leaves SQLAlchemy's
        attribute history unchanged, so no UPDATE is emitted — the silent no-op
        that cost this codebase 424 Hunter verifications."""
        import inspect

        from app.services.close_reason_backfill import backfill_close_reasons

        src = inspect.getsource(backfill_close_reasons)
        assert "updated = dict(qualification)" in src
        assert "qualification[" not in src.split("if not dry_run:")[-1]

    def test_preserves_the_original_prose(self):
        import inspect

        from app.services.close_reason_backfill import backfill_close_reasons

        src = inspect.getsource(backfill_close_reasons)
        assert 'updated["close_reason_detail"] = current' in src

    def test_never_clobbers_a_detail_a_rep_already_wrote(self):
        import inspect

        from app.services.close_reason_backfill import backfill_close_reasons

        src = inspect.getsource(backfill_close_reasons)
        assert 'if not str(updated.get("close_reason_detail") or "").strip():' in src

    def test_skips_rows_the_enum_already_answered(self):
        import inspect

        from app.services.close_reason_backfill import backfill_close_reasons

        src = inspect.getsource(backfill_close_reasons)
        assert "if is_enum_value(current):" in src
        assert "skipped_already_enum += 1" in src

    def test_reports_what_it_could_not_place(self):
        import inspect

        from app.services.close_reason_backfill import backfill_close_reasons

        src = inspect.getsource(backfill_close_reasons)
        assert '"unmatched": unmatched' in src
