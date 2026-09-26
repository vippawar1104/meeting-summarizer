"""Sustained webhook load with Locust: many senders, unique deliveries, valid signatures.

    uv run locust -f loadtest/locustfile.py --headless -u 100 -r 100 -t 30s --host http://localhost:8000 --processes 4

Measures the webhook endpoint only (no PR needs to exist while the worker is stopped). Every
request is a new delivery on a new head SHA, so each one takes the full path: signature check,
dedupe, Postgres insert, Redis enqueue.
"""

import hashlib
import hmac
import itertools
import json
import os
import uuid

from locust import HttpUser, constant, task

SECRET = os.environ.get("REVIEWLY_GITHUB_WEBHOOK_SECRET", "dev-secret")
RUN = uuid.uuid4().hex[:8]
counter = itertools.count(1)


class Sender(HttpUser):
    wait_time = constant(0)

    @task
    def pull_request_opened(self) -> None:
        n = next(counter)
        body = json.dumps({
            "action": "opened", "installation": {"id": 200_000 + n % 25},
            "repository": {"full_name": f"locust{RUN}/r{n % 25}"},
            "pull_request": {"number": n, "head": {"sha": f"{RUN}{n:08x}"}, "additions": 3, "deletions": 1},
        }).encode()  # fmt: skip
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.client.post(
            "/webhooks/github",
            data=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": f"{RUN}-{os.getpid()}-{n}",
                "Content-Type": "application/json",
            },
            name="POST /webhooks/github",
        )
