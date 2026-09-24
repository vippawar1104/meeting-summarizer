import asyncio
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from app.core.config import Settings
from app.cost.guard import GuardedRouter
from app.db.models import Job
from app.db.session import make_engine, make_sessionmaker
from app.github.diff import parse_diff
from app.llm.base import LLMProvider, RouterLike
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import GroqProvider, MistralProvider
from app.llm.router import LLMRouter
from app.pipeline.findings import Finding
from app.pipeline.merge import normalize_path
from app.pipeline.review import ReviewPipeline
from app.queue.errors import SkipJob
from eval.baselines import NullRouter, RegexRouter
from eval.cases import Case
from eval.metrics import CaseResult, FindingView, score_case
from eval.recordings import RecordingRouter, RecordingStore


class EvalGitHub:
    """Serves one case's diff to the real pipeline and captures what it would have posted."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.reviews: list[dict[str, Any]] = []

    async def get_pr(self, inst: int, repo: str, number: int) -> dict[str, Any]:
        return {
            "state": "open",
            "title": self.case.title,
            "head": {"sha": "eval"},
            "base": {"sha": "base"},
        }

    async def list_reviews(self, inst: int, repo: str, number: int) -> list[dict[str, Any]]:
        return []

    async def get_diff(self, inst: int, repo: str, number: int) -> str:
        return self.case.diff

    async def get_file(self, inst: int, repo: str, path: str, ref: str) -> str | None:
        return None

    async def create_review(
        self, inst: int, repo: str, number: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.reviews.append(payload)
        return {"id": 1}

    async def create_comment(self, inst: int, repo: str, number: int, body: str) -> None:
        return None


def is_baseline(model: str) -> bool:
    return model.startswith("baseline:")


PROVIDERS: dict[str, tuple[str, Callable[..., LLMProvider]]] = {
    "gemini": ("REVIEWLY_GEMINI_API_KEY", GeminiProvider),
    "groq": ("REVIEWLY_GROQ_API_KEY", GroqProvider),
    "mistral": ("REVIEWLY_MISTRAL_API_KEY", MistralProvider),
}


def split_model(spec: str) -> tuple[str, str]:
    """`groq:openai/gpt-oss-120b` -> ("groq", "openai/gpt-oss-120b"); a bare name means Gemini."""
    if not is_baseline(spec) and ":" in spec:
        provider, name = spec.split(":", 1)
        if provider in PROVIDERS:
            return provider, name
    return "gemini", spec


def key_env_var(spec: str) -> str | None:
    return None if is_baseline(spec) else PROVIDERS[split_model(spec)[0]][0]


def make_settings(model: str, prompt: str) -> Settings:
    return Settings(
        _env_file=None,
        provider_order=split_model(model)[0],
        prompt_version=prompt,
        review_concurrency=2,
        daily_token_budget=0,
        cache_enabled=False,
    )


def build_router(
    model: str,
    store: RecordingStore,
    mode: str,
    api_key: str | None,
    http: httpx.AsyncClient,
    rpm: float | None = None,
) -> RouterLike:
    if model == "baseline:null":
        return GuardedRouter(NullRouter())
    if model == "baseline:regex":
        return GuardedRouter(RegexRouter())
    inner: LLMRouter | None = None
    if mode != "replay":
        provider, name = split_model(model)
        env_var, cls = PROVIDERS[provider]
        if not api_key:
            raise SystemExit(
                f"{env_var} is not set: cannot call {model} live. Use --mode replay to score from recordings."
            )
        inner = LLMRouter([cls(api_key, name, http, timeout=120.0)])
    # Redaction runs first (in the guard), so recordings are keyed on exactly what a provider would see.
    return GuardedRouter(RecordingRouter(inner, store, model, mode, rpm=rpm))


def views(findings: list[Finding], commentable: dict[str, set[int]]) -> list[FindingView]:
    out = []
    for f in findings:
        path = normalize_path(f.file, commentable) or f.file
        out.append(
            FindingView(path, f.line, f.severity.value, f.category.value, f.message, f.confidence)
        )
    return out


async def run_case(
    case: Case, pipeline: ReviewPipeline, gh: EvalGitHub, store: RecordingStore, mode: str
) -> CaseResult:
    parsed = parse_diff(case.diff)
    commentable = {f.path: f.commentable_lines for f in parsed}
    job = Job(
        idempotency_key=f"eval:{case.id}",
        installation_id=1,
        repo_full_name="eval/case",
        pr_number=1,
        head_sha="eval",
        delivery_id="eval",
        correlation_id=case.id,
    )
    start = time.perf_counter()
    error: str | None = None
    outcome = None
    try:
        outcome = await pipeline.run(job)
    except SkipJob as exc:
        error = f"skipped: {exc}"
    except Exception as exc:  # a provider outage must not hide the other 57 results
        error = f"{type(exc).__name__}: {exc}"[:300]
    elapsed = time.perf_counter() - start

    if outcome is None:
        res = CaseResult(id=case.id, kind=case.kind, labels_total=len(case.labels), error=error)
    else:
        res = score_case(
            case,
            views(outcome.raw_findings, commentable),
            views(outcome.posted, commentable),
            commentable,
        )
        res.prompt_tokens, res.completion_tokens, res.cost_usd = (
            outcome.prompt_tokens,
            outcome.completion_tokens,
            outcome.cost_usd,
        )
    if mode == "replay":
        res.latency_s = store.case_latency.get(case.id, 0.0)
    else:
        if case.id not in store.case_latency and error is None:
            store.add_case_latency(case.id, elapsed)
        res.latency_s = store.case_latency.get(case.id, elapsed)
    return res


async def run_all(
    cases: list[Case],
    model: str,
    prompt: str,
    mode: str,
    concurrency: int,
    recordings: Path,
    api_key: str | None,
    rpm: float | None = None,
) -> list[CaseResult]:
    settings = make_settings(model, prompt)
    store = RecordingStore(recordings)
    tmp = tempfile.mkdtemp(prefix="reviewly-eval-")
    engine: AsyncEngine = make_engine(f"sqlite+aiosqlite:///{tmp}/eval.db")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sm = make_sessionmaker(engine)
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=120) as http:
        router = build_router(model, store, mode, api_key, http, rpm)

        async def one(case: Case) -> CaseResult:
            async with sem:
                gh = EvalGitHub(case)
                pipeline = ReviewPipeline(gh, router, settings, sm)  # type: ignore[arg-type]
                return await run_case(case, pipeline, gh, store, mode)

        results = await asyncio.gather(*(one(c) for c in cases))
    await engine.dispose()
    return list(results)
