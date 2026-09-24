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
- [x] M5 secret redaction, injection hardening, per-installation token budget and rate limit, review cache, partial-review handling
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

## Cost and safety (M5) design notes
- **Redaction** runs on the parsed diff (added, removed and context lines) and on the PR title before anything is built from them, so prompts, retrieval queries and cache keys never contain a raw secret. A second, independent pass in the LLM guard and the embedder wrapper redacts again, so one bypassed layer does not leak. Indexed code is redacted before it is stored. It is line-preserving, so diff line numbers stay exact. It is pattern and entropy based: it catches common token formats, private keys, `password = "..."` assignments, URL credentials, JWTs and high-entropy strings, and it will miss secrets in unusual formats. Treat it as risk reduction, not a guarantee.
- **Prompt injection**: diff, title, retrieved context and feedback are delimited as untrusted data and forged delimiters are neutralized. Invisible/bidi/Unicode-tag characters are stripped. Added lines that address the AI reviewer produce a low-severity finding so humans see the attempt. The model's reply is also untrusted: it is stripped of images, remote links, HTML comments (so it cannot forge our idempotency markers), and @mentions are code-formatted so nobody is pinged. The bot only ever posts `COMMENT` reviews.
- **Budget**: a daily UTC token budget per installation, enforced atomically in Redis (reserve an estimate, settle to real usage). When it runs out mid-review the review is partial and says so; when nothing could be reviewed the PR gets a short notice and the job is skipped, not retried. `REVIEWLY_DAILY_TOKEN_BUDGET=0` disables the limit. The default (500k) is a placeholder, not a measured figure.
- **Rate limit**: a token bucket per installation on LLM calls. Short waits are absorbed; a long wait fails the job into normal retry/backoff.
- **Cache**: keyed on the whitespace-normalized diff section plus prompt version, system prompt, repo rules, retrieved context and feedback, scoped per installation and expiring after `REVIEWLY_CACHE_TTL_DAYS`. It is deliberately *not* embedding-similarity based: `x > 0` and `x >= 0` embed almost identically, and replaying a stale review over a changed condition is the one mistake a reviewer cannot make. Reformatted code still hits; any real token change misses.
