from app.services.timezone_infer import (
    infer_timezone,
    infer_timezone_from_location,
    infer_timezone_from_phone,
)


def test_libphonenumber_resolves_us_area_code_correctly():
    assert infer_timezone_from_phone("+1 224-216-9014") == "America/Chicago"


def test_phone_country_codes_cover_eastern_europe():
    assert infer_timezone_from_phone("+40 726 299 914") == "Europe/Bucharest"
    assert infer_timezone_from_phone("+359 88 974 9044") == "Europe/Sofia"
    assert infer_timezone_from_phone("+371 22 076 104") == "Europe/Riga"


def test_single_zone_country_uses_its_own_locality_name():
    assert infer_timezone_from_phone("+505 8396 1560") == "America/Managua"


def test_libphonenumber_aliases_are_normalized_for_existing_filters():
    assert infer_timezone_from_phone("+91 99306 19651") == "Asia/Kolkata"


def test_multizone_phone_country_wins_over_us_company_headquarters():
    assert infer_timezone(
        phone="+61 406 531 521",
        company_hq="San Francisco, USA",
        company_region="US",
    ) == "Australia/Sydney"


def test_explicit_prospect_location_wins_over_foreign_mobile_number():
    assert infer_timezone(
        phone="+61 452 379 341",
        contact_location="London, England, United Kingdom",
        company_hq="Sydney, Australia",
    ) == "Europe/London"


def test_us_location_uses_state_zone_before_country_default():
    assert infer_timezone_from_location("St. Louis, Missouri, United States") == "America/Chicago"
    assert infer_timezone_from_location("Ogden, Utah, United States") == "America/Denver"
    assert infer_timezone_from_location("San Mateo, California, United States") == "America/Los_Angeles"


def test_ambiguous_multi_country_location_falls_back_to_phone():
    assert infer_timezone_from_location("Israel / France") is None
    assert infer_timezone(
        phone="+972 54-441-3282",
        contact_location="Israel / France",
    ) == "Asia/Jerusalem"


def test_uk_mobile_uses_conservative_country_default():
    assert infer_timezone_from_phone("+44 75 0006 1581") == "Europe/London"


def test_company_location_remains_fallback_when_phone_is_missing():
    assert infer_timezone(phone=None, company_hq="Berlin, Germany") == "Europe/Berlin"
