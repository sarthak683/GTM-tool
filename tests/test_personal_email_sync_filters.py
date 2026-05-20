import unittest

from app.clients.gmail_inbox import EmailMessage
from app.services.personal_email_sync import _is_automated_email


class PersonalEmailSyncFilterTests(unittest.TestCase):
    def test_filters_auto_reply(self) -> None:
        msg = EmailMessage(
            message_id="auto-reply",
            gmail_id="auto-reply",
            subject="Automatic reply: Beacon - Comments on the Due Diligence form",
            from_addr="buyer@example.com",
            to_addrs=["rep@beacon.li"],
        )

        self.assertTrue(_is_automated_email(msg))

    def test_filters_notetaker_notifications(self) -> None:
        msg = EmailMessage(
            message_id="notetaker",
            gmail_id="notetaker",
            subject="Peak3 ROI Model by Beacon",
            from_addr="notetaker@fyxer.com",
            to_addrs=["rep@beacon.li"],
        )

        self.assertTrue(_is_automated_email(msg))

    def test_filters_recording_failure_notifications(self) -> None:
        msg = EmailMessage(
            message_id="recording-failure",
            gmail_id="recording-failure",
            subject="We couldn't record your meeting: Beacon X PeopleStrong",
            from_addr="notetaker-updates@fyxer.com",
            to_addrs=["rep@beacon.li"],
        )

        self.assertTrue(_is_automated_email(msg))


if __name__ == "__main__":
    unittest.main()
