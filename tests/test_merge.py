from hypothesis import given
from hypothesis import strategies as st

from app.pipeline.findings import SEVERITY_WEIGHT, Category, Finding, Severity
from app.pipeline.merge import merge_findings, normalize_path, rank_key

VALID = {"a.py": {1, 2, 3, 4, 5}, "b.py": {10, 11}}


def mk(file="a.py", line=3, sev="high", cat="bug", msg="Loop bound is off by one.", conf=0.9):
    return Finding(file=file, line=line, severity=sev, category=cat, message=msg, confidence=conf)


def merge(findings, **kw):
    return merge_findings(
        findings,
        valid_lines=VALID,
        min_confidence=kw.get("min_confidence", 0.6),
        max_comments=kw.get("max_comments", 25),
    )


def test_line_outside_the_diff_is_dropped_and_counted():
    kept, stats = merge([mk(line=99), mk(line=3)])
    assert [f.line for f in kept] == [3] and stats.line_not_in_diff == 1


def test_unknown_file_is_dropped():
    kept, stats = merge([mk(file="ghost.py")])
    assert kept == [] and stats.unknown_file == 1


def test_low_confidence_is_dropped():
    kept, stats = merge([mk(conf=0.3), mk(line=4, conf=0.8)])
    assert [f.line for f in kept] == [4] and stats.below_confidence == 1


def test_confidence_exactly_at_threshold_is_kept():
    assert len(merge([mk(conf=0.6)])[0]) == 1


def test_paths_with_diff_prefixes_are_normalised():
    kept, _ = merge([mk(file="b/a.py"), mk(file="./b.py", line=10)])
    assert {f.file for f in kept} == {"a.py", "b.py"}


def test_normalize_path_prefers_exact_match_and_rejects_unknown():
    known = {"a/b.py": {1}, "b.py": {1}}
    assert normalize_path("a/b.py", known) == "a/b.py"
    assert normalize_path("nope.py", known) is None


def test_same_line_and_category_are_deduplicated_keeping_the_stronger():
    kept, stats = merge([mk(sev="low", msg="one"), mk(sev="critical", msg="two entirely")])
    assert len(kept) == 1 and kept[0].severity == Severity.CRITICAL and stats.duplicates == 1


def test_nearby_findings_with_similar_text_are_deduplicated():
    a = mk(line=3, msg="Possible SQL injection via string formatting in query")
    b = mk(line=4, msg="SQL injection possible: string formatting used in query", cat="security")
    assert len(merge([a, b])[0]) == 1


def test_distant_or_different_findings_are_both_kept():
    a = mk(line=1, msg="Null dereference when user is missing")
    b = mk(line=5, msg="Null dereference when user is missing")
    c = mk(line=3, msg="Completely unrelated timing issue", cat="performance")
    assert len(merge([a, b, c])[0]) == 3


def test_ranked_by_severity_then_confidence():
    kept, _ = merge(
        [
            mk(line=1, sev="low", conf=0.99, msg="aaa bbb"),
            mk(line=2, sev="critical", conf=0.7, msg="ccc ddd"),
            mk(line=4, sev="critical", conf=0.9, msg="eee fff"),
            mk(line=5, sev="medium", conf=0.8, msg="ggg hhh"),
        ]
    )
    assert [f.line for f in kept] == [4, 2, 5, 1]


def test_cap_keeps_the_most_severe_and_counts_the_rest():
    findings = [
        mk(line=1, sev="low", msg="null dereference when user missing"),
        mk(line=2, sev="low", msg="race condition between writers"),
        mk(line=4, sev="low", msg="unbounded query loads whole table"),
        mk(line=5, sev="critical", msg="hardcoded credential committed"),
    ]
    kept, stats = merge(findings, max_comments=2)
    assert len(kept) == 2 and kept[0].severity == Severity.CRITICAL and stats.over_cap == 2


def test_empty_input():
    kept, stats = merge([])
    assert kept == [] and stats.over_cap == 0


findings_strategy = st.builds(
    Finding,
    file=st.sampled_from(["a.py", "b.py", "b/a.py", "ghost.py"]),
    line=st.integers(1, 20),
    severity=st.sampled_from(list(Severity)),
    category=st.sampled_from(list(Category)),
    message=st.text("abcdef ", min_size=1, max_size=40).filter(lambda s: s.strip()),
    confidence=st.floats(0, 1),
)


@given(st.lists(findings_strategy, max_size=40), st.integers(1, 10))
def test_property_output_only_ever_contains_lines_that_exist_in_the_diff(findings, cap):
    kept, _ = merge_findings(findings, valid_lines=VALID, min_confidence=0.5, max_comments=cap)
    assert len(kept) <= cap
    for f in kept:
        assert f.file in VALID and f.line in VALID[f.file] and f.confidence >= 0.5
    assert kept == sorted(kept, key=rank_key)
    assert all(SEVERITY_WEIGHT[f.severity] >= 1 for f in kept)
