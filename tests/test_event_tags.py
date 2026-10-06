from app.services.event_tags import canonicalize, clean_event, merge_events, parse_events


def test_parse_events_splits_on_semicolon_not_comma():
    # Event names contain commas, so only ; | and newlines separate events.
    assert parse_events("CS Summit, London; Virtual RT Oct 15") == ["CS Summit, London", "Virtual RT Oct 15"]
    assert parse_events("A | B\nC") == ["A", "B", "C"]


def test_parse_events_cleans_and_dedupes():
    assert parse_events("  cs   summit ; CS Summit ;; ") == ["cs summit"]
    assert parse_events(None) == []
    assert parse_events("") == []


def test_canonicalize_reuses_existing_spelling_case_insensitively():
    known = {"cs summit, london": "CS Summit, London"}
    assert canonicalize(["cs summit, LONDON"], known) == ["CS Summit, London"]
    # A new spelling is remembered so later rows in the same upload agree.
    assert canonicalize(["Virtual RT Oct 15"], known) == ["Virtual RT Oct 15"]
    assert canonicalize(["virtual rt oct 15"], known) == ["Virtual RT Oct 15"]


def test_merge_events_is_additive_and_case_insensitive():
    assert merge_events(["A"], ["a", "B"]) == ["A", "B"]
    assert merge_events(None, ["A"]) == ["A"]
    assert merge_events(["A", "B"], []) == ["A", "B"]


def test_clean_event():
    assert clean_event("  CS   Summit ") == "CS Summit"
    assert clean_event(None) == ""
