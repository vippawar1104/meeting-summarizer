"""Storage contract for indexed code chunks, plus an in-memory implementation.

Every method takes (installation_id, repo): a chunk is never readable outside its installation.
"""

import math
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.rag.chunker import Chunk
from app.rag.text import search_text


@dataclass(frozen=True)
class StoredChunk:
    id: str
    path: str
    start_line: int
    end_line: int
    kind: str
    name: str | None
    content: str


@dataclass(frozen=True)
class Hit:
    chunk: StoredChunk
    score: float


@dataclass(frozen=True)
class RepoState:
    head_sha: str
    file_count: int
    truncated: bool
    indexed_at: datetime


class ChunkStore(Protocol):
    async def file_shas(self, inst: int, repo: str) -> dict[str, str]: ...

    async def replace_file(
        self,
        inst: int,
        repo: str,
        path: str,
        blob_sha: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        model: str,
    ) -> None: ...

    async def delete_files(self, inst: int, repo: str, paths: Iterable[str]) -> int: ...

    async def touch_files(self, inst: int, repo: str, paths: Iterable[str]) -> None: ...

    async def vector_search(
        self, inst: int, repo: str, embedding: list[float], model: str, k: int
    ) -> list[Hit]: ...

    async def lexical_search(self, inst: int, repo: str, terms: list[str], k: int) -> list[Hit]: ...

    async def set_repo_state(
        self, inst: int, repo: str, head_sha: str, file_count: int, truncated: bool
    ) -> None: ...

    async def get_repo_state(self, inst: int, repo: str) -> RepoState | None: ...

    async def delete_repo(self, inst: int, repo: str) -> int: ...

    async def delete_installation(self, inst: int) -> int: ...

    async def purge_older_than(self, cutoff: datetime) -> int: ...

    async def count_chunks(self, inst: int, repo: str) -> int: ...


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Row:
    chunk: StoredChunk
    embedding: list[float]
    model: str
    tokens: list[str]


@dataclass
class _File:
    blob_sha: str
    last_seen: datetime
    rows: list[_Row]


class MemoryChunkStore:
    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        self._files: dict[tuple[int, str], dict[str, _File]] = {}
        self._state: dict[tuple[int, str], RepoState] = {}
        self._seq = 0

    def _repo(self, inst: int, repo: str) -> dict[str, _File]:
        return self._files.setdefault((inst, repo), {})

    async def file_shas(self, inst: int, repo: str) -> dict[str, str]:
        return {p: f.blob_sha for p, f in self._repo(inst, repo).items()}

    async def replace_file(
        self,
        inst: int,
        repo: str,
        path: str,
        blob_sha: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        model: str,
    ) -> None:
        assert len(chunks) == len(embeddings)
        rows = []
        for c, e in zip(chunks, embeddings, strict=True):
            self._seq += 1
            stored = StoredChunk(
                f"m{self._seq}", c.path, c.start_line, c.end_line, c.kind, c.name, c.content
            )
            tokens = search_text(c.path, c.name, c.content).split()
            rows.append(_Row(stored, e, model, tokens))
        self._repo(inst, repo)[path] = _File(blob_sha, self._clock(), rows)

    async def delete_files(self, inst: int, repo: str, paths: Iterable[str]) -> int:
        files = self._repo(inst, repo)
        return sum(1 for p in paths if files.pop(p, None) is not None)

    async def touch_files(self, inst: int, repo: str, paths: Iterable[str]) -> None:
        files = self._repo(inst, repo)
        for p in paths:
            if p in files:
                files[p].last_seen = self._clock()

    def _rows(self, inst: int, repo: str) -> list[_Row]:
        return [r for f in self._repo(inst, repo).values() for r in f.rows]

    async def vector_search(
        self, inst: int, repo: str, embedding: list[float], model: str, k: int
    ) -> list[Hit]:
        scored = [
            Hit(r.chunk, sum(a * b for a, b in zip(embedding, r.embedding, strict=True)))
            for r in self._rows(inst, repo)
            if r.model == model
        ]
        return sorted(scored, key=lambda h: -h.score)[:k]

    async def lexical_search(self, inst: int, repo: str, terms: list[str], k: int) -> list[Hit]:
        rows = self._rows(inst, repo)
        if not rows or not terms:
            return []
        n = len(rows)
        avg_len = sum(len(r.tokens) for r in rows) / n or 1.0
        doc_freq = Counter(t for r in rows for t in set(r.tokens))
        hits = []
        for r in rows:
            tf = Counter(r.tokens)
            score = 0.0
            for term in terms:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                f = tf[term]
                score += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * len(r.tokens) / avg_len))
            if score > 0:
                hits.append(Hit(r.chunk, score))
        return sorted(hits, key=lambda h: -h.score)[:k]

    async def set_repo_state(
        self, inst: int, repo: str, head_sha: str, file_count: int, truncated: bool
    ) -> None:
        self._state[(inst, repo)] = RepoState(head_sha, file_count, truncated, self._clock())

    async def get_repo_state(self, inst: int, repo: str) -> RepoState | None:
        return self._state.get((inst, repo))

    async def delete_repo(self, inst: int, repo: str) -> int:
        n = sum(len(f.rows) for f in self._files.pop((inst, repo), {}).values())
        self._state.pop((inst, repo), None)
        return n

    async def delete_installation(self, inst: int) -> int:
        removed = 0
        for i, r in [key for key in self._files if key[0] == inst]:
            removed += await self.delete_repo(i, r)
        return removed

    async def purge_older_than(self, cutoff: datetime) -> int:
        removed = 0
        for files in self._files.values():
            for path in [p for p, f in files.items() if f.last_seen < cutoff]:
                removed += len(files.pop(path).rows)
        return removed

    async def count_chunks(self, inst: int, repo: str) -> int:
        return len(self._rows(inst, repo))
