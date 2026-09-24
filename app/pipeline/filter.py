import fnmatch
import re
from dataclasses import dataclass, field

from app.github.diff import FileDiff

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock",
    "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock", "gradle.lockfile", "bun.lockb",
    "mix.lock", "pubspec.lock",
}  # fmt: skip
GENERATED_GLOBS = [
    "*.min.js", "*.min.css", "*.map", "*.snap", "*.pb.go", "*_pb2.py", "*_pb2_grpc.py",
    "*.generated.*", "*.g.dart", "*.designer.cs",
]  # fmt: skip
VENDORED_DIRS = ("node_modules/", "vendor/", "dist/", "build/", ".next/", "__pycache__/")
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tar", ".woff",
    ".woff2", ".ttf", ".eot", ".mp4", ".mov", ".so", ".dylib", ".exe", ".jar", ".class",
}  # fmt: skip
GENERATED_MARKERS = re.compile(r"@generated|DO NOT EDIT|Code generated .* DO NOT EDIT", re.I)


@dataclass
class FilterResult:
    kept: list[FileDiff] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # path -> reason


def path_skip_reason(path: str, ignore_globs: list[str]) -> str | None:
    """Path-only exclusions, shared by the diff filter and the repo indexer."""
    name = path.rsplit("/", 1)[-1]
    if name in LOCKFILES:
        return "lock file"
    if any(path.startswith(d) or f"/{d}" in f"/{path}" for d in VENDORED_DIRS):
        return "vendored or build output"
    if any(fnmatch.fnmatch(name, g) for g in GENERATED_GLOBS):
        return "generated file"
    if any(path.lower().endswith(e) for e in BINARY_EXT):
        return "binary file"
    if any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(name, g) for g in ignore_globs):
        return "ignored by .reviewly.yml"
    return None


def skip_reason(f: FileDiff, ignore_globs: list[str]) -> str | None:
    if f.status == "deleted":
        return "deleted file"
    if f.binary:
        return "binary file"
    reason = path_skip_reason(f.path, ignore_globs)
    if reason:
        return reason
    head = [ln.content for h in f.hunks for ln in h.lines if ln.kind == "add"][:5]
    if any(GENERATED_MARKERS.search(ln) for ln in head):
        return "generated file"
    if not f.hunks:
        return "no textual changes"
    return None


def filter_files(files: list[FileDiff], ignore_globs: list[str] | None = None) -> FilterResult:
    result = FilterResult()
    for f in files:
        reason = skip_reason(f, ignore_globs or [])
        if reason:
            result.skipped[f.path] = reason
        else:
            result.kept.append(f)
    return result
