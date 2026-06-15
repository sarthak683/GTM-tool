import unittest

from app.services.activity_signal_classifier import classify_activity_text, detect_latest_intent_from_segments


class ActivitySignalClassifierTests(unittest.TestCase):
    def test_negated_issue_does_not_create_blocker(self) -> None:
        signal = classify_activity_text(
            "Shahruk, No issues on my end, look forward to the discussion. Thanks."
        )
        self.assertEqual(signal.blocker, "negated")
        self.assertIsNone(signal.stage_cue)

    def test_poc_prep_does_not_look_like_poc_done(self) -> None:
        signal = classify_activity_text(
            "We sent the POC video and client requirement sample for them to prepare on their end. "
            "The POC hasn't started yet."
        )
        self.assertIsNone(signal.stage_cue)

    def test_explicit_poc_completion_is_detected(self) -> None:
        signal = classify_activity_text(
            "The POC is complete and the success criteria were met. Let's schedule the commercial review."
        )
        self.assertEqual(signal.stage_cue, "poc_done")

    def test_explicit_poc_alignment_is_detected(self) -> None:
        signal = classify_activity_text(
            "We're aligned to move forward with a POC next week."
        )
        self.assertEqual(signal.stage_cue, "poc_agreed")

    def test_latest_thread_intent_uses_contextual_classifier(self) -> None:
        intent = detect_latest_intent_from_segments(
            [
                "We discussed the POC on the call.",
                "We sent the POC video and sample requirement for them to prepare on their end. The POC hasn't started yet.",
            ]
        )
        self.assertIsNone(intent)


if __name__ == "__main__":
    unittest.main()
