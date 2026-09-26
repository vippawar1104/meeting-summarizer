# Load test

Every number below was measured by the scripts in [loadtest/](../loadtest/); raw results are in
[loadtest/results/](../loadtest/results/). Nothing here was run on Fly.io or against real GitHub or a
real LLM.

## Setup and honest limits
- One 8-core Mac laptop ran everything: Postgres, Redis, the API, the worker, the fake GitHub
  (`scripts/mock_github.py`), the fake LLM (`loadtest/stub_llm.py`) and the load generators. They compete for
  the same cores, so absolute numbers are conservative and **will differ on real infrastructure**.
- The LLM is a stub that answers in 1.0 s ±0.2 s with an empty findings list. Real models take 5-30 s, so
  reviews/min here says how well Reviewly keeps its slots busy, not how many real reviews you will get.
  Cache, free-tier limit and token budget are turned off (`docker-compose.load.yml`), otherwise identical
  diffs would skip the model and flatter the numbers.
- 500 PRs are spread over 25 installations. `per_installation_cap` is 2.
- Every run checks that every finished job produced a review (`valid: true`); one earlier run failed this
  check (worker started without GitHub credentials) and was discarded.
- Each configuration was run once. Treat differences under ~10% as noise.

Reproduce: see [loadtest/README.md](../loadtest/README.md).

## Webhook path (Locust, 20 s per row, 4 Locust processes)
Every request is a new delivery: signature check, dedupe, Postgres insert, Redis enqueue.

| API processes | Senders | Requests/s | p50 | p95 | p99 |
|---|---|---|---|---|---|
| 1 | 10 | 444 | 20 ms | 34 ms | 64 ms |
| 1 | 25 | 385 | 60 ms | 110 ms | 130 ms |
| 1 | 50 | 443 | 99 ms | 180 ms | 220 ms |
| 1 | 100 | 402 | 220 ms | 520 ms | 750 ms |
| 4 | 10 | 995 | 9 ms | 14 ms | 19 ms |
| 4 | 25 | 985 | 22 ms | 41 ms | 83 ms |
| 4 | 50 | 791 | 55 ms | 120 ms | 160 ms |
| 4 | 100 | 740 | 120 ms | 250 ms | 320 ms |

Zero failures in every row.

**Bottleneck found:** with more senders throughput stays flat and latency grows in proportion, and the API
container sat at 99.5% of one core while Postgres was at 20% and Redis at 3%. One Python process is the
limit. A profile showed no single hot spot (many small costs in FastAPI, SQLModel, the driver). **Fix:**
run several uvicorn processes (`WEB_CONCURRENCY`, 1.7-2.3x more throughput here, not 4x because the load
generator and database share the machine). Disabling the access log made no measurable difference.
Each process has its own Postgres pool (`REVIEWLY_DB_POOL_SIZE` + `REVIEWLY_DB_MAX_OVERFLOW`, 10 by default),
so processes x 10 must stay below the database's connection limit.

My first burst driver (a single Python/httpx process) reported p99 of several seconds at 100 senders. That
was the generator's own scheduling delay, not the server; use Locust for webhook latency.

## Drain rate vs worker slots (500 PRs burst, `loadtest.burst`)
| Worker slots | Drain time | Reviews/min |
|---|---|---|
| 4 | 133 s | 226 |
| 8 | 67 s | 446 |
| 16 | 34 s | 881 |
| 32 | 19 s | 1576 |

Near-linear: about 50 reviews/min per slot at 1 s model latency, so with a fast model the worker is not the
limit. The 0.2 s poll interval was: a slot freed by a finished job waited for the next poll. Waking the loop
when a job finishes gave about +10% (4 slots: 204 to 226; 32 slots: 1434 to 1576; single runs).

## Failure scenarios (all: no lost jobs, no duplicate reviews)
| Scenario | Result |
|---|---|
| Primary model always 503, fallback healthy (200 PRs, 16 slots) | 200/200 reviewed in 15 s (787/min, same as healthy). Only 16 calls reached the dead model before its circuit breaker opened. |
| **All** models down for 45 s (100 PRs) | **Before fix: 33/100 reviewed, 67 dead.** Once the breakers opened, each attempt failed instantly and 5 attempts were used up in ~40 s. **After fix: 100/100**, finishing ~58 s after the models returned (breaker cooldown + jittered retry). Fix: an outage defers the job without using attempts (`REVIEWLY_LLM_OUTAGE_WINDOW_S`, default 1 h). |
| Redis restarted mid-drain, AOF on (300 PRs) | 300/300. 287 done in 22 s; the 13 jobs in flight when Redis died waited for the 120 s visibility timeout (total 129 s). |
| Redis wiped (`FLUSHALL`) mid-drain | All accepted jobs finished; the reconciler rebuilt the queue from Postgres (last job done at ~85 s). |
| Burst sent right after a Redis restart | **Before fix: 11/300 webhooks got HTTP 500** (stale pooled connections, no error handling). **After fix: 300/300 accepted.** Fix: resilient Redis client, and Redis failures return 503 so GitHub redelivers. |
| Worker `kill -9` mid-drain, restarted 5 s later (300 PRs) | 300/300 reviewed in 129 s; the ~16 in-flight jobs were retried after the visibility timeout, and the idempotency marker prevented duplicate reviews. |

## Known limits
- Recovery from a worker crash or Redis restart takes up to `REVIEWLY_VISIBILITY_TIMEOUT_S` (120 s). Lower it
  if you want faster recovery; heartbeats keep healthy long jobs alive.
- An LLM outage longer than `REVIEWLY_LLM_OUTAGE_WINDOW_S` still kills jobs; `scripts/requeue_dead.py` puts them back.
- Webhook latency past ~25 concurrent senders exceeds 100 ms with 1-4 processes on this hardware. GitHub
  itself sends a modest number of concurrent deliveries; this was not measured with real GitHub traffic.
- One job in one 32-slot run was retried once after a transient failure (its error was not logged; the retry
  log line now includes it) and finished after 41 s. An earlier note that it stalled for 616 s was wrong: the
  laptop slept during that run and the wall-clock timer counted the sleep. The harness now uses a monotonic clock.
