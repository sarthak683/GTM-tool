"""Header-mapping tests for the prospect CSV importer.

Regression for the Apollo-export gap: files name the person column
"Person Linkedin Url" (vs "Company Linkedin Url"), which the exact-match alias
table missed — the URL landed only in enrichment_data.raw_row, so the
Prospecting dashboard's LinkedIn button rendered dead while the detail page's
"Uploaded Prospect Intelligence" panel showed it. Keys here are pre-normalized
(lowercase) exactly as parse_prospect_upload_file hands them to the mapper.
"""
from app.services.account_sourcing import row_to_contact_fields


def _fields(row: dict) -> dict:
    result = row_to_contact_fields(row, {})
    assert result is not None
    return result


def test_apollo_person_linkedin_url_is_mapped():
    row = {
        "first name": "Herman",
        "last name": "Maritz",
        "email": "herman.maritz@syspro.com",
        "person linkedin url": "http://www.linkedin.com/in/herman-maritz",
        "company linkedin url": "http://www.linkedin.com/company/syspro",
    }
    assert _fields(row)["linkedin_url"] == "http://www.linkedin.com/in/herman-maritz"


def test_classic_linkedin_url_header_still_works():
    row = {"first name": "Victoria", "linkedin url": "https://linkedin.com/in/victoriasubbotina"}
    assert _fields(row)["linkedin_url"] == "https://linkedin.com/in/victoriasubbotina"


def test_company_linkedin_never_fills_person_field():
    row = {
        "first name": "Sam",
        "email": "sam@syspro.com",
        "company linkedin url": "http://www.linkedin.com/company/syspro",
    }
    assert _fields(row)["linkedin_url"] is None


def test_fallback_catches_unknown_linkedin_header_variant():
    # e.g. a second de-collided column ("linkedin url 2") no alias knows about
    row = {"first name": "Ana", "linkedin url 2": "https://www.linkedin.com/in/ana-x"}
    assert _fields(row)["linkedin_url"] == "https://www.linkedin.com/in/ana-x"


def test_fallback_rejects_non_url_values():
    # Free-text in a linkedin-ish column must not pollute linkedin_url
    row = {"first name": "Bo", "email": "bo@x.com", "linkedin connected": "yes"}
    assert _fields(row)["linkedin_url"] is None


def test_activity_posts_column_is_ignored_by_fallback():
    row = {
        "first name": "Kim",
        "email": "kim@x.com",
        "linkedin activity posts": "posted about linkedin.com trends last week",
    }
    assert _fields(row)["linkedin_url"] is None
