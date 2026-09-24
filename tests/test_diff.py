from app.github.diff import build_diff_from_files, parse_diff

MODIFY = """\
diff --git a/app/calc.py b/app/calc.py
index 111..222 100644
--- a/app/calc.py
+++ b/app/calc.py
@@ -1,5 +1,6 @@
 import os
 
-def add(a, b):
-    return a + b
+def add(a, b, c=0):
+    total = a + b
+    return total + c
 
 print(add(1, 2))
"""


def test_modified_file_line_numbers():
    [f] = parse_diff(MODIFY)
    assert (f.path, f.status) == ("app/calc.py", "modified")
    [h] = f.hunks
    assert (h.old_start, h.old_len, h.new_start, h.new_len) == (1, 5, 1, 6)
    added = [(ln.new_no, ln.content) for ln in h.lines if ln.kind == "add"]
    assert added == [
        (3, "def add(a, b, c=0):"),
        (4, "    total = a + b"),
        (5, "    return total + c"),
    ]
    deleted = [(ln.old_no, ln.content) for ln in h.lines if ln.kind == "del"]
    assert deleted == [(3, "def add(a, b):"), (4, "    return a + b")]


def test_commentable_lines_are_added_and_context_but_never_deleted():
    [f] = parse_diff(MODIFY)
    assert f.commentable_lines == {1, 2, 3, 4, 5, 6}
    assert (f.added, f.deleted) == (3, 2)


def test_context_lines_track_both_counters():
    [f] = parse_diff(MODIFY)
    ctx = [ln for ln in f.hunks[0].lines if ln.kind == "ctx"]
    assert [(c.old_no, c.new_no) for c in ctx] == [(1, 1), (2, 2), (5, 6)]


NEW_FILE = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..abc
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
"""


def test_new_file():
    [f] = parse_diff(NEW_FILE)
    assert (f.path, f.status, f.old_path) == ("src/new.py", "added", "src/new.py")
    assert f.commentable_lines == {1, 2}


DELETED = """\
diff --git a/old.py b/old.py
deleted file mode 100644
index abc..0000000
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a = 1
-b = 2
"""


def test_deleted_file_has_no_commentable_lines():
    [f] = parse_diff(DELETED)
    assert (f.path, f.status) == ("old.py", "deleted")
    assert f.commentable_lines == set()


RENAMED = """\
diff --git a/a/old_name.py b/a/new_name.py
similarity index 90%
rename from a/old_name.py
rename to a/new_name.py
index 1..2 100644
--- a/a/old_name.py
+++ b/a/new_name.py
@@ -1,2 +1,2 @@
 keep
-old
+new
"""


def test_rename_uses_new_path_and_remembers_old():
    [f] = parse_diff(RENAMED)
    assert (f.path, f.old_path, f.status) == ("a/new_name.py", "a/old_name.py", "renamed")
    assert 2 in f.commentable_lines


def test_pure_rename_without_hunks():
    text = "diff --git a/x.py b/y.py\nsimilarity index 100%\nrename from x.py\nrename to y.py\n"
    [f] = parse_diff(text)
    assert f.status == "renamed" and f.hunks == []


def test_binary_file_flagged():
    text = (
        "diff --git a/i.png b/i.png\nindex 1..2 100644\nBinary files a/i.png and b/i.png differ\n"
    )
    [f] = parse_diff(text)
    assert f.binary and f.hunks == []


def test_removed_line_starting_with_dashes_is_not_mistaken_for_a_file_header():
    text = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,3 +1,3 @@
 title
--- old rule
+++ new rule
 end
"""
    [f] = parse_diff(text)
    kinds = [(ln.kind, ln.content) for ln in f.hunks[0].lines]
    assert kinds == [
        ("ctx", "title"),
        ("del", "-- old rule"),
        ("add", "++ new rule"),
        ("ctx", "end"),
    ]
    assert len(parse_diff(text)) == 1


def test_no_newline_marker_is_ignored():
    text = """\
diff --git a/f b/f
--- a/f
+++ b/f
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""
    [f] = parse_diff(text)
    assert [ln.kind for ln in f.hunks[0].lines] == ["del", "add"]


def test_hunk_header_without_lengths_defaults_to_one():
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -3 +3 @@\n-a\n+b\n"
    [f] = parse_diff(text)
    h = f.hunks[0]
    assert (h.old_len, h.new_len) == (1, 1)
    assert f.commentable_lines == {3}


def test_multiple_hunks_and_files():
    text = MODIFY + NEW_FILE + DELETED
    files = parse_diff(text)
    assert [f.path for f in files] == ["app/calc.py", "src/new.py", "old.py"]


def test_two_hunks_in_one_file():
    text = """\
diff --git a/f b/f
--- a/f
+++ b/f
@@ -1,2 +1,2 @@
 a
-b
+B
@@ -10,2 +10,3 @@ def fn():
 x
+y
 z
"""
    [f] = parse_diff(text)
    assert len(f.hunks) == 2
    assert f.hunks[1].header.endswith("def fn():")
    assert f.commentable_lines == {1, 2, 10, 11, 12}


def test_blank_context_line_stripped_of_its_leading_space():
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-c\n+C\n"
    [f] = parse_diff(text)
    assert [ln.kind for ln in f.hunks[0].lines] == ["ctx", "ctx", "del", "add"]
    assert f.commentable_lines == {1, 2, 3}


def test_quoted_path_with_special_characters():
    text = 'diff --git "a/sp\\303\\251cial.py" "b/sp\\303\\251cial.py"\n--- "a/sp\\303\\251cial.py"\n+++ "b/sp\\303\\251cial.py"\n@@ -1 +1 @@\n-a\n+b\n'  # noqa: E501
    [f] = parse_diff(text)
    assert f.path == "spécial.py"


def test_crlf_headers_are_tolerated():
    text = MODIFY.replace("\n", "\r\n")
    [f] = parse_diff(text)
    assert f.path == "app/calc.py"


def test_empty_and_garbage_input():
    assert parse_diff("") == []
    assert parse_diff("not a diff at all\n") == []


def test_build_diff_from_files_roundtrips_through_the_parser():
    payload = [
        {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y"},
        {"filename": "n.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+z"},
        {"filename": "gone.py", "status": "removed", "patch": "@@ -1 +0,0 @@\n-q"},
        {"filename": "b.png", "status": "modified"},
        {"filename": "new.py", "previous_filename": "old.py", "status": "renamed",
         "patch": "@@ -1 +1 @@\n-a\n+b"},
    ]  # fmt: skip
    files = {f.path: f for f in parse_diff(build_diff_from_files(payload))}
    assert files["a.py"].commentable_lines == {1}
    assert files["n.py"].status == "added"
    assert files["gone.py"].status == "deleted"
    assert files["b.png"].binary
    assert files["new.py"].old_path == "old.py"
