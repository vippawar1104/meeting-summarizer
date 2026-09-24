from app.pipeline.findings import Finding
from app.pipeline.post import (
    SummaryInfo,
    build_review_payload,
    build_summary,
    fingerprint,
    format_comment,
    has_review_marker,
    review_marker,
)


def mk(**over):
    base = dict(file="a.py", line=3, severity="high", category="bug",
                message="Loop bound is off by one.", confidence=0.87)  # fmt: skip
    return Finding(**{**base, **over})


def info(**over):
    return SummaryInfo(
        files_reviewed=2, groups_total=3, model="fake/m", prompt_version="v1", **over
    )


def test_single_line_patch_becomes_a_suggestion_block():
    text = format_comment(mk(suggested_patch="for i in range(n + 1):"))
    assert "```suggestion\nfor i in range(n + 1):\n```" in text


def test_multi_line_patch_is_plain_code_never_a_suggestion():
    text = format_comment(mk(suggested_patch="a = 1\nb = 2"))
    assert "```suggestion" not in text and "```\na = 1\nb = 2\n```" in text


def test_no_patch_no_code_block():
    assert "```" not in format_comment(mk())


def test_comment_shows_severity_category_message_confidence_and_fingerprint():
    text = format_comment(mk())
    assert "High" in text and "bug" in text and "off by one" in text
    assert "confidence 87%" in text and f"reviewly:finding:{fingerprint(mk())}" in text


def test_fingerprint_is_stable_and_ignores_line_and_punctuation():
    a = mk(line=3, message="Loop bound is off by one!")
    b = mk(line=40, message="loop bound is off by one")
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_differs_by_file_category_and_message():
    base = fingerprint(mk())
    assert fingerprint(mk(file="b.py")) != base
    assert fingerprint(mk(category="security")) != base
    assert fingerprint(mk(message="Something else entirely")) != base


def test_summary_with_findings_counts_by_severity():
    text = build_summary([mk(), mk(severity="low", line=4)], info(), "sha1")
    assert "**2** issue(s) in 2 file(s)" in text and "1 high" in text and "1 low" in text


def test_summary_without_findings():
    assert "No issues found" in build_summary([], info(), "sha1")


def test_summary_reports_partial_review_and_failures_and_config_problems():
    text = build_summary(
        [],
        info(skipped_for_size=["big.py"], groups_failed=1, config_warning="bad yaml", not_shown=4),
        "sha1",
    )
    assert "Partial review" in text and "`big.py`" in text
    assert "1 of 3 section(s) could not be reviewed" in text
    assert "bad yaml" in text and "4 lower-priority" in text


def test_summary_carries_the_idempotency_marker():
    assert review_marker("sha1") in build_summary([], info(), "sha1")


def test_inline_payload_anchors_comments_to_the_right_side():
    payload = build_review_payload([mk()], "summary", "sha1")
    assert payload["commit_id"] == "sha1" and payload["event"] == "COMMENT"
    [c] = payload["comments"]
    assert (c["path"], c["line"], c["side"]) == ("a.py", 3, "RIGHT")


def test_the_bot_can_never_approve_or_request_changes():
    assert build_review_payload([mk()], "s", "sha")["event"] == "COMMENT"


def test_fallback_payload_moves_findings_into_the_body():
    payload = build_review_payload([mk()], "summary", "sha1", inline=False)
    assert "comments" not in payload and "`a.py:3`" in payload["body"]


def test_has_review_marker():
    reviews = [{"body": None}, {"body": "hi"}, {"body": f"x {review_marker('sha1')}"}]
    assert has_review_marker(reviews, "sha1")
    assert not has_review_marker(reviews, "sha2")
    assert not has_review_marker([], "sha1")
