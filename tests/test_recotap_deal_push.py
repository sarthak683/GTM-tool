"""Beacon deal -> Recotap ``POST /deals`` payload mapping and batching.

The mapping is where a push goes quietly wrong: Recotap answers HTTP 200 even
when every item in the batch was rejected, so a malformed field surfaces as a
number that never moves rather than as an error. These tests pin the contract
from docs.recotap.com/api-reference/deals/push-deals field by field.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.clients.recotap import RecotapClient
from app.models.company import Company
from app.models.deal import Deal
from app.models.user import User
from app.services.recotap_deals import build_deal_payload, payload_hash

STAGE_LABELS = {
    "demo_done": "DEMO DONE",
    "closed_won": "CLOSED WON",
    "commercial_negotiation": "COMMERCIAL NEGOTIATION",
}
CLOSED_STAGES = {"closed_won", "closed_lost", "not_a_fit"}


def make_deal(**kwargs) -> Deal:
    base = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "name": "Acme Corp - Enterprise Plan",
        "pipeline_type": "deal",
        "stage": "demo_done",
        "value": Decimal("75000.00"),
        "created_at": datetime(2026, 1, 15, 9, 30, 0),
        "close_date_est": date(2026, 6, 30),
    }
    base.update(kwargs)
    return Deal(**base)


def build(deal: Deal, *, company=None, owner=None, currency="USD") -> dict:
    return build_deal_payload(
        deal,
        company=company,
        owner=owner,
        stage_labels=STAGE_LABELS,
        closed_stage_ids=CLOSED_STAGES,
        currency=currency,
    )


class TestRequiredFields:
    def test_external_deal_id_is_the_beacon_uuid(self):
        """Recotap upserts on externalDealId. It has to be stable across renames
        and stage moves, which a deal name is not."""
        deal = make_deal()
        assert build(deal)["externalDealId"] == "11111111-1111-1111-1111-111111111111"

    def test_name_is_sent(self):
        assert build(make_deal())["name"] == "Acme Corp - Enterprise Plan"

    def test_unnamed_deal_still_produces_a_name(self):
        """`name` is required on their side — an empty one fails the item."""
        payload = build(make_deal(name=""))
        assert payload["name"].startswith("Deal ")


class TestOptionalFieldsAreOmittedNotNulled:
    def test_missing_value_omits_amount(self):
        """Sending null would blank an amount Recotap already holds."""
        assert "amount" not in build(make_deal(value=None))

    def test_missing_owner_omits_all_owner_fields(self):
        payload = build(make_deal(), owner=None)
        assert not {"ownerName", "ownerEmail", "ownerId"} & payload.keys()

    def test_no_company_omits_associated_accounts(self):
        assert "associatedAccounts" not in build(make_deal(), company=None)

    def test_empty_currency_omits_the_field(self):
        assert "dealCurrencyCode" not in build(make_deal(), currency="")


class TestTypesAndFormats:
    def test_amount_is_a_json_serialisable_number(self):
        """`value` is a Decimal, which json.dumps refuses outright."""
        amount = build(make_deal())["amount"]
        assert isinstance(amount, float) and amount == 75000.0

    def test_dates_carry_an_explicit_utc_marker(self):
        """Every datetime in this DB is naive UTC. Sending it bare would let
        Recotap apply whatever zone their server runs in."""
        payload = build(make_deal())
        assert payload["startDate"] == "2026-01-15T09:30:00Z"

    def test_date_only_fields_widen_to_midnight_utc(self):
        assert build(make_deal())["closedDate"] == "2026-06-30T00:00:00Z"

    def test_invalid_owner_email_is_dropped_but_name_survives(self):
        """Recotap validates the address and fails the whole item on a bad one."""
        owner = User(id=uuid4(), email="not-an-email", name="Jane Smith")
        payload = build(make_deal(), owner=owner)
        assert "ownerEmail" not in payload
        assert payload["ownerName"] == "Jane Smith"


class TestStageAndPipeline:
    def test_stage_id_and_label_both_sent(self):
        payload = build(make_deal(stage="commercial_negotiation"))
        assert payload["stageId"] == "commercial_negotiation"
        assert payload["stageLabel"] == "COMMERCIAL NEGOTIATION"

    def test_stage_created_in_settings_after_the_label_map_falls_back_to_its_id(self):
        """Settings can mint new stages (prod has `new_stage_18`). Dropping the
        label entirely is worse than echoing the id."""
        payload = build(make_deal(stage="new_stage_18"))
        assert payload["stageLabel"] == "new_stage_18"

    def test_pipeline_is_reported(self):
        payload = build(make_deal())
        assert payload["pipelineId"] == "deal"
        assert payload["pipelineLabel"] == "Deal Pipeline"


class TestClosedDate:
    def test_closed_deal_uses_actual_close_not_the_estimate(self):
        """Dating won revenue by the rep's old estimate misstates the period
        Recotap attributes it to."""
        deal = make_deal(
            stage="closed_won",
            stage_entered_at=datetime(2026, 5, 2, 14, 0, 0),
            close_date_est=date(2026, 6, 30),
        )
        assert build(deal)["closedDate"] == "2026-05-02T14:00:00Z"

    def test_closed_deal_without_stage_history_falls_back_to_the_estimate(self):
        deal = make_deal(stage="closed_won", stage_entered_at=None, close_date_est=date(2026, 6, 30))
        assert build(deal)["closedDate"] == "2026-06-30T00:00:00Z"

    def test_open_deal_uses_the_estimate(self):
        deal = make_deal(stage="demo_done", stage_entered_at=datetime(2026, 5, 2, 14, 0, 0))
        assert build(deal)["closedDate"] == "2026-06-30T00:00:00Z"


class TestAssociatedAccounts:
    def test_real_domain_is_sent_for_matching(self):
        company = Company(id=uuid4(), name="Acme Corp", domain="https://www.acme.com/")
        account = build(make_deal(), company=company)["associatedAccounts"][0]
        assert account["domain"] == "acme.com"
        assert account["name"] == "Acme Corp"
        assert account["externalId"] == str(company.id)

    def test_placeholder_domain_is_withheld_so_the_deal_stays_unlinked(self):
        """`vistex.unknown` can never match a Recotap account. Their docs define
        an unmatched deal as created-unlinked, which beats attaching it to junk."""
        company = Company(id=uuid4(), name="Vistex", domain="vistex.unknown")
        account = build(make_deal(), company=company)["associatedAccounts"][0]
        assert "domain" not in account
        assert account["externalId"] == str(company.id)


class TestChangeDetection:
    def test_hash_is_stable_across_key_ordering(self):
        a = {"externalDealId": "x", "name": "n", "amount": 1.0}
        b = {"amount": 1.0, "name": "n", "externalDealId": "x"}
        assert payload_hash(a) == payload_hash(b)

    def test_hash_changes_when_any_field_changes(self):
        base = build(make_deal())
        moved = build(make_deal(stage="closed_won"))
        assert payload_hash(base) != payload_hash(moved)

    def test_hash_covers_the_stage_label_not_just_the_id(self):
        """A label renamed in Settings must re-push: Recotap stores the label."""
        deal = make_deal()
        a = build_deal_payload(
            deal, company=None, owner=None,
            stage_labels={"demo_done": "DEMO DONE"}, closed_stage_ids=CLOSED_STAGES, currency="USD",
        )
        b = build_deal_payload(
            deal, company=None, owner=None,
            stage_labels={"demo_done": "Demo Complete"}, closed_stage_ids=CLOSED_STAGES, currency="USD",
        )
        assert payload_hash(a) != payload_hash(b)


class TestBatching:
    @pytest.mark.asyncio
    async def test_client_chunks_at_the_documented_limit(self, monkeypatch):
        """Recotap rejects a request carrying more than 100 deals. 689 live deals
        in prod means the caller must never be the one to remember that."""
        assert RecotapClient.DEAL_BATCH_LIMIT == 100

        sent_batches: list[int] = []

        class FakeResponse:
            def __init__(self, n): self._n = n
            def raise_for_status(self): return None
            def json(self):
                return {
                    "results": [{"externalDealId": str(i), "status": "upserted"} for i in range(self._n)],
                    "summary": {"total": self._n, "upserted": self._n, "failed": 0},
                }

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, headers=None, json=None):
                n = len(json["deals"])
                sent_batches.append(n)
                return FakeResponse(n)

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", lambda **kw: FakeClient())

        client = RecotapClient(api_key="test-key")
        deals = [{"externalDealId": str(i), "name": f"d{i}"} for i in range(250)]
        body = await client.push_deals(deals)

        assert sent_batches == [100, 100, 50]
        assert body["summary"]["total"] == 250
        assert body["summary"]["upserted"] == 250

    @pytest.mark.asyncio
    async def test_one_failed_batch_does_not_strand_the_others(self, monkeypatch):
        calls = {"n": 0}

        class FakeResponse:
            def __init__(self, n): self._n = n
            def raise_for_status(self): return None
            def json(self):
                return {
                    "results": [{"externalDealId": "ok", "status": "upserted"}],
                    "summary": {"total": self._n, "upserted": self._n, "failed": 0},
                }

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, headers=None, json=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("connection reset")
                return FakeResponse(len(json["deals"]))

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", lambda **kw: FakeClient())

        client = RecotapClient(api_key="test-key")
        body = await client.push_deals([{"externalDealId": str(i), "name": "d"} for i in range(150)])

        assert calls["n"] == 2
        assert body["summary"]["failed"] == 100
        assert body["summary"]["upserted"] == 50

    @pytest.mark.asyncio
    async def test_empty_input_makes_no_request(self, monkeypatch):
        def explode(**kw):
            raise AssertionError("should not open an HTTP client for zero deals")

        monkeypatch.setattr("app.clients.recotap.httpx.AsyncClient", explode)
        body = await RecotapClient(api_key="k").push_deals([])
        assert body["summary"] == {"total": 0, "upserted": 0, "failed": 0}
