from app.github.diff import parse_diff
from app.pipeline.hunks import group_file, group_files, render_line, select_within_budget
from tests.test_diff import MODIFY


def test_render_includes_new_line_numbers_and_markers():
    [f] = parse_diff(MODIFY)
    [g] = group_file(f, 10_000)
    assert "    3 + def add(a, b, c=0):" in g.text
    assert "      - def add(a, b):" in g.text
    assert "    1   import os" in g.text
    assert g.text.startswith("File: app/calc.py\n@@")


def test_small_file_is_one_group_with_valid_lines():
    [f] = parse_diff(MODIFY)
    groups = group_file(f, 10_000)
    assert len(groups) == 1
    assert groups[0].valid_lines == f.commentable_lines and groups[0].added == 3


def big_file(n_lines):
    body = "".join(f"+line number {i}\n" for i in range(1, n_lines + 1))
    return parse_diff(
        f"diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -0,0 +1,{n_lines} @@\n{body}"
    )[0]


def test_oversized_hunk_is_split_and_line_numbers_are_preserved():
    f = big_file(200)
    groups = group_file(f, 600)
    assert len(groups) > 1
    assert all(g.chars <= 800 for g in groups)  # budget plus header slack
    covered = set().union(*(g.valid_lines for g in groups))
    assert covered == set(range(1, 201))
    for g in groups:  # every group only claims lines it actually shows
        for n in g.valid_lines:
            assert f"{n:>5} + line number {n}\n" in g.text + "\n"


def test_split_groups_have_disjoint_line_sets():
    groups = group_file(big_file(200), 600)
    seen: set[int] = set()
    for g in groups:
        assert not (seen & g.valid_lines)
        seen |= g.valid_lines


def test_multiple_small_hunks_are_packed_into_one_group():
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n@@ -50 +50 @@\n-c\n+d\n"
    [f] = parse_diff(text)
    assert len(group_file(f, 10_000)) == 1
    assert len(group_file(f, 60)) == 2


def test_group_files_covers_every_file():
    text = MODIFY + MODIFY.replace("calc", "other")
    groups = group_files(parse_diff(text), 10_000)
    assert [g.path for g in groups] == ["app/calc.py", "app/other.py"]


def test_render_line_deleted_has_no_number():
    [f] = parse_diff(MODIFY)
    dels = [ln for ln in f.hunks[0].lines if ln.kind == "del"]
    assert render_line(dels[0]).strip().startswith("- ")


def test_select_within_budget_prefers_small_groups_and_reports_dropped():
    groups = []
    for name, n in (("big", 100), ("s1", 5), ("s2", 5)):
        f = big_file(n)
        f.path = name
        groups += group_file(f, 100_000)
    budget = sum(g.chars for g in groups if g.path != "big") + 10
    chosen, dropped = select_within_budget(groups, budget)
    assert [g.path for g in chosen] == ["s1", "s2"]  # original order kept
    assert [g.path for g in dropped] == ["big"]


def test_select_within_budget_keeps_everything_when_it_fits():
    groups = group_file(big_file(10), 10_000)
    chosen, dropped = select_within_budget(groups, 10**9)
    assert chosen == groups and dropped == []
