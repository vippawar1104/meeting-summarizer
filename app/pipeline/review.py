import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.billing.plans import PlanStatus, plan_status
from app.billing.usage import record_review
from app.core.config import Settings
from app.cost.cache import ReviewCache
from app.cost.guard import BudgetExceeded, current_installation
from app.db.models import FindingRow, Job
from app.feedback.apply import suppressed_fingerprints
from app.github.client import GitHubClient, GitHubNotFound, GitHubValidation
from app.github.diff import parse_diff
from app.llm.base import Completer, RouterLike
from app.llm.byok import ByokError
from app.pipeline.config import RepoConfig, parse_repo_config
from app.pipeline.filter import filter_files
from app.pipeline.findings import Category, Finding, Severity, parse_findings
from app.pipeline.hunks import HunkGroup, group_files, select_within_budget
from app.pipeline.merge import MergeStats, merge_findings
from app.pipeline.post import (
    FINDING_MARKER_RE,
    SummaryInfo,
    build_review_payload,
    build_summary,
    fingerprint,
    has_review_marker,
)
from app.pipeline.prompts import (
    load_system_prompt,
    render_context,
    render_feedback,
    render_user_prompt,
)
from app.pipeline.review_llm import GroupResult, review_group
from app.queue.errors import PermanentError, SkipJob
from app.rag.feedback import FeedbackContext, past_feedback
from app.rag.retrieval import Retriever
from app.safety.injection import InjectionSignal, detect, scan_diff
from app.safety.redact import RedactionReport, redact_diff, redact_text

log = structlog.get_logger()


@dataclass
class ReviewOutcome:
    posted: list[Finding] = field(default_factory=list)
    raw_findings: list[Finding] = field(default_factory=list)  # model output before verification
    review_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    groups_total: int = 0
    groups_failed: int = 0
    parse_failures: int = 0
    repairs: int = 0
    context_chunks: int = 0
    cache_hits: int = 0
    groups_budget_skipped: int = 0
    redactions: int = 0
    hidden_chars: int = 0
    injection_signals: int = 0
    partial: bool = False
    merge: MergeStats = field(default_factory=MergeStats)


class _AllSkippedForBudget(Exception):
    """Every section was refused because the installation's daily token budget is used up."""


class ReviewPipeline:
    """diff -> hunks -> filter -> LLM per group -> merge/verify/rank -> one GitHub review."""

    def __init__(
        self,
        github: GitHubClient,
        router: Completer,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        retriever: Retriever | None = None,
        cache: ReviewCache | None = None,
        byok: Callable[[int], Awaitable[tuple[RouterLike, str] | None]] | None = None,
    ) -> None:
        self._gh, self._router, self._s, self._sm = github, router, settings, sessionmaker
        self._retriever, self._cache, self._byok = retriever, cache, byok
        if retriever is not None and settings.prompt_version == "v1":
            raise ValueError(
                "repository context needs prompt v2 or later (v1 does not describe it)"
            )
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
            own = await self._own_router(job)
            if own is None and not getattr(self._router, "providers", True):
                await self._byok_notice(job, "No AI model is available for this installation.")
                raise SkipJob("no AI model configured: add your own API key in the dashboard")
            if (
                own is None
            ):  # their own key means their own bill: the free tier is for platform models
                plan = await plan_status(self._sm, self._s, inst)
                if not plan.allowed:
                    await self._limit_notice(job, plan)
                    raise SkipJob("free-tier monthly review limit reached")

            diff_text = await self._gh.get_diff(inst, repo, number)
            # Config comes from the BASE branch: a PR must not be able to rewrite its own rules.
            raw_cfg = await self._gh.get_file(inst, repo, ".reviewly.yml", pr["base"]["sha"])
        except GitHubNotFound as exc:
            raise PermanentError(f"not found on GitHub: {exc}") from exc
        config, config_warning = parse_repo_config(raw_cfg)

        token = current_installation.set(inst)  # LLM calls below are metered to this installation
        try:
            return await self._review(job, pr, diff_text, config, config_warning, own)
        finally:
            current_installation.reset(token)

    async def _review(
        self,
        job: Job,
        pr: dict[str, Any],
        diff_text: str,
        config: RepoConfig,
        config_warning: str | None,
        own: tuple[RouterLike, str] | None = None,
    ) -> ReviewOutcome:
        inst, repo, number = job.installation_id, job.repo_full_name, job.pr_number
        title = pr.get("title") or ""
        router: Completer = own[0] if own else self._router
        model_tag = own[1] if own else "platform"
        # Repo-context search embeds and reranks with the platform's providers; a BYOK installation's
        # code goes only to the provider it chose, so retrieval is skipped for it.
        retriever = None if own else self._retriever

        parsed = parse_diff(diff_text)
        # Scan for injection attempts on the raw text, then redact: nothing below (prompts, context
        # lookups, the cache key) ever sees an unredacted secret or an invisible character.
        signals = scan_diff(parsed)
        title_labels = detect(title)
        red = RedactionReport()
        if self._s.redaction_enabled:
            red = redact_diff(parsed)
            title, _ = redact_text(title)
        kept = filter_files(parsed, config.ignore).kept
        if not kept:
            raise SkipJob("no reviewable files")
        valid_lines = {f.path: f.commentable_lines for f in kept}
        groups = group_files(kept, self._s.max_group_chars)
        chosen, dropped = select_within_budget(groups, self._s.max_review_chars)

        out = ReviewOutcome(
            groups_total=len(chosen),
            partial=bool(dropped),
            redactions=red.total,
            hidden_chars=red.hidden_chars,
            injection_signals=len(signals) + len(title_labels) + (1 if red.hidden_chars else 0),
        )
        changed_paths = {f.path for f in parsed} | {f.old_path for f in parsed if f.old_path}
        feedback = await self._feedback(job, changed_paths) if retriever else None
        results = await self._review_all(
            chosen, title, config, job, changed_paths, feedback, router, retriever, model_tag
        )
        try:
            findings = self._collect(results, out)
        except _AllSkippedForBudget:
            await self._budget_notice(job)
            raise SkipJob("daily token budget exhausted") from None
        out.raw_findings = list(findings)
        if self._s.injection_findings:
            findings += self._injection_findings(signals)

        max_comments = config.max_comments or self._s.max_comments
        final, stats = merge_findings(
            findings,
            valid_lines=valid_lines,
            min_confidence=config.min_confidence,
            max_comments=max_comments,
            suppressed=await self._suppressed(job),
        )
        out.merge, out.posted = stats, final

        model = next((r.model for r in results if isinstance(r, GroupResult) and r.model), "")
        info = SummaryInfo(
            files_reviewed=len({g.path for g in chosen}),
            groups_total=out.groups_total,
            groups_failed=out.groups_failed,
            budget_skipped=out.groups_budget_skipped,
            redactions=out.redactions,
            hidden_chars=out.hidden_chars,
            title_flagged=bool(title_labels),
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
        await self._after_post(job, out)
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

    async def _feedback(self, job: Job, paths: set[str]) -> FeedbackContext | None:
        try:
            return await past_feedback(self._sm, job.installation_id, job.repo_full_name, paths)
        except Exception:
            log.exception("feedback_lookup_failed", job_id=job.id)
            return None

    async def _review_all(
        self,
        groups: list[HunkGroup],
        title: str,
        config: RepoConfig,
        job: Job,
        changed_paths: set[str],
        feedback: FeedbackContext | None,
        router: Completer,
        retriever: Retriever | None,
        model_tag: str,
    ) -> list[GroupResult | BaseException]:
        sem = asyncio.Semaphore(self._s.review_concurrency)

        async def one(g: HunkGroup) -> GroupResult:
            async with sem:
                context, extra = [], None
                if retriever is not None:
                    try:  # missing context must never fail a review
                        extra = await retriever.retrieve(
                            job.installation_id, job.repo_full_name, g, changed_paths
                        )
                        context = extra.chunks
                    except Exception:
                        log.exception("retrieval_failed", job_id=job.id, path=g.path)
                user = render_user_prompt(
                    g, pr_title=title, rules=config.rules, context=context, feedback=feedback
                )
                key = None
                if self._cache is not None:
                    key = ReviewCache.key(
                        self._s.prompt_version,
                        self._system,
                        g.norm_text or g.text,
                        config.rules,
                        render_context(context) if context else "",
                        render_feedback(feedback) if feedback else "",
                        model_tag,
                    )
                    hit = await self._cache_get(job.installation_id, key)
                    if hit is not None:
                        cached = parse_findings(hit)
                        if not cached.errors:
                            return GroupResult(findings=cached.findings, cache_hit=True)
                result = await review_group(router, self._system, user)
                if key is not None and result.clean:
                    await self._cache_put(job.installation_id, key, result.raw_text)
                if extra is not None:
                    result.prompt_tokens += extra.prompt_tokens
                    result.completion_tokens += extra.completion_tokens
                    result.cost_usd += extra.cost_usd
                    result.context_chunks = len(context)
                return result

        return list(await asyncio.gather(*(one(g) for g in groups), return_exceptions=True))

    async def _own_router(self, job: Job) -> tuple[RouterLike, str] | None:
        """The installation's own provider, if it configured one. If it is configured but broken we
        stop and say so: silently falling back to Reviewly's models would send their code to a
        provider they did not choose and spend the platform's tokens."""
        if self._byok is None:
            return None
        try:
            return await self._byok(job.installation_id)
        except ByokError as exc:
            await self._byok_notice(job, str(exc))
            raise SkipJob("the installation's own API key configuration is invalid") from None

    async def _byok_notice(self, job: Job, reason: str) -> None:
        body = (
            f"**Reviewly** could not review this pull request. {reason} Update it in the dashboard "
            f"({self._s.public_url}/) and push a new commit.\n\n<!-- reviewly:byok:{job.head_sha} -->"
        )
        try:
            await self._gh.create_comment(
                job.installation_id, job.repo_full_name, job.pr_number, body
            )
        except Exception:
            log.exception("byok_notice_failed", job_id=job.id)

    async def _suppressed(self, job: Job) -> frozenset[str]:
        try:
            return await suppressed_fingerprints(self._sm, job.installation_id, job.repo_full_name)
        except Exception:
            log.exception("suppression_lookup_failed", job_id=job.id)  # never block a review
            return frozenset()

    async def _limit_notice(self, job: Job, plan: PlanStatus) -> None:
        body = (
            f"**Reviewly** did not review this pull request: this installation has used its "
            f"{plan.limit} free reviews for this month. Upgrade at "
            f"{self._s.public_url}/ to keep getting reviews, or wait for the monthly reset."
            f"\n\n<!-- reviewly:limit:{job.head_sha} -->"
        )
        try:
            await self._gh.create_comment(
                job.installation_id, job.repo_full_name, job.pr_number, body
            )
        except Exception:
            log.exception("limit_notice_failed", job_id=job.id)

    async def _after_post(self, job: Job, out: ReviewOutcome) -> None:
        """Best effort, after the review is already on GitHub: never fail the job over bookkeeping."""
        try:
            await record_review(
                self._sm,
                job.installation_id,
                prompt_tokens=out.prompt_tokens,
                completion_tokens=out.completion_tokens,
                cost_usd=out.cost_usd,
                findings=len(out.posted),
            )
        except Exception:
            log.exception("usage_record_failed", job_id=job.id)
        if out.review_id and out.posted:
            await self._link_comments(job, out.review_id)

    async def _link_comments(self, job: Job, review_id: int) -> None:
        """Remember which GitHub comment carries which finding, so reactions and replies can be
        traced back to it. The comment body holds a hidden fingerprint marker to match on."""
        try:
            comments = await self._gh.list_review_comments(
                job.installation_id, job.repo_full_name, job.pr_number, review_id
            )
        except Exception:
            log.exception("comment_link_failed", job_id=job.id)
            return
        ids: dict[str, list[int]] = {}
        for c in comments:
            m = FINDING_MARKER_RE.search(c.get("body") or "")
            if m:
                ids.setdefault(m[1], []).append(int(c["id"]))
        async with self._sm() as s:
            rows = (
                (await s.execute(select(FindingRow).where(col(FindingRow.job_id) == job.id)))
                .scalars()
                .all()
            )
            for row in rows:
                if ids.get(row.fingerprint):
                    row.github_comment_id = ids[row.fingerprint].pop(0)
            await s.commit()

    async def _cache_get(self, inst: int, key: str) -> str | None:
        try:
            assert self._cache is not None
            return await self._cache.get(inst, key)
        except Exception:
            log.exception("cache_read_failed")  # a cache outage must never fail a review
            return None

    async def _cache_put(self, inst: int, key: str, response: str) -> None:
        try:
            assert self._cache is not None
            await self._cache.put(inst, key, response)
        except Exception:
            log.exception("cache_write_failed")

    async def _budget_notice(self, job: Job) -> None:
        body = (
            "**Reviewly** did not review this pull request: this installation's daily AI budget "
            "has been used up. It resets at 00:00 UTC; push a new commit after that to get a "
            f"review.\n\n<!-- reviewly:budget:{job.head_sha} -->"
        )
        try:
            await self._gh.create_comment(
                job.installation_id, job.repo_full_name, job.pr_number, body
            )
        except Exception:
            log.exception("budget_notice_failed", job_id=job.id)

    @staticmethod
    def _injection_findings(signals: list[InjectionSignal]) -> list[Finding]:
        """Make attempts to instruct the AI reviewer visible to humans (at most three)."""
        by_line: dict[tuple[str, int], list[str]] = {}
        for sig in signals:
            if sig.file is not None and sig.line is not None:
                by_line.setdefault((sig.file, sig.line), []).append(sig.label)
        return [
            Finding(
                file=file,
                line=line,
                severity=Severity.LOW,
                category=Category.SECURITY,
                message=(
                    "This line contains text that looks like an instruction to an AI code "
                    f"reviewer ({', '.join(sorted(set(labels)))}). Reviewly treats code and "
                    "comments as data and ignored it, but that is unusual in source code and "
                    "worth a human look."
                ),
                confidence=0.9,
            )
            for (file, line), labels in list(by_line.items())[:3]
        ]

    @staticmethod
    def _collect(results: list[GroupResult | BaseException], out: ReviewOutcome) -> list[Finding]:
        findings: list[Finding] = []
        errors: list[BaseException] = []
        for r in results:
            if isinstance(r, BaseException):
                if isinstance(r, asyncio.CancelledError):
                    raise r
                if isinstance(r, BudgetExceeded):
                    out.groups_budget_skipped += 1
                else:
                    out.groups_failed += 1
                    errors.append(r)
                continue
            out.cache_hits += r.cache_hit
            findings += r.findings
            out.prompt_tokens += r.prompt_tokens
            out.completion_tokens += r.completion_tokens
            out.cost_usd += r.cost_usd
            out.repairs += r.repaired
            out.context_chunks += r.context_chunks
            out.parse_failures += r.parse_failed
        if out.groups_total and out.groups_failed + out.groups_budget_skipped == out.groups_total:
            if errors:
                # Every call failed (provider outage): raise so the job is retried, not "reviewed".
                raise errors[0]
            raise _AllSkippedForBudget
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
