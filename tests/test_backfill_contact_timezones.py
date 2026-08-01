from app.models.contact import Contact
from scripts.backfill_contact_timezones import propose_timezone_change


def _contact(**overrides) -> Contact:
    values = {
        "first_name": "Test",
        "last_name": "Prospect",
        "phone": "+40 726 299 914",
        "timezone": None,
    }
    values.update(overrides)
    return Contact(**values)


def test_missing_phone_timezone_is_proposed():
    change, skipped = propose_timezone_change(_contact())

    assert skipped is None
    assert change is not None
    assert change.before is None
    assert change.after == "Europe/Bucharest"
    assert change.reason == "phone_missing"


def test_non_nanp_conflict_can_be_repaired():
    change, skipped = propose_timezone_change(
        _contact(timezone="America/New_York"),
        repair_mismatches=True,
    )

    assert skipped is None
    assert change is not None
    assert change.after == "Europe/Bucharest"


def test_nanp_conflict_is_held_for_manual_review():
    change, skipped = propose_timezone_change(
        _contact(phone="+1 224-216-9014", timezone="America/New_York"),
        repair_mismatches=True,
    )

    assert change is None
    assert skipped == "nanp_mismatch_review_required"


def test_explicit_uploaded_timezone_is_never_overwritten():
    contact = _contact(
        timezone="America/New_York",
        enrichment_data={"raw_row": {"Contact Timezone": "EST"}},
    )
    change, skipped = propose_timezone_change(contact, repair_mismatches=True)

    assert change is None
    assert skipped == "explicit_upload_timezone"


def test_equivalent_calling_zones_are_not_churned():
    change, skipped = propose_timezone_change(
        _contact(phone="+49 162 4273036", timezone="Europe/Paris"),
        repair_mismatches=True,
    )

    assert change is None
    assert skipped == "already_equivalent"


def test_existing_australia_zone_is_preserved_for_mobile_number():
    change, skipped = propose_timezone_change(
        _contact(phone="+61 406 531 521", timezone="Australia/Brisbane"),
        repair_mismatches=True,
    )

    assert change is None
    assert skipped == "australia_zone_already_specific"
