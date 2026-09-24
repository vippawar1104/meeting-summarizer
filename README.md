# Reviewly

AI pull request reviewer delivered as a GitHub App. Work in progress: see milestone status below.
No performance or quality numbers are published here until they have been measured.

## Local dev
```
uv sync
make test      # pytest
make lint      # ruff + mypy
make up        # docker compose: postgres+pgvector, redis, migrate, api, worker
```

## Status
- [x] M1 skeleton: webhook verify, dedupe, idempotent enqueue, migrations, CI config
- [x] M2 worker + queue semantics: fair scheduler, per-installation cap, small-PR priority, retry/backoff+jitter, DLQ, visibility timeout, graceful shutdown, Postgres reconciler
- [x] M3 diff parsing, LLM review, structured output, line verification (verified with fakes and fixtures; not yet run against live Gemini/GitHub)
- [x] M4 repo index (tree-sitter chunks, pgvector + full-text), hybrid retrieval with RRF, LLM reranker, maintainer-feedback context. Off by default (`REVIEWLY_RAG_ENABLED`): its effect on review quality is not measured yet, that is M6
- [ ] M5 cost controls + safety
- [ ] M6 eval harness
- [ ] M7 dashboard, feedback, billing
- [ ] M8 observability, load test, deploy

## Repo context (M4) design notes
- Indexed per push to the default branch; incremental by git blob SHA, so unchanged files are never re-fetched or re-embedded.
- Chunks follow function/class boundaries (Python, JS/TS/TSX, Go, Java via tree-sitter; line windows for other text files).
- Retrieval = pgvector cosine + Postgres full-text, fused with reciprocal-rank fusion, then an optional LLM rerank. Chunks from files the PR changes are never used (the index holds the default branch, so they would be stale).
- Vector search is an exact scan scoped to one (installation, repo). An ANN index would apply its tenant filter after picking candidates and can return nothing for small tenants.
- Indexed code not seen by an index run for `REVIEWLY_RETENTION_DAYS` (default 30) is deleted; uninstalling or removing a repo purges it immediately.
- Without `REVIEWLY_GEMINI_API_KEY` the embedder falls back to a local hashing embedder: keyword overlap only, no semantic understanding.
