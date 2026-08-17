import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from app.services import meeting_automation
from app.services.meeting_automation import (
    normalize_pre_meeting_settings,
    run_due_pre_meeting_intel_once,
)
from app.tasks.job_health_signals import _skip_reason


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


class _StubSettingsRow:
    def __init__(self, cfg: dict) -> None:
        self.pre_meeting_automation_settings = cfg


class DisabledAutomationReportsSkipTests(unittest.TestCase):
    """A switched-off automation must report a *skip*, not a plain success.

    job_health recognises a deliberate no-op only via an explicit "status" key
    (app.tasks.job_health_signals._skip_reason); a bare counters dict advances
    last_effective_at. That is how production showed this job "effective" at
    2026-08-17 08:55 while the newest brief actually emailed was 42 days old,
    with the workspace config sitting at {"enabled": false} the whole time.
    """

    def _run_with_config(self, cfg: dict) -> dict:
        @asynccontextmanager
        async def _fake_task_session():
            yield object()

        async def _fake_get_or_create_settings(_session):
            return _StubSettingsRow(cfg)

        with (
            patch.object(meeting_automation, "task_session", _fake_task_session),
            patch.object(
                meeting_automation,
                "_get_or_create_settings",
                _fake_get_or_create_settings,
            ),
        ):
            return asyncio.run(run_due_pre_meeting_intel_once())

    def test_disabled_automation_is_reported_as_a_skip(self) -> None:
        result = self._run_with_config({"enabled": False})

        self.assertEqual(_skip_reason(result), "automation_disabled")

    def test_disabled_automation_still_returns_the_counter_keys(self) -> None:
        """The admin "Run now" endpoint returns this dict straight to the UI."""
        result = self._run_with_config({"enabled": False})

        for key in ("checked", "generated", "emailed", "skipped"):
            self.assertEqual(result[key], 0)

    def test_outside_daily_send_window_is_reported_as_a_skip(self) -> None:
        # send_time 00:00 with a 1h window: any run outside 00:00-01:00 local
        # does no work. Pin the clock into the middle of the day so the
        # assertion cannot flap with wall-clock time.
        cfg = {
            "enabled": True,
            "send_mode": "daily_time",
            "send_time": "00:00",
            "timezone": "UTC",
        }

        real_datetime = meeting_automation.datetime

        class _FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 8, 17, 12, 0, tzinfo=tz)

        with patch.object(meeting_automation, "datetime", _FixedDatetime):
            result = self._run_with_config(cfg)

        self.assertEqual(_skip_reason(result), "outside_daily_send_window")


if __name__ == "__main__":
    unittest.main()
