import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.rag.chunker import chunk_file, chunk_text, language_for

PY = """import os

X = 1

@decorator
def add(a, b):
    return a + b

class Calc:
    def mul(self, a, b):
        return a * b

    def div(self, a, b):
        return a / b
"""

TS = """import x from "y";
export function foo(a: number) { return a; }
export const bar = (b) => b * 2;
class K { m() { return 1; } }
interface I { a: string }
type T = { z: number };
"""

GO = """package main

import "fmt"

type S struct{ A int }

func (s S) Hi() { fmt.Println("hi") }

func main() {}
"""

JAVA = """package p;
public class A {
  private int x;
  public A() {}
  public int get() { return x; }
}
"""


def summary(chunks):
    return [(c.kind, c.name, c.start_line, c.end_line) for c in chunks]


def test_python_functions_classes_and_module_code():
    assert summary(chunk_file("m.py", PY)) == [
        ("block", None, 1, 4),
        ("function", "add", 5, 7),  # the decorator line belongs to the function
        ("class", "Calc", 9, 14),
    ]


def test_python_decorator_is_part_of_the_function_chunk():
    add = next(c for c in chunk_file("m.py", PY) if c.name == "add")
    assert add.content.startswith("@decorator\ndef add")


def test_oversized_class_is_split_into_its_methods():
    got = summary(chunk_file("m.py", PY, max_chars=60))
    assert ("function", "mul", 10, 11) in got and ("function", "div", 13, 14) in got
    assert not any(k == "class" for k, *_ in got)


def test_typescript_functions_arrow_consts_classes_interfaces_types():
    assert summary(chunk_file("m.ts", TS)) == [
        ("block", None, 1, 1),
        ("function", "foo", 2, 2),
        ("function", "bar", 3, 3),
        ("class", "K", 4, 4),
        ("interface", "I", 5, 5),
        ("type", "T", 6, 6),
    ]


def test_tsx_and_jsx_and_js_are_supported():
    assert language_for("a.tsx") == "tsx" and language_for("a.jsx") == "javascript"
    js = "function a() { return 1 }\nconst b = function () { return 2 }\n"
    assert [c.name for c in chunk_file("a.js", js)] == ["a", "b"]
    tsx = "export const C = () => <div>hi</div>;\n"
    assert chunk_file("c.tsx", tsx)[0].kind == "function"


def test_go_types_methods_and_functions():
    assert summary(chunk_file("m.go", GO)) == [
        ("block", None, 1, 4),
        ("type", "S", 5, 5),
        ("method", "Hi", 7, 7),
        ("function", "main", 9, 9),
    ]


def test_java_class_fits_in_one_chunk_but_splits_into_methods_when_large():
    assert (
        summary(chunk_file("A.java", JAVA)) == [("class", "A", 2, 6), ("block", None, 1, 1)][::-1]
    )
    got = summary(chunk_file("A.java", JAVA, max_chars=50))
    assert ("method", "A", 4, 4) in got and ("method", "get", 5, 5) in got


def test_a_long_function_without_nested_definitions_is_cut_into_windows():
    body = "\n".join(f"    x{i} = {i}" for i in range(100))
    chunks = chunk_file("big.py", f"def big():\n{body}\n", max_chars=300)
    assert len(chunks) > 1 and all(c.kind == "function" and c.name == "big" for c in chunks)
    assert all(len(c.content) <= 320 for c in chunks)


def test_content_matches_the_reported_line_range():
    lines = PY.split("\n")
    for c in chunk_file("m.py", PY):
        assert c.content == "\n".join(lines[c.start_line - 1 : c.end_line])


def test_unknown_extension_uses_line_windows():
    text = "\n".join(f"line {i}" for i in range(200))
    chunks = chunk_file("notes.md", text, max_chars=500)
    assert len(chunks) > 1 and all(c.kind == "block" for c in chunks)
    assert chunks[0].start_line == 1


def test_file_without_extension_and_empty_files():
    assert chunk_file("Makefile", "all:\n\techo hi\n")[0].kind == "block"
    assert chunk_file("empty.py", "") == []
    assert chunk_file("blank.py", "\n\n   \n") == []
    assert chunk_text("x.txt", "") == []


def test_syntax_errors_do_not_crash_and_still_cover_the_file():
    broken = "def ok():\n    return 1\n\ndef bad(:\n    pass\n\ndef also_ok():\n    return 2\n"
    names = [c.name for c in chunk_file("b.py", broken)]
    assert "ok" in names and "also_ok" in names


def test_unicode_content_and_crlf():
    src = "def héllo():\r\n    return 'ünï'\r\n"
    [c] = chunk_file("u.py", src)
    assert c.name == "héllo" and "ünï" in c.content


def test_chunks_are_sorted_and_do_not_overlap():
    chunks = chunk_file("m.py", PY, max_chars=60)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_line < b.start_line


@pytest.mark.parametrize(
    "path,text",
    [("m.py", PY), ("m.ts", TS), ("m.go", GO), ("A.java", JAVA)],
)
@given(max_chars=st.integers(min_value=20, max_value=400))
def test_property_every_nonblank_line_is_covered_exactly_once(path, text, max_chars):
    chunks = chunk_file(path, text, max_chars)
    lines = text.split("\n")
    seen: dict[int, int] = {}
    for c in chunks:
        assert 1 <= c.start_line <= c.end_line <= len(lines)
        for n in range(c.start_line, c.end_line + 1):
            seen[n] = seen.get(n, 0) + 1
    for n, line in enumerate(lines, start=1):
        if line.strip():
            assert seen.get(n) == 1, f"line {n} covered {seen.get(n, 0)} times"
    assert all(v == 1 for v in seen.values())
