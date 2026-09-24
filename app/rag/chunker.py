"""Split source files into retrievable chunks along function/class boundaries (tree-sitter).

Every line of the file lands in exactly one chunk: definitions become `function`/`class`/... chunks,
everything between them (imports, constants, module code) becomes `block` chunks, and anything
oversized is cut into line windows. Line numbers are 1-based and exact.
"""

from dataclasses import dataclass
from functools import cache
from typing import Any

import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    kind: str
    name: str | None
    content: str


_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
}  # fmt: skip

# node type -> chunk kind, per language
_DEFS: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": {
        "function_declaration": "function", "generator_function_declaration": "function",
        "class_declaration": "class", "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function", "generator_function_declaration": "function",
        "class_declaration": "class", "method_definition": "method",
        "interface_declaration": "interface", "type_alias_declaration": "type",
        "enum_declaration": "enum",
    },
    "go": {
        "function_declaration": "function", "method_declaration": "method",
        "type_declaration": "type",
    },
    "java": {
        "class_declaration": "class", "interface_declaration": "interface",
        "enum_declaration": "enum", "record_declaration": "class",
        "method_declaration": "method", "constructor_declaration": "method",
    },
}  # fmt: skip
_DEFS["tsx"] = _DEFS["typescript"]

_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


@cache
def _parser(lang: str) -> Parser:
    grammar: Any = {
        "python": tree_sitter_python.language,
        "javascript": tree_sitter_javascript.language,
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
        "go": tree_sitter_go.language,
        "java": tree_sitter_java.language,
    }[lang]
    return Parser(Language(grammar()))


def language_for(path: str) -> str | None:
    dot = path.rfind(".")
    return _LANG_BY_EXT.get(path[dot:].lower()) if dot != -1 else None


def _text(node: Node | None) -> str | None:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else None


def _classify(node: Node, lang: str) -> tuple[str, str | None] | None:
    """If `node` is a definition, return (kind, name)."""
    defs = _DEFS[lang]
    t = node.type
    if t in defs:
        name_node = node.child_by_field_name("name")
        if name_node is None and t == "type_declaration":  # go: type_declaration -> type_spec
            spec = next((c for c in node.children if c.type == "type_spec"), None)
            name_node = spec.child_by_field_name("name") if spec else None
        return defs[t], _text(name_node)
    # wrappers that carry the real definition: @decorator / `export ...`
    if t in ("decorated_definition", "export_statement"):
        inner = next((c for c in node.children if _classify(c, lang)), None)
        return _classify(inner, lang) if inner is not None else None
    # const foo = () => {...}
    if t in ("lexical_declaration", "variable_declaration"):
        for decl in node.children:
            value = (
                decl.child_by_field_name("value") if decl.type == "variable_declarator" else None
            )
            if value is not None and value.type in _FUNCTION_VALUES:
                return "function", _text(decl.child_by_field_name("name"))
    return None


def _line_range(node: Node) -> tuple[int, int]:
    start = node.start_point.row + 1
    end = node.end_point.row + (0 if node.end_point.column == 0 else 1)
    return start, max(end, start)


def _windows(lines: list[str], first_line: int, max_chars: int) -> list[tuple[int, int]]:
    """Cut consecutive lines into (start, end) ranges of at most max_chars, at least one line."""
    out: list[tuple[int, int]] = []
    start, size = 0, 0
    for i, ln in enumerate(lines):
        cost = len(ln) + 1
        if i > start and size + cost > max_chars:
            out.append((first_line + start, first_line + i - 1))
            start, size = i, 0
        size += cost
    if lines:
        out.append((first_line + start, first_line + len(lines) - 1))
    return out


def _collect(
    node: Node, lang: str, lines: list[str], max_chars: int, out: list[Chunk], path: str
) -> None:
    for child in node.children:
        found = _classify(child, lang)
        if found is None:
            if child.child_count:
                _collect(child, lang, lines, max_chars, out, path)
            continue
        kind, name = found
        start, end = _line_range(child)
        size = sum(len(x) + 1 for x in lines[start - 1 : end])
        if size <= max_chars:
            out.append(Chunk(path, start, end, kind, name, "\n".join(lines[start - 1 : end])))
            continue
        # Oversized: emit nested definitions (methods of a big class) separately, then let the
        # gap filler cover the remaining header/body lines.
        before = len(out)
        _collect(child, lang, lines, max_chars, out, path)
        if len(out) == before:  # no nested definitions: a long function, cut it into windows
            for s, e in _windows(lines[start - 1 : end], start, max_chars):
                out.append(Chunk(path, s, e, kind, name, "\n".join(lines[s - 1 : e])))


def _fill_gaps(covered: list[Chunk], lines: list[str], max_chars: int, path: str) -> list[Chunk]:
    taken = [False] * (len(lines) + 2)
    for c in covered:
        for n in range(c.start_line, c.end_line + 1):
            taken[n] = True
    result = list(covered)
    n = 1
    while n <= len(lines):
        if taken[n]:
            n += 1
            continue
        m = n
        while m <= len(lines) and not taken[m]:
            m += 1
        run = lines[n - 1 : m - 1]
        if any(x.strip() for x in run):
            for s, e in _windows(run, n, max_chars):
                content = "\n".join(lines[s - 1 : e])
                if content.strip():
                    result.append(Chunk(path, s, e, "block", None, content))
        n = m
    return sorted(result, key=lambda c: (c.start_line, c.end_line))


def chunk_text(path: str, text: str, max_chars: int = 2000) -> list[Chunk]:
    """Language-agnostic fallback: line windows."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [
        Chunk(path, s, e, "block", None, "\n".join(lines[s - 1 : e]))
        for s, e in _windows(lines, 1, max_chars)
        if "\n".join(lines[s - 1 : e]).strip()
    ]


def chunk_file(path: str, text: str, max_chars: int = 2000) -> list[Chunk]:
    lang = language_for(path)
    if lang is None:
        return chunk_text(path, text, max_chars)
    try:
        tree = _parser(lang).parse(text.encode("utf-8"))
    except Exception:  # a parser failure must never stop indexing
        return chunk_text(path, text, max_chars)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    found: list[Chunk] = []
    _collect(tree.root_node, lang, lines, max_chars, found, path)
    return _fill_gaps(found, lines, max_chars, path)
