from app.services.us_pod_call_report import _event_tags_html, _event_tags_text, _render_report_html, _render_report_text


def test_tags_first_two_then_plus_n():
    html_out = _event_tags_html(["CS Summit, London", "SaaStr", "Web Summit"])
    assert "CS Summit, London" in html_out and "SaaStr" in html_out
    assert ">+1<" in html_out and 'title="Web Summit"' in html_out
    assert _event_tags_text(["A", "B", "C", "D"]) == "[A, B] +2"


def test_no_tags_renders_nothing_and_escapes():
    assert _event_tags_html([]) == "" and _event_tags_html(None) == ""
    assert _event_tags_text(None) == ""
    assert "<script>" not in _event_tags_html(["<script>x"])


def _report(events):
    return {
        "report_type": "daily", "pod": "US", "timezone": "Asia/Kolkata",
        "report_date": "2026-10-13", "rows": [], "totals": {},
        "call_details": [{
            "rep_name": "Rep One", "company_name": "Acme", "prospect_name": "P Q",
            "designation": "VP", "outcome_label": "Meeting", "meeting_date": "Oct 20",
            "created_at": None, "events": events,
        }],
    }


def test_render_includes_tags_in_meetings_table():
    html_out = _render_report_html(_report(["SaaStr"]))
    text_out = _render_report_text(_report(["SaaStr"]))
    assert "SaaStr" in html_out and "[SaaStr]" in text_out


def test_mql_pill_in_html_and_text():
    assert ">MQL<" in _event_tags_html([], is_mql=True)
    assert _event_tags_text([], True) == "[MQL]"
    assert _event_tags_text(["SaaStr"], True) == "[MQL, SaaStr]"
    assert _event_tags_html([], is_mql=False) == ""


def test_render_includes_mql_in_meetings_table():
    report = _report([])
    report["call_details"][0]["is_marketing_lead"] = True
    assert ">MQL<" in _render_report_html(report)
    assert "[MQL]" in _render_report_text(report)
