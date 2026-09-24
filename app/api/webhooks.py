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
SUPPORTED_EVENTS = {"pull_request", "push", "installation", "installation_repositories"}
# Indexing is slow; a large "size" keeps it behind reviews inside the same installation.
INDEX_PRIORITY_LINES = 2000


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

    if x_github_event not in SUPPORTED_EVENTS:
        return _ok("ignored_event")
    payload: dict[str, Any] = json.loads(body)
    jobs = jobs_for_event(x_github_event, payload, x_github_delivery)
    if not jobs:
        return _ok("ignored_action")

    try:
        outcomes = [await _persist_and_enqueue(request, job) for job in jobs]
    except Exception:
        # Free the delivery id so GitHub's redelivery is not swallowed as a duplicate.
        await redis.delete(dedupe_key)
        log.exception("webhook_failed", delivery=x_github_delivery)
        raise HTTPException(status_code=503, detail="temporarily unavailable") from None

    for job in jobs:
        log.info("job_enqueued", job_id=job.id, kind=job.kind, repo=job.repo_full_name)
    return _ok("enqueued" if any(outcomes) else "duplicate_review")


async def _persist_and_enqueue(request: Request, job: Job) -> bool:
    """True if the job was newly created. A duplicate key never creates a second job."""
    queue = request.app.state.queue
    async with request.app.state.sessionmaker() as session:
        session.add(job)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            # A previous attempt may have committed the row but failed to enqueue it. The
            # enqueue is idempotent, so re-offering a still-queued job is always safe.
            existing = (
                await session.execute(
                    select(Job).where(col(Job.idempotency_key) == job.idempotency_key)
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status == JobStatus.QUEUED:
                await queue.enqueue(existing.id, existing.installation_id, existing.changed_lines)
            return False
    await queue.enqueue(job.id, job.installation_id, job.changed_lines)
    return True


def jobs_for_event(event: str, payload: dict[str, Any], delivery_id: str) -> list[Job]:
    installation = payload.get("installation") or {}
    if "id" not in installation:
        return []
    inst = int(installation["id"])
    action = payload.get("action")

    if event == "pull_request":
        return [_review_job(payload, delivery_id)] if action in REVIEW_ACTIONS else []

    if event == "push":
        repo = payload["repository"]["full_name"]
        default_branch = payload["repository"].get("default_branch")
        after = payload.get("after") or ""
        if payload.get("deleted") or payload.get("ref") != f"refs/heads/{default_branch}":
            return []  # only the default branch is indexed
        return [_index_job(inst, repo, after, f"index:{inst}:{repo}:{after}", delivery_id)]

    if event == "installation":
        if action == "created":
            return [
                _index_job(
                    inst,
                    r["full_name"],
                    "HEAD",
                    f"index:{inst}:{r['full_name']}:install:{delivery_id}",
                    delivery_id,
                )  # noqa: E501
                for r in payload.get("repositories", [])
            ]
        if action == "deleted":
            return [_purge_job(inst, "*", delivery_id)]
        return []

    # installation_repositories
    if action == "added":
        return [
            _index_job(
                inst,
                r["full_name"],
                "HEAD",
                f"index:{inst}:{r['full_name']}:install:{delivery_id}",
                delivery_id,
            )  # noqa: E501
            for r in payload.get("repositories_added", [])
        ]
    if action == "removed":
        return [
            _purge_job(inst, r["full_name"], delivery_id)
            for r in payload.get("repositories_removed", [])
        ]  # noqa: E501
    return []


def _review_job(payload: dict[str, Any], delivery_id: str) -> Job:
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


def _index_job(inst: int, repo: str, sha: str, key: str, delivery_id: str) -> Job:
    return Job(
        kind="index",
        idempotency_key=key,
        installation_id=inst,
        repo_full_name=repo,
        pr_number=0,
        head_sha=sha,
        changed_lines=INDEX_PRIORITY_LINES,
        delivery_id=delivery_id,
        correlation_id=correlation_id.get(),
    )


def _purge_job(inst: int, repo: str, delivery_id: str) -> Job:
    return Job(
        kind="purge",
        idempotency_key=f"purge:{inst}:{repo}:{delivery_id}",
        installation_id=inst,
        repo_full_name=repo,
        pr_number=0,
        head_sha="-",
        delivery_id=delivery_id,
        correlation_id=correlation_id.get(),
    )


def _ok(status: str) -> Response:
    return Response(
        content=json.dumps({"status": status}), media_type="application/json", status_code=200
    )
