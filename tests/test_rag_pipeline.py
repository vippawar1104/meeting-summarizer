import json

import pytest

from app.db.models import FindingRow, Job
from app.pipeline.review import ReviewPipeline
from app.rag.indexer import RepoIndexer
from app.rag.rerank import LLMReranker
from app.rag.retrieval import Retriever
from app.rag.store import MemoryChunkStore
from tests.helpers import add_job, make_settings
from tests.rag_helpers import FakeRepoGitHub, SpyEmbedder
from tests.test_feedback_prompt_v2 import row, seed
from tests.test_review_pipeline import FakeGitHub, ScriptedRouter, finding, reply
from tests.test_worker import load

UTILS = b"def total(values):\n    return sum(values)\n"
CALC = b"def add(a, b):\n    return a + b\n"


async def build(wenv, *, files=None, router=None, **settings):
    files = files if files is not None else {"math_utils.py": UTILS}
    s = make_settings(prompt_version="v2", **settings)
    emb, store = SpyEmbedder(), MemoryChunkStore()
    await RepoIndexer(FakeRepoGitHub(files), emb, store, s).index_repo(1, "a/b")
    router = router or ScriptedRouter(lambda m: reply())
    retriever = Retriever(emb, store, LLMReranker(router), s)
    gh = FakeGitHub()
    [jid] = await add_job(wenv["sm"], wenv["queue"])
    pipeline = ReviewPipeline(gh, router, s, wenv["sm"], retriever)
    return pipeline, router, gh, await load(wenv, jid), retriever


async def test_related_code_is_added_to_the_prompt_inside_a_data_block(wenv):
    pipeline, router, gh, job, _ = await build(wenv)
    out = await pipeline.run(job)
    prompt = router.user_prompts()[0]
    assert "<untrusted_context>" in prompt and "math_utils.py" in prompt and "def total" in prompt
    assert prompt.index("</untrusted_context>") < prompt.index("<untrusted_diff>")
    assert out.context_chunks >= 1 and gh.created


async def test_context_never_includes_the_file_the_pr_changes(wenv):
    pipeline, router, gh, job, _ = await build(
        wenv, files={"app/calc.py": CALC, "math_utils.py": UTILS}
    )
    await pipeline.run(job)
    context = (
        router.user_prompts()[0].split("<untrusted_context>")[1].split("</untrusted_context>")[0]
    )
    assert "app/calc.py" not in context and "math_utils.py" in context


async def test_v1_prompt_cannot_be_combined_with_retrieval(wenv):
    _, _, _, _, retriever = await build(wenv)
    with pytest.raises(ValueError, match="prompt v2"):
        ReviewPipeline(
            FakeGitHub(),
            ScriptedRouter(lambda m: reply()),
            make_settings(prompt_version="v1"),
            wenv["sm"],
            retriever,
        )


async def test_without_a_retriever_the_prompt_has_no_context_section(wenv):
    [jid] = await add_job(wenv["sm"], wenv["queue"])
    router = ScriptedRouter(lambda m: reply())
    await ReviewPipeline(FakeGitHub(), router, make_settings(), wenv["sm"]).run(
        await load(wenv, jid)
    )
    assert "untrusted_context" not in router.user_prompts()[0]


async def test_retrieval_failure_never_fails_the_review(wenv):
    pipeline, router, gh, job, retriever = await build(
        wenv, router=ScriptedRouter(lambda m: reply(finding()))
    )

    async def boom(*a, **k):
        raise ConnectionError("vector store down")

    retriever.retrieve = boom
    out = await pipeline.run(job)
    assert len(gh.created) == 1 and len(out.posted) == 1
    assert "untrusted_context" not in router.user_prompts()[0]


async def test_embedding_outage_during_retrieval_is_tolerated(wenv):
    pipeline, router, gh, job, retriever = await build(wenv)
    retriever._embedder.fail = True
    await pipeline.run(job)
    assert len(gh.created) == 1


async def test_rerank_and_review_spend_are_both_recorded_on_the_job(wenv):
    def respond(messages):
        return '{"scores": []}' if "untrusted_snippet" in messages[1].content else reply()

    files = {
        "math_utils.py": UTILS,
        "other_utils.py": b"def total_all(rows):\n    return sum(rows)\n",
    }
    pipeline, router, gh, job, _ = await build(wenv, files=files, router=ScriptedRouter(respond))
    await pipeline.run(job)
    async with wenv["sm"]() as s:
        row_ = await s.get(Job, job.id)
    assert len(router.calls) == 2  # one rerank call + one review call
    assert row_.prompt_tokens == 200 and row_.cost_usd == pytest.approx(0.002)


async def test_maintainer_feedback_reaches_the_prompt(wenv):
    pipeline, router, gh, job, _ = await build(wenv)
    await seed(
        wenv,
        row("dismissed", file="app/calc.py", msg="Consider renaming variable", repo="a/b", inst=1),
    )
    await pipeline.run(job)
    prompt = router.user_prompts()[0]
    assert "<untrusted_feedback>" in prompt and "Consider renaming variable" in prompt


async def test_feedback_from_another_installation_is_not_used(wenv):
    pipeline, router, gh, job, _ = await build(wenv)
    await seed(wenv, row("dismissed", msg="tenant two secret", repo="a/b", inst=2))
    await pipeline.run(job)
    assert "tenant two secret" not in router.user_prompts()[0]


async def test_feedback_lookup_failure_is_tolerated(wenv, monkeypatch):
    import app.pipeline.review as review_mod

    async def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(review_mod, "past_feedback", boom)
    pipeline, router, gh, job, _ = await build(wenv)
    await pipeline.run(job)
    assert len(gh.created) == 1 and "untrusted_feedback" not in router.user_prompts()[0]


async def test_findings_are_still_verified_and_persisted_with_context_on(wenv):
    pipeline, router, gh, job, _ = await build(
        wenv, router=ScriptedRouter(lambda m: reply(finding(line=3), finding(line=999)))
    )
    out = await pipeline.run(job)
    assert [f.line for f in out.posted] == [3] and out.merge.line_not_in_diff == 1
    async with wenv["sm"]() as s:
        from sqlalchemy import select

        assert len((await s.execute(select(FindingRow))).scalars().all()) == 1
    assert json.loads(json.dumps(gh.created[0]["comments"]))[0]["line"] == 3
