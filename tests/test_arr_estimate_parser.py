import pytest

from app.services.icp_intelligence import _parse_arr_estimate as parse


@pytest.mark.parametrize(
    "text, expected",
    [
        # Real cases the old parser read as billions because of a stray "b"
        ("Unknown — likely sub-$5M based on SMB/developer pricing model", 5_000_000),
        ("Not available — likely sub-$1M", 1_000_000),
        ("~$80-100M estimated (public filings)", 80_000_000),
        ("~$800M+ ARR (pre-acquisition public filings)", 800_000_000),
        ("Estimated $50M–$100M ARR based on 650 employees", 50_000_000),
        ("Estimated $50-80M revenue based on scale", 50_000_000),
        ("Estimated $800M–$1B ARR based on take-private valuation", 800_000_000),
        ("~$706M revenue FY2023; ARR not separately disclosed", 706_000_000),
        ("₹700Cr (~$85M ARR)", 85_000_000),
        ("₹6,000+ crore revenue (~$720M USD)", 720_000_000),
        # Genuine billions still work
        ("$18+ billion annual revenue", 18_000_000_000),
        ("~$2B+ (2024 public estimates for Canva overall)", 2_000_000_000),
        ("$1.5B", 1_500_000_000),
        ("$250K", 250_000),
        ("12 million", 12_000_000),
        # Numbers pass through; plain big numbers are trusted
        (1234.0, 1234.0),
        ("5000000", 5_000_000),
        ("$5,000,000", 5_000_000),
    ],
)
def test_parses_amounts(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Unknown",
        "Not publicly disclosed",
        "Not disclosed, trading at 1x ARR multiple",
        "Bootstrapped / Unknown",
        "No funding, ARR, or revenue data found",
    ],
)
def test_no_amount_gives_none(text):
    assert parse(text) is None
