"""Meetings that belong to no single account must not be auto-attributed to one.

Beacon's own board reviews were auto-linked to a client deal (IQVIA 2) and then
dragged 103 unrelated emails onto it, including the company's Series A
correspondence — readable by everyone, since the pipeline is workspace-wide.

The mechanism is counter-intuitive and worth pinning: linking is domain-first,
but the domain matcher only answers when attendees share EXACTLY ONE external
domain. A five-domain board review therefore resolves to *no* company, which is
precisely the state in which the "deal's company must match the domain" guard
cannot fire — so the contact-derived deal went unchallenged and the meeting then
adopted that deal's company. Ambiguity made linking more permissive, not less.

The threshold is measured, not guessed: across 1,443 production meetings, 1,294
had one external domain and 63 had two (a customer plus their advisor, investor
or note-taker). Only 8 ever reached three, and every auto-linked one of those
was this bug.
"""
from __future__ import annotations

import pytest

from app.services.tldv_sync import (
    MULTI_PARTY_DOMAIN_THRESHOLD,
    is_multi_party_event,
)


class TestMultiPartyDetection:
    def test_one_external_domain_is_a_normal_account_meeting(self):
        assert is_multi_party_event(["acme.com"]) is False

    def test_no_external_domains_is_not_multi_party(self):
        """An all-internal meeting is handled by is_internal_only, not here."""
        assert is_multi_party_event([]) is False

    def test_two_domains_stay_linkable(self):
        """The customer plus one outside party is the single most common shape
        of a real deal meeting — EQT sitting in on Peak3, Treelife on the Zellis
        MSA review, a Gong note-taker. 63 production meetings look like this and
        all of them are legitimate, so the threshold must not catch them."""
        assert is_multi_party_event(["peak3.com", "eqtpartners.com"]) is False

    def test_three_domains_is_an_event(self):
        assert is_multi_party_event(["a.com", "b.com", "c.com"]) is True

    def test_the_actual_board_meeting_shape(self):
        """Beacon's 'Board Meeting + Apr 26 MIS review' — six outside companies,
        none of them the customer whose deal it was attached to."""
        board = [
            "sorininvestments.com", "unicornivc.com", "atheravp.com",
            "jafcoasia.com", "jif.capital", "aandabassociates.com",
        ]
        assert is_multi_party_event(board) is True

    def test_a_webinar_is_an_event(self):
        """Prod carries two 40+ domain sessions (an AWS accelerator, a Stripe
        virtual session). They were already unlinked by luck; now by rule."""
        assert is_multi_party_event([f"attendee{i}.com" for i in range(42)]) is True

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_below_threshold_never_blocks(self, n):
        assert is_multi_party_event([f"d{i}.com" for i in range(n)]) is False

    @pytest.mark.parametrize("n", [3, 4, 10])
    def test_at_or_above_threshold_always_blocks(self, n):
        assert is_multi_party_event([f"d{i}.com" for i in range(n)]) is True

    def test_threshold_is_three(self):
        """Pinned deliberately: lowering it to 2 would unlink 12 real meetings
        that are currently attached to the correct deal."""
        assert MULTI_PARTY_DOMAIN_THRESHOLD == 3
