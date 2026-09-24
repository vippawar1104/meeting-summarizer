import asyncio
import base64  # noqa: F401  (kept for callers that decode blobs themselves)
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.core.config import Settings
from app.github.client import GitHubClient, GitHubNotFound
from app.pipeline.filter import path_skip_reason
from app.queue.errors import PermanentError
from app.rag.chunker import chunk_file, language_for
from app.rag.embeddings import Embedder
from app.rag.store import ChunkStore
from app.safety.redact import redact_text

log = structlog.get_logger()

# Beyond the tree-sitter languages, plain-text chunking is still useful for these.
TEXT_EXTENSIONS = {
    ".rb", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".kt", ".swift", ".scala",
    ".sql", ".sh", ".yml", ".yaml", ".toml", ".md",
}  # fmt: skip


@dataclass
class IndexReport:
    head_sha: str
    files_indexed: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    chunks: int = 0
    truncated: bool = False
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _indexable(path: str) -> bool:
    dot = path.rfind(".")
    ext = path[dot:].lower() if dot != -1 else ""
    return language_for(path) is not None or ext in TEXT_EXTENSIONS


def _decode(data: bytes) -> str | None:
    if b"\x00" in data[:8000]:
        return None  # binary
    try:
        return data.decode("utf-8").replace("\x00", "")
    except UnicodeDecodeError:
        return None


def embedding_text(path: str, kind: str, name: str | None, content: str) -> str:
    return f"{path}\n{kind} {name or ''}\n{content}"


class RepoIndexer:
    """Keeps a repo's code index in sync with a commit. Idempotent: keyed by git blob SHA, so only
    files whose content changed are fetched, chunked and embedded."""

    def __init__(
        self, github: GitHubClient, embedder: Embedder, store: ChunkStore, settings: Settings
    ) -> None:
        self._gh, self._embedder, self._store, self._s = github, embedder, store, settings

    async def index_repo(self, inst: int, repo: str, sha: str | None = None) -> IndexReport:
        try:
            if sha is None:
                info = await self._gh.get_repo(inst, repo)
                sha = await self._gh.get_branch_head(inst, repo, info["default_branch"])
            tree, truncated = await self._gh.get_tree(inst, repo, sha)
        except GitHubNotFound as exc:
            raise PermanentError(f"repository or commit not found: {exc}") from exc

        report = IndexReport(head_sha=sha, truncated=truncated)
        eligible = self._eligible(tree, report)
        existing = await self._store.file_shas(inst, repo)

        todo = [e for e in eligible if existing.get(e["path"]) != e["sha"]]
        unchanged = [e["path"] for e in eligible if existing.get(e["path"]) == e["sha"]]
        report.files_unchanged = len(unchanged)
        await self._store.touch_files(inst, repo, unchanged)  # still present: keep it from expiring

        sem = asyncio.Semaphore(self._s.index_concurrency)

        async def one(entry: dict[str, Any]) -> None:
            async with sem:
                await self._index_file(inst, repo, entry, report)

        async with asyncio.TaskGroup() as tg:  # first failure cancels the rest; the job retries
            for entry in todo:
                tg.create_task(one(entry))

        if not truncated:
            # Only safe when we saw the whole tree, otherwise "missing" may just mean "cut off".
            keep = {e["path"] for e in eligible}
            gone = [p for p in existing if p not in keep]
            report.files_deleted = await self._store.delete_files(inst, repo, gone)

        await self._store.set_repo_state(inst, repo, sha, len(eligible), truncated)
        log.info("repo_indexed", repo=repo, sha=sha[:8], **{
            "indexed": report.files_indexed, "unchanged": report.files_unchanged,
            "deleted": report.files_deleted, "chunks": report.chunks, "truncated": truncated,
        })  # fmt: skip
        return report

    def _eligible(self, tree: list[dict[str, Any]], report: IndexReport) -> list[dict[str, Any]]:
        out = []
        for e in tree:
            if e.get("type") != "blob":
                continue
            path = e["path"]
            if (reason := path_skip_reason(path, [])) is not None:
                report.skip(reason)  # most specific reason first: lock file, binary, vendored...
            elif not _indexable(path):
                report.skip("unsupported file type")
            elif int(e.get("size", 0)) > self._s.max_file_bytes:
                report.skip("file too large")
            else:
                out.append(e)
        if len(out) > self._s.max_index_files:
            # Prefer real code (tree-sitter languages) and smaller files when over the cap.
            out.sort(key=lambda e: (language_for(e["path"]) is None, int(e.get("size", 0))))
            for _ in out[self._s.max_index_files :]:
                report.skip("over file limit")
            out = out[: self._s.max_index_files]
        return out

    async def _index_file(
        self, inst: int, repo: str, entry: dict[str, Any], report: IndexReport
    ) -> None:
        path, blob_sha = entry["path"], entry["sha"]
        raw = await self._gh.get_blob(inst, repo, blob_sha)
        text = _decode(raw) if raw is not None else None
        if text is None:
            report.skip("binary or unreadable")
            return
        if self._s.redaction_enabled:
            text, _ = redact_text(text)  # line-preserving: chunk line numbers stay exact
        chunks = chunk_file(path, text, self._s.chunk_max_chars)
        texts = [embedding_text(c.path, c.kind, c.name, c.content) for c in chunks]
        embeddings = await self._embedder.embed(texts, task="document") if texts else []
        await self._store.replace_file(
            inst, repo, path, blob_sha, chunks, embeddings, self._embedder.model
        )
        report.files_indexed += 1
        report.chunks += len(chunks)


async def purge_expired(store: ChunkStore, retention_days: int, now: datetime | None = None) -> int:
    """Delete indexed code that no index run has seen for `retention_days`."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    removed = await store.purge_older_than(cutoff)
    if removed:
        log.info("index_retention_purge", chunks=removed, retention_days=retention_days)
    return removed
