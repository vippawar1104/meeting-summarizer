"""Fire a burst of signed pull_request webhooks, then watch the system drain.

    python -m loadtest.burst --n 500 --label baseline

Needs the stack up with docker-compose.load.yml, scripts.mock_github on :9000 and
loadtest.stub_llm on :9001 (see loadtest/README.md). Reports, all measured by this script:

  * webhook latency (p50/p95/p99/max) and status codes, as seen by the sender
  * time until every job reached a terminal state, and reviews/minute over that time
  * lost jobs (webhook accepted, job never finished) and duplicate reviews (one PR reviewed twice)

Job state is read straight from Postgres, review count from the fake GitHub, so neither number
trusts the code under test.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import statistics
import sys
import time
from pathlib import Path

import httpx
import psycopg

DIFF = """diff --git a/app/orders.py b/app/orders.py
index 1111111..2222222 100644
--- a/app/orders.py
+++ b/app/orders.py
@@ -10,6 +10,9 @@ def total(items):
     result = 0
     for item in items:
         result += item.price * item.qty
+    if result > 1000:
+        result = result * 0.9
+    return result
-    return result
"""


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--installations", type=int, default=25)
    ap.add_argument("--concurrency", type=int, default=100, help="webhooks in flight at once")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--mock", default="http://localhost:9000")
    ap.add_argument("--secret", default="dev-secret")
    ap.add_argument("--pg", default="postgresql://reviewly:reviewly@localhost:5432/reviewly")
    ap.add_argument("--timeout", type=int, default=900, help="seconds to wait for the drain")
    ap.add_argument("--no-drain", action="store_true", help="only measure the webhook side")
    ap.add_argument("--label", default="run")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    run = f"{int(time.time())}"
    repo_prefix = f"load{run}"
    prs = [(f"{repo_prefix}/r{i % args.installations}", i) for i in range(args.n)]
    installation = lambda i: 100_000 + (i % args.installations)  # noqa: E731

    async with httpx.AsyncClient(
        timeout=60, limits=httpx.Limits(max_connections=args.concurrency + 20)
    ) as http:
        shas: dict[int, str] = {}
        sem = asyncio.Semaphore(50)

        async def register(repo: str, number: int) -> None:
            async with sem:
                r = await http.post(
                    f"{args.mock}/_debug/pr",
                    json={
                        "repo": repo,
                        "number": number,
                        "title": "Apply bulk discount",
                        "diff": DIFF,
                    },
                )
                shas[number] = r.json()["sha"]

        await asyncio.gather(*(register(repo, n) for repo, n in prs))

        latencies: list[float] = []
        statuses: dict[int, int] = {}
        send_sem = asyncio.Semaphore(args.concurrency)

        async def send(repo: str, number: int) -> None:
            body = json.dumps({
                "action": "opened", "installation": {"id": installation(number)},
                "repository": {"full_name": repo},
                "pull_request": {"number": number, "head": {"sha": shas[number]}, "additions": 3, "deletions": 1},
            }).encode()  # fmt: skip
            async with send_sem:
                started = time.perf_counter()
                r = await http.post(
                    f"{args.api}/webhooks/github",
                    content=body,
                    headers={
                        "X-Hub-Signature-256": sign(args.secret, body),
                        "X-GitHub-Event": "pull_request",
                        "X-GitHub-Delivery": f"{repo_prefix}-{number}",
                        "Content-Type": "application/json",
                    },
                )
                latencies.append((time.perf_counter() - started) * 1000)
                statuses[r.status_code] = statuses.get(r.status_code, 0) + 1

        t0 = time.monotonic()  # monotonic: a sleeping laptop must not look like a slow drain
        await asyncio.gather(*(send(repo, n) for repo, n in prs))
        burst_s = time.monotonic() - t0

        counts: dict[str, int] = {}
        timeline: list[tuple[float, int]] = []
        with psycopg.connect(args.pg, autocommit=True) as conn:
            while time.monotonic() - t0 < args.timeout:
                rows = conn.execute(
                    "SELECT status, count(*) FROM jobs WHERE repo_full_name LIKE %s GROUP BY status",
                    (f"{repo_prefix}/%",),
                ).fetchall()
                counts = {s: int(c) for s, c in rows}
                finished = counts.get("done", 0) + counts.get("dead", 0)
                timeline.append((round(time.monotonic() - t0, 1), counts.get("done", 0)))
                if finished >= args.n or args.no_drain:
                    break
                await asyncio.sleep(1)
        drained_s = time.monotonic() - t0

        posted = (await http.get(f"{args.mock}/_debug/posted")).json()
        reviewed: dict[int, int] = {}
        for p in posted:
            if p["repo"].startswith(repo_prefix) and p["kind"] == "review":
                reviewed[p["number"]] = reviewed.get(p["number"], 0) + 1
        try:
            stub = (await http.get("http://localhost:9001/_stats")).json()
        except httpx.HTTPError:
            stub = {}

    done, dead = counts.get("done", 0), counts.get("dead", 0)
    result = {
        "label": args.label,
        "note": args.note,
        "webhooks": args.n,
        "installations": args.installations,
        "send_concurrency": args.concurrency,
        "webhook_status_codes": statuses,
        "webhook_ms": {
            "p50": round(statistics.median(latencies), 1),
            "p95": round(percentile(latencies, 0.95), 1),
            "p99": round(percentile(latencies, 0.99), 1),
            "max": round(max(latencies), 1),
        },
        "burst_send_seconds": round(burst_s, 2),
        "drain_seconds": round(drained_s, 1),
        "jobs_by_status": counts,
        "reviews_per_minute": round(done / drained_s * 60, 1) if drained_s else 0,
        "lost_jobs": max(0, statuses.get(200, 0) - sum(counts.values())),
        "unfinished_jobs": args.n - done - dead,
        "prs_reviewed": len(reviewed),
        # a job can be "done" without reviewing anything (e.g. a worker started with no GitHub
        # credentials), so a run only counts if every finished job produced a review
        "valid": args.no_drain or len(reviewed) >= done > 0,
        "duplicate_reviews": sum(1 for c in reviewed.values() if c > 1),
        "stub_llm_calls": stub.get("calls"),
        "stub_llm_failed": stub.get("failed"),
        "timeline_done": timeline[:: max(1, len(timeline) // 12)],
    }
    out = Path(__file__).parent / "results" / f"{args.label}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["valid"]:
        print("INVALID RUN: fewer reviews were posted than jobs finished", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
