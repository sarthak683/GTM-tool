import unittest

from app.services.meeting_automation import normalize_pre_meeting_settings


class MeetingPrepWorkflowTests(unittest.TestCase):
    def test_generation_window_never_less_than_send_window(self) -> None:
        settings = normalize_pre_meeting_settings({
            "enabled": True,
            "send_hours_before": 12,
            "generate_hours_before": 6,
            "auto_generate_if_missing": True,
        })

        self.assertEqual(settings["send_hours_before"], 12)
        self.assertEqual(settings["generate_hours_before"], 12)

    def test_generation_window_clamps_to_max(self) -> None:
        settings = normalize_pre_meeting_settings({
            "enabled": True,
            "send_hours_before": 12,
            "generate_hours_before": 999,
            "auto_generate_if_missing": True,
        })

        self.assertEqual(settings["send_hours_before"], 12)
        self.assertEqual(settings["generate_hours_before"], 168)


if __name__ == "__main__":
    unittest.main()
