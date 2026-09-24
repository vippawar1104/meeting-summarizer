"""Everything the dashboard shows, computed from Postgres. Every query is scoped to one installation."""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.billing.plans import plan_status
from app.billing.usage import period_of, usage_series
from app.core.config import Settings
from app.db.models import FindingRow, Job, JobStatus
from app.feedback.apply import rule_stats


def _round(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(x, n)


async def overview(
    sm: async_sessionmaker[AsyncSession], settings: Settings, installation_id: int
) -> dict[str, Any]:
    now = datetime.now(UTC)
    plan = await plan_status(sm, settings, installation_id, now)
    rules = await rule_stats(sm, installation_id)
    series = await usage_series(sm, installation_id, 6)
    this_month = next((u for u in series if u.period == period_of(now)), None)

    async with sm() as s:
        done = (
            (col(Job.installation_id) == installation_id)
            & (col(Job.kind) == "review")
            & (col(Job.status) == JobStatus.DONE)
        )
        totals = (
            await s.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(Job.prompt_tokens + Job.completion_tokens), 0),
                    func.coalesce(func.sum(Job.cost_usd), 0.0),
                ).where(done)
            )
        ).one()
        prs = (
            await s.execute(
                select(func.count()).select_from(
                    select(col(Job.repo_full_name), col(Job.pr_number))
                    .where(done)
                    .distinct()
                    .subquery()
                )
            )
        ).scalar_one()
        recent_rows = (
            await s.execute(
                select(
                    Job,
                    select(func.count())
                    .where(col(FindingRow.job_id) == Job.id)
                    .correlate(Job)
                    .scalar_subquery(),
                )
                .where((col(Job.installation_id) == installation_id) & (col(Job.kind) == "review"))
                .order_by(col(Job.created_at).desc())
                .limit(20)
            )
        ).all()
        repos = (
            await s.execute(
                select(
                    col(FindingRow.repo_full_name),
                    func.count(),
                    func.sum(case((col(FindingRow.feedback) == "accepted", 1), else_=0)),
                    func.sum(case((col(FindingRow.feedback) == "dismissed", 1), else_=0)),
                )
                .where(col(FindingRow.installation_id) == installation_id)
                .group_by(col(FindingRow.repo_full_name))
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()

    accepted = sum(r.accepted for r in rules)
    dismissed = sum(r.dismissed for r in rules)
    judged = accepted + dismissed
    return {
        "installation_id": installation_id,
        "plan": {"name": plan.plan, "limit": plan.limit, "used": plan.used},
        "period": period_of(now),
        "totals": {
            "reviews": totals[0],
            "prs": prs,
            "findings": sum(r.posted for r in rules),
            "accepted": accepted,
            "dismissed": dismissed,
            "resolved": sum(r.resolved for r in rules),
            "precision": _round(accepted / judged) if judged else None,
            "tokens": int(totals[1]),
            "cost_usd": _round(float(totals[2]), 6),
        },
        "month": {
            "reviews": this_month.reviews if this_month else 0,
            "findings": this_month.findings if this_month else 0,
            "tokens": (this_month.prompt_tokens + this_month.completion_tokens)
            if this_month
            else 0,
            "cost_usd": _round(this_month.cost_usd, 6) if this_month else 0.0,
        },
        "rules": [
            {
                **{k: v for k, v in asdict(r).items()},
                "pending": r.pending,
                "precision": _round(r.precision),
            }
            for r in rules
        ],
        "repos": [
            {
                "repo": repo,
                "findings": n,
                "accepted": a or 0,
                "dismissed": d or 0,
                "precision": _round((a or 0) / ((a or 0) + (d or 0)))
                if (a or 0) + (d or 0)
                else None,
            }
            for repo, n, a, d in repos
        ],
        "usage": [
            {
                "period": u.period,
                "reviews": u.reviews,
                "findings": u.findings,
                "tokens": u.prompt_tokens + u.completion_tokens,
                "cost_usd": _round(u.cost_usd, 6),
            }
            for u in series
        ],
        "recent": [
            {
                "repo": job.repo_full_name,
                "pr": job.pr_number,
                "sha": job.head_sha[:7],
                "status": job.status.value,
                "findings": n,
                "tokens": job.prompt_tokens + job.completion_tokens,
                "cost_usd": _round(job.cost_usd, 6),
                "note": job.last_error,
                "at": job.created_at.isoformat(),
            }
            for job, n in recent_rows
        ],
    }


async def findings_page(
    sm: async_sessionmaker[AsyncSession],
    installation_id: int,
    *,
    feedback: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = select(FindingRow).where(col(FindingRow.installation_id) == installation_id)
    if feedback == "pending":
        q = q.where(col(FindingRow.feedback).is_(None))
    elif feedback in ("accepted", "dismissed"):
        q = q.where(col(FindingRow.feedback) == feedback)
    async with sm() as s:
        rows = (
            (await s.execute(q.order_by(col(FindingRow.created_at).desc()).limit(min(limit, 200))))
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "repo": r.repo_full_name,
            "pr": r.pr_number,
            "file": r.file,
            "line": r.line,
            "severity": r.severity,
            "category": r.category,
            "message": r.message,
            "confidence": r.confidence,
            "feedback": r.feedback,
            "resolved": r.resolved,
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
