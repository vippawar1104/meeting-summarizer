# Runbook

Metrics: API `GET /metrics` (set `REVIEWLY_METRICS_TOKEN` to require `Authorization: Bearer ...`), worker on
port `REVIEWLY_WORKER_METRICS_PORT` (9100). Logs are JSON with a `correlation_id` that follows a delivery from
webhook to job to worker: `grep <id>` across both services shows one PR's whole path. Tracing is off until
`REVIEWLY_OTLP_ENDPOINT` is set (spans carry the same `correlation_id` attribute; they are not linked by W3C
trace context). Fly.io deployment files exist (`fly.web.toml`, `fly.worker.toml`) but have **not** been
deployed by the author.

## First deploy (Fly.io)
```
fly apps create reviewly && fly apps create reviewly-worker
# Postgres with pgvector (Fly Postgres or Supabase) and Redis (Upstash, persistence on) -> connection URLs
for a in reviewly reviewly-worker; do fly secrets set -a $a \
  REVIEWLY_DATABASE_URL=... REVIEWLY_REDIS_URL=... REVIEWLY_GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 32) \
  REVIEWLY_DASHBOARD_SECRET=$(openssl rand -hex 32) REVIEWLY_ENCRYPTION_KEY=$(python -m app.core.crypto) \
  REVIEWLY_PUBLIC_URL=https://reviewly.fly.dev REVIEWLY_GROQ_API_KEY=...; done   # use the SAME values for both apps
fly secrets set -a reviewly REVIEWLY_SETUP_TOKEN=$(openssl rand -hex 16)
fly deploy -c fly.web.toml && fly deploy -c fly.worker.toml
```
Open `https://<host>/setup?token=<REVIEWLY_SETUP_TOKEN>`, press the button, and put the credentials it prints
(app id, private key, webhook secret, OAuth client id/secret, slug) into **both** apps' secrets. Then remove
`REVIEWLY_SETUP_TOKEN`. Check `/readyz` returns `{"db":"ok","redis":"ok"}`.
Size: each web process holds up to 10 Postgres connections (`REVIEWLY_DB_POOL_SIZE` + `REVIEWLY_DB_MAX_OVERFLOW`);
machines x `WEB_CONCURRENCY` x 10 plus the worker must stay under the database limit.

## Symptoms and what to do
| Symptom | Look at | Action |
|---|---|---|
| PRs not reviewed at all | `reviewly_webhook_requests_total{result}`; GitHub App > Advanced > Recent deliveries | `bad_signature`: webhook secret differs between GitHub and the app. No deliveries: wrong webhook URL. |
| Webhook returns 503 | `/readyz`, logs `webhook_failed` / `webhook_dedupe_failed` | Postgres or Redis is down. GitHub redelivers 5xx automatically; nothing is lost. |
| Queue growing (`reviewly_queue_depth{state="ready"}`) | `reviewly_llm_seconds`, `reviewly_job_seconds`, worker logs | Slow model: raise `REVIEWLY_WORKER_CONCURRENCY` or add worker machines. Throughput scales ~linearly with slots. |
| `reviewly_llm_breaker_open` = 1 | `reviewly_llm_calls_total{outcome="error"}` | A provider is failing; traffic uses the next in `REVIEWLY_PROVIDER_ORDER`. If all are open, jobs are *deferred* (retried ~every 30 s, attempts kept) for up to `REVIEWLY_LLM_OUTAGE_WINDOW_S`. Nothing to do but fix or replace the key/provider. |
| Jobs `dead` | `select id,last_error from jobs where status='dead'` | Fix the cause, then `python -m scripts.requeue_dead` (dry run) and `--apply` (optionally `--installation N --since-hours 6`). Reviews are idempotent per head SHA: an already reviewed PR is skipped. |
| Redis restarted or wiped | worker log `reconciled_jobs` | Automatic. Postgres is the source of truth; the reconciler re-enqueues jobs within `REVIEWLY_RECONCILE_INTERVAL_S` + `REVIEWLY_RECONCILE_GRACE_S`; jobs that were in flight wait up to `REVIEWLY_VISIBILITY_TIMEOUT_S` (120 s). |
| Worker crashed / deploy | log `job_reaped` | Automatic after the visibility timeout. SIGTERM finishes or requeues in-flight jobs within `REVIEWLY_SHUTDOWN_GRACE_S` (25 s); Fly's `kill_timeout` is 40 s. |
| "no AI model configured" notices on PRs | dashboard > AI model | The platform has no model key and the installation has not added its own. Set a platform key or ask them to add one. |
| A user's own key stopped working | PR notice; dashboard | They are told; there is deliberately no fallback to platform models. Ask them to re-enter it. If `REVIEWLY_ENCRYPTION_KEY` was lost, all stored keys are unreadable and must be re-entered. |
| Free-tier notices unexpectedly | `usage` table, `REVIEWLY_FREE_REVIEWS_PER_MONTH` | 0 disables the limit. |
| Cost surprise | `reviewly_llm_cost_usd_total`, `reviewly_llm_tokens_total`, `REVIEWLY_DAILY_TOKEN_BUDGET` | Per-installation daily token budget caps spend on platform models. Cost uses an unverified price table: treat as an estimate. |

## Migrations and rollback
`fly deploy` runs `alembic upgrade head` before switching traffic. Migrations are additive so far
(0001-0006); to roll back the app, `fly releases` then `fly deploy --image <previous>`. Do not run
`alembic downgrade` in production without a backup.

## Secrets rotation
- Encryption key: set `REVIEWLY_ENCRYPTION_KEY=<new>,<old>` (first encrypts, all decrypt), deploy, later drop the old one.
- Webhook secret: change it in the GitHub App settings and in both apps at the same time; deliveries fail with
  `bad_signature` in between.
