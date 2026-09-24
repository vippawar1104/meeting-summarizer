import hashlib
import hmac
import json
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel import col

from app.core.logging import correlation_id
from app.db.models import Job, JobStatus

router = APIRouter()
log = structlog.get_logger()

REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> Response:
    settings = request.app.state.settings
    body = await request.body()

    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="missing delivery id")

    redis = request.app.state.redis
    dedupe_key = f"delivery:{x_github_delivery}"
    if not await redis.set(dedupe_key, "1", nx=True, ex=settings.delivery_ttl_seconds):
        return _ok("duplicate_delivery")

    if x_github_event != "pull_request":
        return _ok("ignored_event")
    payload: dict[str, Any] = json.loads(body)
    if payload.get("action") not in REVIEW_ACTIONS:
        return _ok("ignored_action")

    try:
        job = _job_from_payload(payload, x_github_delivery)
        async with request.app.state.sessionmaker() as session:
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                # Same installation + PR + head SHA already has a job: never review twice.
                await session.rollback()
                # A previous attempt may have committed the row but failed to enqueue it. The
                # enqueue is idempotent, so re-offering a still-queued job is always safe.
                existing = (
                    await session.execute(
                        select(Job).where(col(Job.idempotency_key) == job.idempotency_key)
                    )
                ).scalar_one_or_none()
                if existing is not None and existing.status == JobStatus.QUEUED:
                    await request.app.state.queue.enqueue(
                        existing.id, existing.installation_id, existing.changed_lines
                    )
                return _ok("duplicate_review")
        await request.app.state.queue.enqueue(job.id, job.installation_id, job.changed_lines)
    except Exception:
        # Free the delivery id so GitHub's redelivery is not swallowed as a duplicate.
        await redis.delete(dedupe_key)
        log.exception("webhook_failed", delivery=x_github_delivery)
        raise HTTPException(status_code=503, detail="temporarily unavailable") from None

    log.info("job_enqueued", job_id=job.id, repo=job.repo_full_name, pr=job.pr_number)
    return _ok("enqueued")


def _job_from_payload(payload: dict[str, Any], delivery_id: str) -> Job:
    pr = payload["pull_request"]
    installation_id = int(payload["installation"]["id"])
    repo = payload["repository"]["full_name"]
    number = int(pr["number"])
    sha = pr["head"]["sha"]
    return Job(
        idempotency_key=f"{installation_id}:{repo}:{number}:{sha}",
        installation_id=installation_id,
        repo_full_name=repo,
        pr_number=number,
        head_sha=sha,
        changed_lines=int(pr.get("additions", 0)) + int(pr.get("deletions", 0)),
        delivery_id=delivery_id,
        correlation_id=correlation_id.get(),
    )


def _ok(status: str) -> Response:
    return Response(
        content=json.dumps({"status": status}), media_type="application/json", status_code=200
    )
