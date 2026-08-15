"""_normalize_beacon_sender must tolerate a missing sender.

instantly_sync resolves a lead's sender as
``lead.get("email_account") or lead.get("from_email") or None`` and passes the
result straight in. Leads legitimately carry neither, and the None then hit
``if "@" not in addr`` — a TypeError that aborted that contact's lead sync on
every 15-minute poll, permanently. Production logged 71 such failures.
"""
import unittest

from app.services.personal_email_sync import _normalize_beacon_sender


class NormaliseBeaconSenderTests(unittest.TestCase):
    def test_none_is_returned_unchanged_instead_of_raising(self):
        self.assertIsNone(_normalize_beacon_sender(None))

    def test_empty_string_is_returned_unchanged(self):
        self.assertEqual(_normalize_beacon_sender(""), "")

    def test_value_without_an_at_sign_is_passed_through(self):
        self.assertEqual(_normalize_beacon_sender("not-an-address"), "not-an-address")

    def test_alternate_beacon_domains_fold_to_the_primary_one(self):
        self.assertEqual(_normalize_beacon_sender("mahesh@beaconli.co"), "mahesh@beacon.li")
        self.assertEqual(_normalize_beacon_sender("mahesh@beaconli.com"), "mahesh@beacon.li")

    def test_primary_domain_is_left_alone(self):
        self.assertEqual(_normalize_beacon_sender("annie@beacon.li"), "annie@beacon.li")

    def test_external_senders_are_never_rewritten(self):
        self.assertEqual(_normalize_beacon_sender("buyer@acme.com"), "buyer@acme.com")


if __name__ == "__main__":
    unittest.main()
