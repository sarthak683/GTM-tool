"""job_health must tell "ran and worked" apart from "ran and did nothing".

On 2026-08-15 the production System Health panel showed all 14 scheduled jobs
green while tl;dv had imported no meeting since April, no pre-meeting brief had
been sent all month, and personal inbox sync had been skipping every user since
the previous evening. None of those jobs were failing — each ran on schedule and
returned without error, having done nothing. The panel had no field that could
express the difference, so the first signal was a rep asking why their email
had stopped syncing.
"""
import unittest

from app.tasks.job_health_signals import _skip_reason


class SkipDetectionTests(unittest.TestCase):
    def test_real_work_has_no_skip_reason(self):
        self.assertIsNone(_skip_reason({"processed": 12, "created": 3}))

    def test_non_dict_return_is_not_a_skip(self):
        for retval in (None, "ok", 42, ["a"]):
            self.assertIsNone(_skip_reason(retval))

    def test_skipped_status_reports_its_reason(self):
        """The exact shape personal_email_sync returns when the workspace is in
        zippy-only mode — the one that froze every inbox at 08-14 19:43."""
        self.assertEqual(
            _skip_reason({"queued": 0, "status": "skipped", "reason": "zippy_only_email_sync"}),
            "zippy_only_email_sync",
        )

    def test_gmail_not_connected_is_a_skip(self):
        self.assertEqual(
            _skip_reason({"status": "skipped", "reason": "gmail not connected"}),
            "gmail not connected",
        )

    def test_disabled_and_throttled_also_count_as_skips(self):
        self.assertEqual(_skip_reason({"status": "disabled"}), "disabled")
        self.assertEqual(
            _skip_reason({"status": "throttled", "next_run_in_minutes": 5}),
            "throttled",
        )

    def test_status_falls_back_to_itself_when_no_reason_given(self):
        self.assertEqual(_skip_reason({"status": "skipped"}), "skipped")

    def test_status_matching_is_case_and_whitespace_tolerant(self):
        self.assertEqual(_skip_reason({"status": " Skipped "}), "skipped")

    def test_a_zero_count_result_is_not_a_skip(self):
        """A job that legitimately found nothing to do still did its work.

        pre_meeting_brief returns {"checked": 0, ...} on a weekend with no
        meetings in the window. That is a real run, not a skip — only an
        explicit status marks a deliberate no-op.
        """
        self.assertIsNone(
            _skip_reason({"checked": 0, "generated": 0, "emailed": 0, "skipped": 0})
        )


if __name__ == "__main__":
    unittest.main()
