from app.services.timezone_infer import infer_timezone, infer_timezone_from_phone


def test_libphonenumber_resolves_us_area_code_correctly():
    assert infer_timezone_from_phone("+1 224-216-9014") == "America/Chicago"


def test_phone_country_codes_cover_eastern_europe():
    assert infer_timezone_from_phone("+40 726 299 914") == "Europe/Bucharest"
    assert infer_timezone_from_phone("+359 88 974 9044") == "Europe/Sofia"


def test_libphonenumber_aliases_are_normalized_for_existing_filters():
    assert infer_timezone_from_phone("+91 99306 19651") == "Asia/Kolkata"


def test_multizone_phone_country_wins_over_us_company_headquarters():
    assert infer_timezone(
        phone="+61 406 531 521",
        company_hq="San Francisco, USA",
        company_region="US",
    ) == "Australia/Sydney"


def test_uk_mobile_uses_conservative_country_default():
    assert infer_timezone_from_phone("+44 75 0006 1581") == "Europe/London"


def test_company_location_remains_fallback_when_phone_is_missing():
    assert infer_timezone(phone=None, company_hq="Berlin, Germany") == "Europe/Berlin"
