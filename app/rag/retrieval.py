from dataclasses import dataclass, field

import structlog

from app.core.config import Settings
from app.pipeline.hunks import HunkGroup
from app.rag.embeddings import Embedder
from app.rag.rerank import Reranker
from app.rag.store import ChunkStore, Hit
from app.rag.text import query_terms

log = structlog.get_logger()

MAX_CANDIDATES = 15  # how many fused hits are passed to the reranker
CHUNK_CHARS = 1500  # per-snippet cap in the final prompt


@dataclass(frozen=True)
class ContextChunk:
    path: str
    start_line: int
    end_line: int
    kind: str
    name: str | None
    content: str


@dataclass
class Retrieval:
    chunks: list[ContextChunk] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: rank-based, so BM25 and cosine scores never need calibrating."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(
        self, embedder: Embedder, store: ChunkStore, reranker: Reranker, settings: Settings
    ) -> None:
        self._embedder, self._store, self._reranker, self._s = embedder, store, reranker, settings

    async def retrieve(
        self, inst: int, repo: str, group: HunkGroup, changed_paths: set[str]
    ) -> Retrieval:
        query = f"{group.path}\n{group.added_text}".strip()
        [vec] = await self._embedder.embed([query[:4000]], task="query")
        k = self._s.retrieval_k
        vector_hits = await self._store.vector_search(inst, repo, vec, self._embedder.model, k)
        lexical_hits = await self._store.lexical_search(inst, repo, query_terms(query), k)

        by_id: dict[str, Hit] = {h.chunk.id: h for h in vector_hits + lexical_hits}
        fused = rrf(
            [[h.chunk.id for h in vector_hits], [h.chunk.id for h in lexical_hits]], self._s.rrf_k
        )
        ranked = [by_id[i] for i in sorted(fused, key=lambda i: (-fused[i], i))]
        # The index holds the default branch. Anything from a file this PR changes is stale (and
        # would just repeat the code under review), so it is never offered as context.
        candidates = [h for h in ranked if h.chunk.path not in changed_paths][:MAX_CANDIDATES]
        if not candidates:
            return Retrieval()

        out = Retrieval()
        if self._s.rerank_enabled:
            rr = await self._reranker.rerank(query, candidates)
            candidates = [candidates[i] for i in rr.order]
            out.prompt_tokens, out.completion_tokens, out.cost_usd = (
                rr.prompt_tokens,
                rr.completion_tokens,
                rr.cost_usd,
            )

        budget = self._s.context_chars
        for hit in candidates:
            if len(out.chunks) >= self._s.context_chunks or budget <= 0:
                break
            c = hit.chunk
            content = c.content[: min(CHUNK_CHARS, budget)]
            budget -= len(content)
            out.chunks.append(
                ContextChunk(c.path, c.start_line, c.end_line, c.kind, c.name, content)
            )
        return out
