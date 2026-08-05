from app.services.us_pod_call_report import (
    DEFAULT_SALES_REPORT_SETTINGS,
    INDIA_DEFAULT_SALES_REPORT_SETTINGS,
    normalize_sales_report_settings,
)


EXPECTED_DEFAULT_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat"]


def test_sales_report_defaults_exclude_sunday_for_both_pods():
    us_settings = normalize_sales_report_settings(None, defaults=DEFAULT_SALES_REPORT_SETTINGS)
    india_settings = normalize_sales_report_settings(None, defaults=INDIA_DEFAULT_SALES_REPORT_SETTINGS)

    assert us_settings["send_days"] == EXPECTED_DEFAULT_DAYS
    assert india_settings["send_days"] == EXPECTED_DEFAULT_DAYS
    assert "sun" not in us_settings["send_days"]
    assert "sun" not in india_settings["send_days"]


def test_sales_report_send_days_remain_configurable():
    settings = normalize_sales_report_settings(
        {"send_days": ["MONDAY", "wed", "sun"]},
        defaults=DEFAULT_SALES_REPORT_SETTINGS,
    )

    assert settings["send_days"] == ["mon", "wed", "sun"]


def test_invalid_send_days_use_the_selected_pod_defaults():
    custom_defaults = {
        **INDIA_DEFAULT_SALES_REPORT_SETTINGS,
        "send_days": ["tue", "thu"],
        "weekly_report_day": "thu",
    }

    settings = normalize_sales_report_settings(
        {"send_days": ["invalid"], "weekly_report_day": "invalid"},
        defaults=custom_defaults,
    )

    assert settings["send_days"] == ["tue", "thu"]
    assert settings["weekly_report_day"] == "thu"
