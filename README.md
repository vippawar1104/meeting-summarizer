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
- [x] M6 evaluation harness: 58 labeled cases, metrics with confidence intervals, record/replay, CI gate. Gemini numbers pending (needs an API key)
- [x] M7 dashboard (React + TypeScript, black on white with dark mode), feedback loop, usage metering, free tier, Stripe test-mode checkout
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

## Evaluation (M6)
`python -m eval.run --model gemini-2.5-flash --prompt v1` runs the real review pipeline (parsing, redaction, LLM, line
verification, ranking) against a fake GitHub on 58 labeled diffs and scores what it would have posted. See
[eval/dataset/README.md](eval/dataset/README.md) for how the labels were made and their limits, and
[eval/dataset/SOURCES.md](eval/dataset/SOURCES.md) for attribution.

- **Metrics:** precision, recall (per case), false alarms per PR that has nothing to find, raw line accuracy (share of the
  model's own findings that point at a line in the diff, measured *before* verification, so it shows hallucinated lines),
  latency p50/p95, tokens and cost per PR, with bootstrap 95% confidence intervals over cases.
- **Reproducible offline:** every LLM reply is recorded in `eval/recordings/`. `--mode replay` re-scores from them with no
  key and no network. Costs use the per-model price table in `app/llm/pricing.py`, which is not yet verified against
  provider pricing, so treat cost columns as estimates.
- **Baselines** prove the metrics behave: `baseline:null` (never comments) must score 0% recall, and `baseline:regex` is a
  naive anti-pattern matcher that any real model should beat.
- **Prompts are versioned** in `app/prompts/vN/`. A prompt change is only kept if `make eval` shows it helps.
- **CI** runs `python -m eval.validate` (dataset integrity) and `python -m eval.gate` (fails if a scored pipeline gets worse than
  its committed baseline in `eval/baseline.json`).

Reproduce: `make eval-baselines` (no key needed) or `make eval MODEL=gemini-2.5-flash PROMPT=v1` (needs
`REVIEWLY_GEMINI_API_KEY`), then `make eval-report` to refresh this table.

<!-- eval-table:start -->
| Model | Prompt | Cases | Precision (95% CI) | Recall (95% CI) | False alarms / no-bug PR | Line accuracy (raw) | Latency p50 / p95 | Tokens / PR | Cost / PR |
|---|---|---|---|---|---|---|---|---|---|
| baseline:null | v1 | 58 (0 err) | n/a (nothing posted) | 0% (0%–0%) | 0.00 (0.00–0.00) | n/a | 0.0s / 0.0s | 0 | n/a |
| baseline:regex | v1 | 58 (0 err) | 100% (100%–100%) | 2% (0%–8%) | 0.00 (0.00–0.00) | 100% | 0.0s / 0.0s | 0 | n/a |
| groq:openai/gpt-oss-120b | v1 | 58 (0 err) | 67% (52%–82%) | 98% (92%–100%) | 1.18 (0.69–1.73) | 100% | 9.7s / 46.3s | 1798 | n/a |
| groq:openai/gpt-oss-120b | v3 | 58 (0 err) | 84% (73%–93%) | 98% (91%–100%) | 0.53 (0.29–0.77) | 100% | 15.3s / 101.3s | 2397 | n/a |
| groq:openai/gpt-oss-20b | v1 | 58 (6 err) | 68% (51%–84%) | 87% (76%–97%) | 1.23 (0.70–2.00) | 100% | 16.1s / 125.3s | 2224 | n/a |
| groq:qwen/qwen3.8-27b | v1 | 58 (0 err) | 79% (67%–90%) | 95% (88%–100%) | 0.59 (0.27–0.93) | 100% | 6.5s / 72.4s | 1304 | n/a |
| groq:qwen/qwen3.8-27b | v3 | 58 (0 err) | 86% (76%–94%) | 95% (88%–100%) | 0.41 (0.18–0.65) | 100% | 17.3s / 79.3s | 1947 | n/a |
<!-- eval-table:end -->

### What the numbers say
Measured on Groq's free tier (58 cases each; `gpt-oss-120b` and `qwen3.8-27b` had 0 errors, `gpt-oss-20b` had 6 cases that still
failed after retries and its row covers only the cases that scored).

- **Prompt v3 is a proven improvement on `gpt-oss-120b`.** Paired over the same 58 cases: precision +16.8 points (95% CI +6.1 to
  +28.0), false alarms per no-bug PR 1.18 to 0.53 (CI -1.24 to -0.17), recall unchanged (98%). v3 adds rules of evidence (no
  speculation about unseen code, no guessing intent, at most 3 findings, stricter confidence levels). Cost: about 33% more tokens per PR.
- **On `qwen3.8-27b` v3 helps in the same direction but not significantly** (precision +7.1 points, CI -0.3 to +14.3). Its v1 was already
  quieter than `gpt-oss-120b` v1.
- The default prompt is v3, and the default provider order is `gpt-oss-120b` with `qwen3.8-27b` as fallback.

### Read these numbers carefully
- 58 cases is small; the intervals are wide and many differences between models are not significant.
- **Recall is probably optimistic.** Labels mark whole regions a fix touched, a hit may be 2 lines away, and the bug cases come from
  famous libraries a model may have seen in training. Do not read 98% as "catches 98% of real bugs".
- **Precision is probably slightly pessimistic.** "Clean" PRs are only assumed clean, so a genuine latent bug reported there counts as a false alarm.
- Latency includes waiting out Groq's per-minute rate limits while several runs shared the budget, so treat it as an upper bound.
- Cost columns are n/a: no verified price for these models.
- Remaining false alarms are mostly confident speculation (0.93 to 0.95). The next improvement to try is a second verification pass that re-checks each finding against the visible diff.

## Dashboard, feedback and billing (M7)
**Run it locally with demo data**
```
make up                     # postgres, redis, migrations, api (serves the dashboard), worker
make seed                   # fake installation 42 with reviews, findings and feedback (dev only)
open "http://localhost:8000/auth/dev-login?installation=42"
```
`dev-login` exists only when `REVIEWLY_ENV=dev` and `REVIEWLY_DASHBOARD_DEV_LOGIN=true`; the app refuses to start outside dev
with placeholder secrets or with dev login on. Frontend work: `make dashboard-dev` (Vite, proxies to :8000) and `make dashboard-test`.

**The dashboard** is one page, black on white (white on black in dark mode, following the system until you press the toggle):
summary tiles, plan and usage, precision by rule, reviews per month (with a table view), repositories, and recent reviews.
Every query is scoped to one installation, and another tenant's installation returns 404.

**Feedback loop.** Reviewly learns what maintainers think of its comments from:
- a reply `@reviewly dismiss` or `@reviewly accept` (a deliberate command always outranks reactions),
- reactions on its comment (👍 ❤️ 🎉 🚀 accept; 👎 😕 dismiss; bots ignored), polled every 10 minutes because GitHub sends no webhook for reactions,
- resolving a thread, which is shown but **not** counted in precision (people resolve threads for many reasons).

Precision per rule (category) is `accepted / (accepted + dismissed)` and is shown as a dash, never 0% or 100%, until something is judged.
A finding a repo's maintainers have dismissed at least twice and never accepted is suppressed there from then on.

**Free tier and billing.** Each installation gets `REVIEWLY_FREE_REVIEWS_PER_MONTH` reviews per UTC month (default 20, a placeholder;
0 = unlimited). Over the limit, the PR gets a short notice and no tokens are spent. Stripe test-mode Checkout upgrades an installation;
signed webhooks (`/webhooks/stripe`, timestamp-checked against replay) keep the subscription in sync, with a 3-day grace for failed payments.

**Not verified against the real services** (no credentials were available): GitHub OAuth login, the reaction polling and
comment linking against GitHub itself, and Stripe Checkout and its webhooks. All are covered by tests with mocked HTTP; the
feedback webhook, dashboard API, tenant isolation and a comment id above 2^31 were exercised live against Postgres.
Cost figures show "n/a" because there is no verified price for the models in use.
