"""Recognising a Zippy-tagged email.

Zippy is the single tracking mailbox: reps CC it on their own outbound so the
CRM sees the send without every rep having to connect every mailbox they send
from. Two places need to agree on what "CC'd to Zippy" means — the ingestion
side (``app/tasks/email_sync.py``) and the Emails Out metric
(``app/api/v1/endpoints/analytics.py``) — so the rule lives here once.

"Beacon domains" had previously been redefined in four modules with different
contents (one was missing beaconli.co for months). Import from here instead of
adding a fifth copy.
"""

# Every domain we send from: the primary plus the Instantly cold-outreach
# lookalikes, which reps also send manual outreach from.
BEACON_SENDING_DOMAINS = frozenset({"beacon.li", "beaconli.co", "beaconli.com"})


def is_zippy_address(addr: str | None) -> bool:
    """True for a Zippy tagging address in either shape reps actually use.

    - ``zippy@beacon.li`` — the plain mailbox
    - ``zippy+acme-corp@beacon.li`` — the per-deal alias that binds a thread to
      a deal

    Accepted on any of our sending domains, since a rep working out of
    ``sipra@beaconli.com`` may address Zippy on that domain rather than the
    primary one.
    """
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return False
    local, _, domain = addr.rpartition("@")
    if domain not in BEACON_SENDING_DOMAINS:
        return False
    return local == "zippy" or local.startswith("zippy+")


def any_zippy_address(addresses) -> bool:
    """True if any address in the iterable is a Zippy tagging address."""
    return any(is_zippy_address(a) for a in (addresses or []))


def is_our_sending_address(addr: str | None) -> bool:
    """True when the address is one of OUR outbound identities.

    Guards the Zippy fallback: a *prospect* who hits reply-all on a thread that
    happens to CC Zippy must not have their reply counted as one of our sends.
    """
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return False
    _local, _, domain = addr.rpartition("@")
    return domain in BEACON_SENDING_DOMAINS
