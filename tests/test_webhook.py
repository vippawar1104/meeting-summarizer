import json

import pytest
from sqlalchemy import func, select

from app.api.webhooks import verify_signature
from app.db.models import Job
from tests.conftest import SECRET, post_webhook, pr_payload, sign


async def job_count(env) -> int:
    async with env["sessionmaker"]() as s:
        return (await s.execute(select(func.count()).select_from(Job))).scalar_one()


def test_verify_signature_accepts_valid():
    assert verify_signature(SECRET, b"x", sign(b"x"))


@pytest.mark.parametrize("header", [None, "", "sha256=", "sha1=abc", "sha256=deadbeef"])
def test_verify_signature_rejects_bad(header):
    assert not verify_signature(SECRET, b"x", header)


def test_verify_signature_rejects_wrong_secret():
    assert not verify_signature(SECRET, b"x", sign(b"x", "other"))


async def test_bad_signature_is_401_and_enqueues_nothing(env):
    r = await post_webhook(env["client"], pr_payload(), signature="sha256=bad")
    assert r.status_code == 401
    assert await job_count(env) == 0
    assert (await env["queue"].depth())["ready"] == 0


async def test_valid_pr_event_is_enqueued(env):
    r = await post_webhook(env["client"], pr_payload())
    assert r.status_code == 200
    assert r.json() == {"status": "enqueued"}
    assert await job_count(env) == 1
    assert (await env["queue"].depth())["ready"] == 1


async def test_job_row_fields(env):
    await post_webhook(env["client"], pr_payload(sha="feed", number=9, installation=5))
    async with env["sessionmaker"]() as s:
        job = (await s.execute(select(Job))).scalar_one()
    assert job.idempotency_key == "5:acme/widgets:9:feed"
    assert job.changed_lines == 15
    assert job.status == "queued"


async def test_redelivery_same_delivery_id_is_deduped(env):
    await post_webhook(env["client"], pr_payload(), delivery="same")
    r = await post_webhook(env["client"], pr_payload(), delivery="same")
    assert r.json() == {"status": "duplicate_delivery"}
    assert await job_count(env) == 1
    assert (await env["queue"].depth())["ready"] == 1


async def test_new_delivery_same_head_sha_never_reviews_twice(env):
    await post_webhook(env["client"], pr_payload(), delivery="a")
    r = await post_webhook(env["client"], pr_payload(), delivery="b")
    assert r.json() == {"status": "duplicate_review"}
    assert await job_count(env) == 1
    assert (await env["queue"].depth())["ready"] == 1


async def test_new_head_sha_gets_new_review(env):
    await post_webhook(env["client"], pr_payload(sha="one"), delivery="a")
    await post_webhook(env["client"], pr_payload(sha="two"), delivery="b")
    assert await job_count(env) == 2


async def test_non_pr_event_ignored(env):
    r = await post_webhook(env["client"], {"zen": "hi"}, event="ping")
    assert r.json() == {"status": "ignored_event"}
    assert await job_count(env) == 0


async def test_irrelevant_action_ignored(env):
    r = await post_webhook(env["client"], pr_payload(action="labeled"))
    assert r.json() == {"status": "ignored_action"}
    assert await job_count(env) == 0


async def test_missing_delivery_header_is_400(env):
    body = json.dumps(pr_payload()).encode()
    r = await env["client"].post(
        "/webhooks/github", content=body, headers={"X-Hub-Signature-256": sign(body)}
    )
    assert r.status_code == 400


async def test_enqueue_failure_releases_delivery_id_for_redelivery(env, monkeypatch):
    async def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(env["queue"], "enqueue", boom)
    r = await post_webhook(env["client"], pr_payload(), delivery="retry-me")
    assert r.status_code == 503
    assert not await env["redis"].exists("delivery:retry-me")


async def test_correlation_id_returned_and_stored(env):
    r = await post_webhook(env["client"], pr_payload())
    cid = r.headers["x-request-id"]
    async with env["sessionmaker"]() as s:
        job = (await s.execute(select(Job))).scalar_one()
    assert job.correlation_id == cid


async def test_redelivery_after_failed_enqueue_recovers_the_job(env, monkeypatch):
    """DB commit succeeded but Redis failed: GitHub's redelivery must enqueue the orphaned job."""
    real = env["queue"].enqueue

    async def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(env["queue"], "enqueue", boom)
    r1 = await post_webhook(env["client"], pr_payload(), delivery="first")
    assert r1.status_code == 503 and await job_count(env) == 1
    assert (await env["queue"].depth())["ready"] == 0

    monkeypatch.setattr(env["queue"], "enqueue", real)
    r2 = await post_webhook(env["client"], pr_payload(), delivery="second")
    assert r2.status_code == 200
    assert (await env["queue"].depth())["ready"] == 1


async def test_status_is_stored_as_lowercase_value(env):
    from sqlalchemy import text

    await post_webhook(env["client"], pr_payload())
    async with env["sessionmaker"]() as s:
        raw = (await s.execute(text("select status from jobs"))).scalar_one()
    assert raw == "queued"
