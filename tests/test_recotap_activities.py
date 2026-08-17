"""Beacon activity → Recotap ``POST /sales-activities`` mapping.

Recotap requires `domain`, `ownerEmail` and at least one contact email on every
activity, and answers HTTP 200 whatever happens to the individual items. So an
activity missing a required link does not fail loudly — it comes back as one
more "failed" row indistinguishable from a real error. These tests pin the rule
that such activities are never sent in the first place.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from app.clients.recotap import RecotapClient
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.user import User
from app.services.recotap_activities import build_activity_payload


def make_company(domain="acme.com", name="Acme Corp"):
    return Company(id=uuid4(), name=name, domain=domain)


def make_contact(email="dana@acme.com", company=None):
    return Contact(
        id=uuid4(), email=email, first_name="Dana", last_name="Reid",
        company_id=company.id if company else None,
    )


def make_owner(email="rep@beacon.li", name="Jacob Mathew"):
    return User(id=uuid4(), email=email, name=name, role="sdr")


def make_activity(**kw):
    base = dict(
        id=uuid4(), type="call", medium="call",
        created_at=datetime(2026, 8, 14, 9, 30, 0),
    )
    base.update(kw)
    return Activity(**base)


def build(activity, *, contact=..., company=..., owner=...):
    company = make_company() if company is ... else company
    contact = make_contact(company=company) if contact is ... else contact
    owner = make_owner() if owner is ... else owner
    return build_activity_payload(activity, contact=contact, company=company, owner=owner)


class TestRequiredLinks:
    """All three are required by Recotap; without them the item cannot attach
    to an account, so it is dropped here rather than sent to be rejected."""

    def test_no_company_domain_is_not_sent(self):
        assert build(make_activity(), company=None) is None

    def test_placeholder_domain_is_not_sent(self):
        """"vistex.unknown" can never match a Recotap account."""
        assert build(make_activity(), company=make_company(domain="vistex.unknown")) is None

    def test_no_owner_is_not_sent(self):
        assert build(make_activity(), owner=None) is None

    def test_owner_without_a_real_email_is_not_sent(self):
        assert build(make_activity(), owner=make_owner(email="not-an-email")) is None

    def test_no_contact_is_not_sent(self):
        assert build(make_activity(), contact=None) is None

    def test_contact_without_an_email_is_not_sent(self):
        assert build(make_activity(), contact=make_contact(email="")) is None


class TestActivityTypeFiltering:
    """Recotap accepts only `call` and `email`; anything else returns as
    "skipped", so it is filtered out before the request."""

    def test_calls_map(self):
        assert build(make_activity(type="call", medium="call"))["activityType"] == "call"

    def test_gmail_sends_map_even_with_no_medium(self):
        """Gmail-synced sends carry type="email" and NO medium — see
        metric_definitions.sent_email_filter."""
        payload = build(make_activity(type="email", medium=None))
        assert payload["activityType"] == "email"

    @pytest.mark.parametrize("medium", ["linkedin", "whatsapp", "in_person", "sms"])
    def test_other_channels_are_dropped(self, medium):
        assert build(make_activity(type="note", medium=medium)) is None

    def test_meetings_are_dropped(self):
        assert build(make_activity(type="meeting", medium=None)) is None


class TestPayloadShape:
    def test_external_activity_id_is_the_beacon_uuid(self):
        """It is Recotap's dedup key — a repeat comes back as failed."""
        activity = make_activity()
        assert build(activity)["externalActivityId"] == str(activity.id)

    def test_occurred_at_carries_an_explicit_utc_marker(self):
        assert build(make_activity())["occurredAt"] == "2026-08-14T09:30:00Z"

    def test_domain_is_normalised(self):
        payload = build(make_activity(), company=make_company(domain="https://www.Acme.com/"))
        assert payload["domain"] == "acme.com"

    def test_contact_block_carries_an_email_and_our_id(self):
        contact = make_contact()
        payload = build(make_activity(), contact=contact, company=make_company())
        assert payload["contacts"][0]["email"] == "dana@acme.com"
        assert payload["contacts"][0]["externalContactId"] == str(contact.id)
        assert payload["contacts"][0]["name"] == "Dana Reid"

    def test_call_duration_is_converted_from_seconds_to_minutes(self):
        """We store seconds; Recotap's field is durationMinutes."""
        payload = build(make_activity(call_duration=150))
        assert payload["durationMinutes"] == 2.5

    def test_call_outcome_and_direction(self):
        payload = build(make_activity(call_outcome="answered"))
        assert payload["outcome"] == "answered"
        assert payload["direction"] == "outbound"

    def test_email_subject_is_carried_and_bounded(self):
        payload = build(make_activity(type="email", medium=None, email_subject="x" * 500))
        assert len(payload["subject"]) == 300

    def test_no_call_fields_on_an_email(self):
        payload = build(make_activity(type="email", medium=None))
        assert "direction" not in payload and "durationMinutes" not in payload


class TestBatching:
    def test_client_chunks_at_recotaps_activity_limit(self):
        """50, not the 100 used for deals — a different endpoint with a
        different ceiling."""
        assert RecotapClient.ACTIVITY_BATCH_LIMIT == 50
        assert RecotapClient.DEAL_BATCH_LIMIT == 100

    @pytest.mark.asyncio
    async def test_empty_input_makes_no_request(self, monkeypatch):
        def explode(**kw):
            raise AssertionError("should not open an HTTP client for zero activities")

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", explode)
        body = await RecotapClient(api_key="k").push_sales_activities([])
        assert body["summary"] == {"total": 0, "created": 0, "failed": 0, "skipped": 0}

    @pytest.mark.asyncio
    async def test_batches_of_fifty(self, monkeypatch):
        sizes: list[int] = []

        class FakeResponse:
            def __init__(self, n): self._n = n
            def raise_for_status(self): return None
            def json(self):
                return {"results": [], "summary": {"total": self._n, "created": self._n, "failed": 0, "skipped": 0}}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, headers=None, json=None):
                sizes.append(len(json["activities"]))
                return FakeResponse(len(json["activities"]))

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", lambda **kw: FakeClient())
        acts = [{"externalActivityId": str(i)} for i in range(120)]
        body = await RecotapClient(api_key="k").push_sales_activities(acts)
        assert sizes == [50, 50, 20]
        assert body["summary"]["created"] == 120


class TestDealStageRegistration:
    @pytest.mark.asyncio
    async def test_409_reads_as_already_registered_not_an_error(self, monkeypatch):
        """Recotap rejects the ENTIRE request with 409 once any pipelineId
        exists, with no partial creates. On every run after the first that is
        the expected outcome, so a scheduled caller must not treat it as a
        failure and retry."""
        class FakeResponse:
            status_code = 409
            def raise_for_status(self): raise AssertionError("must not raise on 409")
            def json(self): return {}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, headers=None, json=None): return FakeResponse()

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", lambda **kw: FakeClient())
        out = await RecotapClient(api_key="k").push_deal_stages(
            [{"pipelineId": "deal", "pipelineLabel": "Deal Pipeline", "stages": []}]
        )
        assert out["status"] == "already_registered"

    @pytest.mark.asyncio
    async def test_no_pipelines_makes_no_request(self, monkeypatch):
        def explode(**kw):
            raise AssertionError("should not call Recotap with zero pipelines")

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", explode)
        out = await RecotapClient(api_key="k").push_deal_stages([])
        assert out["status"] == "skipped"


class TestCustomFieldCreation:
    @pytest.mark.asyncio
    async def test_409_returns_none_so_the_caller_re_reads(self, monkeypatch):
        """Creation is explicitly NOT idempotent — 409 means it already exists,
        and since the key is generated by Recotap the caller has to list the
        fields again rather than guess it."""
        class FakeResponse:
            status_code = 409
            def raise_for_status(self): raise AssertionError("must not raise on 409")
            def json(self): return {}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, headers=None, json=None): return FakeResponse()

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", lambda **kw: FakeClient())
        assert await RecotapClient(api_key="k").create_account_custom_field(label="CRM Stage") is None

    @pytest.mark.asyncio
    async def test_selection_types_always_send_options(self, monkeypatch):
        """Recotap rejects a selection field with no options."""
        seen: dict = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return [{"key": "CRM_STAGE_C", "label": "CRM Stage"}]

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, headers=None, json=None):
                seen.update(json)
                return FakeResponse()

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", lambda **kw: FakeClient())
        created = await RecotapClient(api_key="k").create_account_custom_field(
            label="CRM Stage", label_type="singleSelection", options=["POC", "Customer"]
        )
        assert seen["options"] == ["POC", "Customer"]
        assert created["key"] == "CRM_STAGE_C"
