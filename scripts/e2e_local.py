"""Drive one pull request through the running system: webhook -> queue -> worker -> LLM -> review.

Needs `scripts/mock_github.py` on :9000 and the API on :8000 (see docker-compose.e2e.yml).

    python -m scripts.e2e_local [--case bug-httpx-c75ddc2] [--timeout 240]
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.request
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent / "eval" / "dataset" / "cases"


def call(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(
        url, data=data, headers=headers or {}, method="POST" if data is not None else "GET"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (local URLs only)
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="bug-httpx-c75ddc2")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--mock", default="http://localhost:9000")
    ap.add_argument("--secret", default="dev-secret")
    ap.add_argument("--installation", type=int, default=4242)
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    case = json.loads((CASES / args.case / "case.json").read_text())
    diff = (CASES / args.case / "diff.patch").read_text()
    repo, number = "acme/e2e-demo", int(time.time()) % 100000
    sha = json.loads(
        call(
            f"{args.mock}/_debug/pr",
            json.dumps(
                {"repo": repo, "number": number, "title": case["title"], "diff": diff}
            ).encode(),
        )
    )["sha"]
    print(
        f"registered PR {repo}#{number} @ {sha} (case {args.case}; labels: {[(lb['file'], lb['start'], lb['end']) for lb in case['labels']]})"
    )

    body = json.dumps({
        "action": "opened", "installation": {"id": args.installation}, "repository": {"full_name": repo},
        "pull_request": {"number": number, "head": {"sha": sha}, "additions": 10, "deletions": 5},
    }).encode()  # fmt: skip
    sig = "sha256=" + hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
    started = time.time()
    reply = call(
        f"{args.api}/webhooks/github",
        body,
        {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": f"e2e-{number}",
        },
    )
    print(f"webhook accepted in {time.time() - started:.2f}s -> {reply.decode()}")

    while time.time() - started < args.timeout:
        posted = [
            p
            for p in json.loads(call(f"{args.mock}/_debug/posted"))
            if p["repo"] == repo and p["number"] == number
        ]
        if posted:
            print(f"\nREVIEW POSTED after {time.time() - started:.1f}s")
            for p in posted:
                if p["kind"] == "review":
                    print(
                        f"event={p['event']} commit={p['commit_id']} inline_comments={len(p.get('comments', []))}\n--- summary ---\n{p['body']}"
                    )
                    for c in p.get("comments", []):
                        print(f"--- {c['path']}:{c['line']} ({c['side']}) ---\n{c['body']}")
                else:
                    print(f"--- PR comment ---\n{p['body']}")
            return 0
        time.sleep(3)
    print("TIMED OUT: no review was posted")
    return 1


if __name__ == "__main__":
    sys.exit(main())
