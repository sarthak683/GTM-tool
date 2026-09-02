from decimal import Decimal

from sqlalchemy import Numeric

from app.models.company import Company, CompanyRead


MONEY_FIELDS = (
    "arr_estimate",
    "opp_amount",
    "opp_arr",
    "opp_multiyear_license_fee",
    "opp_service_fee",
)


def test_company_money_columns_use_exact_numeric_storage():
    for field_name in MONEY_FIELDS:
        column_type = Company.__table__.c[field_name].type
        assert isinstance(column_type, Numeric)
        assert column_type.precision == 15
        assert column_type.scale == 2


def test_company_money_round_trip_preserves_decimal_value():
    amount = Decimal("1234567.89")
    company = Company(
        name="Precision Test",
        domain="precision-test.invalid",
        arr_estimate=amount,
        opp_amount=amount,
        opp_arr=amount,
        opp_multiyear_license_fee=amount,
        opp_service_fee=amount,
    )

    read_model = CompanyRead.model_validate(company)

    for field_name in MONEY_FIELDS:
        assert getattr(read_model, field_name) == amount
        assert isinstance(getattr(read_model, field_name), Decimal)

    assert '"opp_amount":1234567.89' in read_model.model_dump_json()
