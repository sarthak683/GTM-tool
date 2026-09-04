"""The prospect board must show every prospect, and stay cheap doing it.

The board used to call the paginated list endpoint with a hard-coded
``limit=500`` and treat the answer as the whole population. Production has
5,935 contacts, so an admin saw 8% of the board — ``cold_strategic`` rendered
320 of its 4,804 cards — with nothing on screen saying the list was partial.
Rows arrive newest-first, so it silently hid the OLDEST prospects: the ones
most likely to be going stale.

Simply raising the ceiling was not possible at ``ContactRead``'s measured 6,414
bytes per row (36 MB for the full set). ``ContactBoardCard`` trades the
enrichment payload — ``enrichment_data`` alone is 4,197 of those bytes — for
the ~20 fields a card and the board's filters actually read.

Pure-logic tests: no database, Redis, or Celery (same philosophy as
``tests/conftest.py``).
"""
from __future__ import annotations

import inspect
from datetime import datetime
from uuid import uuid4

from app.api.v1.endpoints import contacts as contacts_endpoint
from app.models.contact import ContactBoardCard, ContactRead


def _full_contact(**overrides) -> ContactRead:
    base = dict(
        id=uuid4(),
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        updated_at=datetime(2026, 1, 1, 0, 0, 0),
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        title="Head of Ops",
        phone="+1 555 0100",
        linkedin_url="https://linkedin.com/in/ada",
        persona="operator",
        company_id=uuid4(),
        company_name="Example Corp",
        assigned_to_id=uuid4(),
        assigned_to_name="Rep One",
        sdr_id=uuid4(),
        sdr_name="SDR Two",
        outreach_lane="cold_strategic",
        sequence_status="active",
        instantly_status="active",
        tracking_stage="engaged",
        tracking_summary="Opened twice",
        tracking_score=42.0,
        tracking_label="Warm",
        tracking_last_activity_at=datetime(2026, 9, 1, 12, 0, 0),
        # The fat fields the card deliberately drops.
        enrichment_data={"noise": "x" * 4000},
        talking_points=["a" * 400],
        personalization_notes="b" * 160,
        conversation_starter="c" * 120,
    )
    base.update(overrides)
    return ContactRead(**base)


# ── the projection ───────────────────────────────────────────────────────────

def test_card_keeps_every_field_the_board_renders():
    """Each of these is read by a card, the stage derivation, or a filter."""
    card = ContactBoardCard.from_read(_full_contact())
    assert card.first_name == "Ada"
    assert card.last_name == "Lovelace"
    assert card.email == "ada@example.com"
    assert card.title == "Head of Ops"
    assert card.phone == "+1 555 0100"
    assert card.linkedin_url == "https://linkedin.com/in/ada"
    assert card.persona == "operator"
    assert card.company_name == "Example Corp"
    assert card.assigned_to_name == "Rep One"
    assert card.sdr_name == "SDR Two"
    # prospectStage() on the client derives the column from these four.
    assert card.outreach_lane == "cold_strategic"
    assert card.sequence_status == "active"
    assert card.instantly_status == "active"
    assert card.tracking_stage == "engaged"
    assert card.tracking_summary == "Opened twice"
    assert card.tracking_score == 42.0
    assert card.tracking_label == "Warm"


def test_card_carries_the_csv_export_s_last_activity():
    """The per-column CSV export writes a "Last Activity" column from this;
    dropping it from the projection would silently empty that column."""
    card = ContactBoardCard.from_read(_full_contact())
    assert card.tracking_last_activity_at == datetime(2026, 9, 1, 12, 0, 0)


def test_card_drops_the_payload_the_board_never_reads():
    card = ContactBoardCard.from_read(_full_contact())
    for fat in ("enrichment_data", "talking_points", "personalization_notes", "conversation_starter"):
        assert not hasattr(card, fat), f"{fat} is 4 KB of dead weight on a board card"


def test_card_is_an_order_of_magnitude_smaller():
    """The point of the projection: the FULL board must cost less than the
    truncated one used to. Production-shaped rows measure 6,414 -> 641 bytes."""
    contact = _full_contact()
    fat = len(contact.model_dump_json())
    slim = len(ContactBoardCard.from_read(contact).model_dump_json())
    assert slim * 5 < fat, f"projection only saved {fat / slim:.1f}x — check what crept back in"


def test_card_tolerates_a_sparsely_populated_contact():
    """Most production contacts have nothing but a name and an email."""
    card = ContactBoardCard.from_read(
        ContactRead(
            id=uuid4(),
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            updated_at=datetime(2026, 1, 1, 0, 0, 0),
            first_name="X",
            last_name="Y",
            email="x@y.com",
        )
    )
    assert card.email == "x@y.com"
    assert card.outreach_lane is None
    assert card.tracking_score is None


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_board_route_is_declared_before_the_contact_id_route():
    """FastAPI matches in declaration order — "/board" below "/{contact_id}"
    would be parsed as a contact UUID and 422."""
    source = inspect.getsource(contacts_endpoint)
    board_at = source.index('@router.get("/board"')
    by_id_at = source.index('@router.get("/{contact_id}"')
    assert board_at < by_id_at, "/board must stay above /{contact_id}"


def test_board_reuses_the_shared_visibility_and_filter_path():
    """The board must not grow its own query, or it will drift from the list
    and the CSV export the way the export once did."""
    source = inspect.getsource(contacts_endpoint.contact_board)
    assert "can_view_all_prospects" in source, "board must apply the prospect visibility gate"
    assert "list_with_company_name" in source, "board must reuse the shared repository call"
    assert "filters.as_repo_kwargs()" in source, "board must consume the shared ContactFilters"


def test_board_reports_truncation_instead_of_hiding_it():
    """The whole bug was a clipped board that looked complete."""
    response = contacts_endpoint.ContactBoardResponse(
        items=[ContactBoardCard.from_read(_full_contact())],
        total=5935,
        truncated=True,
    )
    assert response.truncated is True
    assert response.total == 5935

    complete = contacts_endpoint.ContactBoardResponse(items=[], total=0, truncated=False)
    assert complete.truncated is False


def test_board_default_ceiling_clears_current_production_volume():
    """5,935 contacts today; the default must not clip a real workspace."""
    sig = inspect.signature(contacts_endpoint.contact_board)
    limit_default = sig.parameters["limit"].default.default
    assert limit_default >= 8000, f"default ceiling {limit_default} is too close to production volume"
