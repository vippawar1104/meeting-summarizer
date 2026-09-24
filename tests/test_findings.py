import json

import pytest

from app.pipeline.findings import Category, Severity, extract_json, parse_findings


def finding(**over):
    base = {
        "file": "a.py",
        "line": 3,
        "severity": "high",
        "category": "bug",
        "message": "Off by one in loop bound.",
        "suggested_patch": None,
        "confidence": 0.9,
    }
    return {**base, **over}


def reply(*items):
    return json.dumps({"findings": list(items)})


def test_valid_reply_parses():
    out = parse_findings(reply(finding()))
    assert out.errors == []
    f = out.findings[0]
    assert (f.file, f.line, f.severity, f.category) == ("a.py", 3, Severity.HIGH, Category.BUG)


def test_empty_findings_is_valid():
    out = parse_findings('{"findings": []}')
    assert out.findings == [] and out.errors == []


def test_markdown_fenced_json_is_accepted():
    out = parse_findings("```json\n" + reply(finding()) + "\n```")
    assert len(out.findings) == 1 and not out.errors


def test_prose_around_json_is_tolerated():
    out = parse_findings("Sure! Here you go:\n" + reply(finding()) + "\nHope that helps.")
    assert len(out.findings) == 1


def test_top_level_list_is_accepted():
    assert len(parse_findings(json.dumps([finding()])).findings) == 1


@pytest.mark.parametrize("text", ["", "no json here", "{broken", "[1, 2"])
def test_unparseable_reply_reports_an_error(text):
    out = parse_findings(text)
    assert out.findings == [] and out.errors


def test_wrong_top_level_shape_reports_an_error():
    out = parse_findings('{"results": []}')
    assert out.findings == [] and out.errors


def test_one_bad_item_does_not_discard_the_good_ones():
    out = parse_findings(reply(finding(), finding(line=0), finding(file="b.py")))
    assert [f.file for f in out.findings] == ["a.py", "b.py"]
    assert len(out.errors) == 1 and "findings[1].line" in out.errors[0]


@pytest.mark.parametrize(
    "over",
    [
        {"line": 0},
        {"line": -4},
        {"category": "vibes"},
        {"severity": "apocalyptic"},
        {"confidence": 1.7 * 100},
        {"confidence": -0.1},
        {"message": ""},
        {"file": ""},
    ],
)
def test_invalid_fields_are_rejected(over):
    out = parse_findings(reply(finding(**over)))
    assert out.findings == [] and len(out.errors) == 1


def test_case_and_whitespace_in_enums_is_normalised():
    f = parse_findings(reply(finding(severity=" HIGH ", category="Security"))).findings[0]
    assert (f.severity, f.category) == (Severity.HIGH, Category.SECURITY)


def test_percent_confidence_is_scaled():
    assert parse_findings(reply(finding(confidence=85))).findings[0].confidence == 0.85


def test_string_line_number_is_coerced():
    assert parse_findings(reply(finding(line="12"))).findings[0].line == 12


def test_unknown_fields_ignored_and_blank_patch_becomes_none():
    f = parse_findings(reply(finding(extra="x", suggested_patch="  "))).findings[0]
    assert f.suggested_patch is None


def test_extract_json_raises_for_non_json():
    with pytest.raises(ValueError):
        extract_json("nothing")
