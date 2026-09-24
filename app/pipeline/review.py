import asyncio
from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import FindingRow, Job
from app.github.client import GitHubClient, GitHubNotFound, GitHubValidation
from app.github.diff import parse_diff
from app.llm.router import LLMRouter
from app.pipeline.config import RepoConfig, parse_repo_config
from app.pipeline.filter import filter_files
from app.pipeline.findings import Finding
from app.pipeline.hunks import HunkGroup, group_files, select_within_budget
from app.pipeline.merge import MergeStats, merge_findings
from app.pipeline.post import (
    SummaryInfo,
    build_review_payload,
    build_summary,
    fingerprint,
    has_review_marker,
)
from app.pipeline.prompts import load_system_prompt, render_user_prompt
from app.pipeline.review_llm import GroupResult, review_group
from app.queue.errors import PermanentError, SkipJob

log = structlog.get_logger()


@dataclass
class ReviewOutcome:
    posted: list[Finding] = field(default_factory=list)
    review_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    groups_total: int = 0
    groups_failed: int = 0
    parse_failures: int = 0
    repairs: int = 0
    partial: bool = False
    merge: MergeStats = field(default_factory=MergeStats)


class ReviewPipeline:
    """diff -> hunks -> filter -> LLM per group -> merge/verify/rank -> one GitHub review."""

    def __init__(
        self,
        github: GitHubClient,
        router: LLMRouter,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._gh, self._router, self._s, self._sm = github, router, settings, sessionmaker
        self._system = load_system_prompt(settings.prompt_version)

    async def run(self, job: Job) -> ReviewOutcome:
        inst, repo, number = job.installation_id, job.repo_full_name, job.pr_number
        try:
            pr = await self._gh.get_pr(inst, repo, number)
            if pr.get("state") != "open":
                raise SkipJob("pull request is not open")
            if pr["head"]["sha"] != job.head_sha:
                raise SkipJob("superseded by a newer commit")
            # A retry after a partial failure must not post a second review.
            if has_review_marker(await self._gh.list_reviews(inst, repo, number), job.head_sha):
                raise SkipJob("already reviewed")

            diff_text = await self._gh.get_diff(inst, repo, number)
            # Config comes from the BASE branch: a PR must not be able to rewrite its own rules.
            raw_cfg = await self._gh.get_file(inst, repo, ".reviewly.yml", pr["base"]["sha"])
        except GitHubNotFound as exc:
            raise PermanentError(f"not found on GitHub: {exc}") from exc
        config, config_warning = parse_repo_config(raw_cfg)

        kept = filter_files(parse_diff(diff_text), config.ignore).kept
        if not kept:
            raise SkipJob("no reviewable files")
        valid_lines = {f.path: f.commentable_lines for f in kept}
        groups = group_files(kept, self._s.max_group_chars)
        chosen, dropped = select_within_budget(groups, self._s.max_review_chars)

        out = ReviewOutcome(groups_total=len(chosen), partial=bool(dropped))
        results = await self._review_all(chosen, pr.get("title") or "", config)
        findings = self._collect(results, out)

        max_comments = config.max_comments or self._s.max_comments
        final, stats = merge_findings(
            findings,
            valid_lines=valid_lines,
            min_confidence=config.min_confidence,
            max_comments=max_comments,
        )
        out.merge, out.posted = stats, final

        model = next((r.model for r in results if isinstance(r, GroupResult) and r.model), "")
        info = SummaryInfo(
            files_reviewed=len({g.path for g in chosen}),
            groups_total=out.groups_total,
            groups_failed=out.groups_failed,
            skipped_for_size=sorted({g.path for g in dropped}),
            not_shown=stats.over_cap,
            config_warning=config_warning,
            model=model,
            prompt_version=self._s.prompt_version,
        )
        summary = build_summary(final, info, job.head_sha)
        try:
            review = await self._gh.create_review(
                inst, repo, number, build_review_payload(final, summary, job.head_sha)
            )
        except GitHubValidation:
            log.warning("inline_comments_rejected", job_id=job.id)
            review = await self._gh.create_review(
                inst, repo, number, build_review_payload(final, summary, job.head_sha, inline=False)
            )
        out.review_id = review.get("id")
        await self._persist(job, out)
        log.info(
            "review_posted",
            job_id=job.id,
            findings=len(final),
            groups=out.groups_total,
            failed=out.groups_failed,
            partial=out.partial,
            cost_usd=round(out.cost_usd, 6),
        )
        return out

    async def _review_all(
        self, groups: list[HunkGroup], title: str, config: RepoConfig
    ) -> list[GroupResult | BaseException]:
        sem = asyncio.Semaphore(self._s.review_concurrency)

        async def one(g: HunkGroup) -> GroupResult:
            async with sem:
                user = render_user_prompt(g, pr_title=title, rules=config.rules)
                return await review_group(self._router, self._system, user)

        return list(await asyncio.gather(*(one(g) for g in groups), return_exceptions=True))

    @staticmethod
    def _collect(results: list[GroupResult | BaseException], out: ReviewOutcome) -> list[Finding]:
        findings: list[Finding] = []
        errors: list[BaseException] = []
        for r in results:
            if isinstance(r, BaseException):
                if isinstance(r, asyncio.CancelledError):
                    raise r
                out.groups_failed += 1
                errors.append(r)
                continue
            findings += r.findings
            out.prompt_tokens += r.prompt_tokens
            out.completion_tokens += r.completion_tokens
            out.cost_usd += r.cost_usd
            out.repairs += r.repaired
            out.parse_failures += r.parse_failed
        if errors and out.groups_failed == out.groups_total:
            # Every call failed (provider outage): raise so the job is retried, not "reviewed".
            raise errors[0]
        return findings

    async def _persist(self, job: Job, out: ReviewOutcome) -> None:
        async with self._sm() as s:
            row = await s.get(Job, job.id)
            if row is not None:
                row.review_id = out.review_id
                row.prompt_tokens = out.prompt_tokens
                row.completion_tokens = out.completion_tokens
                row.cost_usd = out.cost_usd
            for f in out.posted:
                s.add(
                    FindingRow(
                        job_id=job.id,
                        installation_id=job.installation_id,
                        repo_full_name=job.repo_full_name,
                        pr_number=job.pr_number,
                        file=f.file,
                        line=f.line,
                        severity=f.severity,
                        category=f.category,
                        message=f.message,
                        suggested_patch=f.suggested_patch,
                        confidence=f.confidence,
                        fingerprint=fingerprint(f),
                    )
                )
            await s.commit()
