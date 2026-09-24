import pytest
from sqlalchemy import select

from app.db.models import Job, JobStatus
from app.queue.errors import PermanentError
from app.rag.indexer import RepoIndexer
from app.rag.store import MemoryChunkStore
from tests.conftest import post_webhook, pr_payload
from tests.helpers import make_settings
from tests.rag_helpers import PY_BILLING, PY_ICONS, FakeRepoGitHub, SpyEmbedder
from tests.test_worker import drain, load, make_worker
from worker.dispatch import dispatch
from worker.handlers import make_index_handler, make_purge_handler


def push(ref="refs/heads/main", after="c0ffee", default="main", deleted=False, inst=42):
    return {
        "ref": ref, "after": after, "deleted": deleted, "installation": {"id": inst},
        "repository": {"full_name": "acme/widgets", "default_branch": default},
    }  # fmt: skip


def install(action, repos, key="repositories", inst=42):
    return {"action": action, "installation": {"id": inst}, key: [{"full_name": r} for r in repos]}


async def jobs(env):
    async with env["sessionmaker"]() as s:
        return (await s.execute(select(Job).order_by(Job.repo_full_name))).scalars().all()


async def send(env, event, payload, delivery="d1"):
    return (await post_webhook(env["client"], payload, delivery=delivery, event=event)).json()[
        "status"
    ]


async def test_push_to_the_default_branch_enqueues_an_index_job(env):
    assert await send(env, "push", push()) == "enqueued"
    [job] = await jobs(env)
    assert (job.kind, job.head_sha, job.repo_full_name) == ("index", "c0ffee", "acme/widgets")
    assert job.idempotency_key == "index:42:acme/widgets:c0ffee" and job.pr_number == 0


@pytest.mark.parametrize(
    "payload",
    [
        push(ref="refs/heads/feature"),
        push(deleted=True),
        push(ref="refs/tags/v1"),
        push(default="trunk"),
    ],
)
async def test_pushes_that_should_not_be_indexed_are_ignored(env, payload):
    assert await send(env, "push", payload) == "ignored_action"
    assert await jobs(env) == []


async def test_redelivered_push_for_the_same_commit_is_indexed_once(env):
    await send(env, "push", push(), delivery="a")
    assert await send(env, "push", push(), delivery="b") == "duplicate_review"
    assert len(await jobs(env)) == 1


async def test_a_new_commit_gets_a_new_index_job(env):
    await send(env, "push", push(after="one"), delivery="a")
    await send(env, "push", push(after="two"), delivery="b")
    assert len(await jobs(env)) == 2


async def test_installation_created_indexes_every_repo(env):
    assert await send(env, "installation", install("created", ["a/x", "a/y"])) == "enqueued"
    got = await jobs(env)
    assert [(j.kind, j.repo_full_name, j.head_sha) for j in got] == [
        ("index", "a/x", "HEAD"),
        ("index", "a/y", "HEAD"),
    ]


async def test_repositories_added_are_indexed_and_removed_are_purged(env):
    await send(
        env,
        "installation_repositories",
        install("added", ["a/new"], "repositories_added"),
        delivery="a",
    )
    await send(
        env,
        "installation_repositories",
        install("removed", ["a/old"], "repositories_removed"),
        delivery="b",
    )
    assert [(j.kind, j.repo_full_name) for j in await jobs(env)] == [
        ("purge", "a/old"),
        ("index", "a/new"),
    ][::-1]


async def test_uninstalling_purges_everything_for_that_installation(env):
    await send(env, "installation", {"action": "deleted", "installation": {"id": 42}})
    [job] = await jobs(env)
    assert (job.kind, job.repo_full_name) == ("purge", "*")


@pytest.mark.parametrize("action", ["suspend", "unsuspend", "new_permissions_accepted"])
async def test_other_installation_actions_are_ignored(env, action):
    assert await send(env, "installation", install(action, [])) == "ignored_action"


async def test_events_without_an_installation_are_ignored(env):
    assert await send(env, "push", {"ref": "refs/heads/main"}) == "ignored_action"


async def test_pull_request_events_still_create_review_jobs(env):
    await send(env, "pull_request", pr_payload())
    [job] = await jobs(env)
    assert job.kind == "review" and job.pr_number == 7


async def test_index_jobs_queue_behind_reviews_from_the_same_installation(env):
    queue = env["queue"]
    await send(env, "push", push(), delivery="a")
    await send(env, "pull_request", pr_payload(installation=42), delivery="b")
    first = await queue.claim(visibility_ms=30_000, cap=5)
    async with env["sessionmaker"]() as s:
        assert (await s.get(Job, first.job_id)).kind == "review"


# ---- dispatch and handlers through the real worker --------------------------------------------


def handlers(files=None):
    gh = FakeRepoGitHub(files or {"billing.py": PY_BILLING, "icons.py": PY_ICONS})
    store = MemoryChunkStore()
    indexer = RepoIndexer(gh, SpyEmbedder(), store, make_settings())
    return gh, store, {"index": make_index_handler(indexer), "purge": make_purge_handler(store)}


async def test_push_webhook_to_indexed_code_end_to_end(env):
    gh, store, h = handlers()
    await send(env, "push", push(after="c0ffee"))
    worker = make_worker({"sm": env["sessionmaker"], "queue": env["queue"]}, dispatch(h))
    await drain(worker)
    assert set(await store.file_shas(42, "acme/widgets")) == {"billing.py", "icons.py"}
    assert (await store.get_repo_state(42, "acme/widgets")).head_sha == "c0ffee"


async def test_installation_created_indexes_the_default_branch_head(env):
    gh, store, h = handlers()
    gh.head = "headsha"
    await send(env, "installation", install("created", ["acme/widgets"]))
    await drain(make_worker({"sm": env["sessionmaker"], "queue": env["queue"]}, dispatch(h)))
    assert (await store.get_repo_state(42, "acme/widgets")).head_sha == "headsha"


async def test_uninstall_deletes_all_indexed_code(env):
    gh, store, h = handlers()
    await send(env, "push", push(), delivery="a")
    worker = make_worker({"sm": env["sessionmaker"], "queue": env["queue"]}, dispatch(h))
    await drain(worker)
    assert await store.count_chunks(42, "acme/widgets") > 0
    await send(env, "installation", {"action": "deleted", "installation": {"id": 42}}, delivery="b")
    await drain(worker)
    assert await store.count_chunks(42, "acme/widgets") == 0


async def test_removing_one_repo_keeps_the_others(env):
    gh, store, h = handlers()
    for repo in ("a/one", "a/two"):
        await h["index"](Job(idempotency_key=repo, installation_id=42, repo_full_name=repo, pr_number=0,
                             head_sha="HEAD", delivery_id="d", correlation_id="c", kind="index"))  # fmt: skip
    await send(
        env, "installation_repositories", install("removed", ["a/one"], "repositories_removed")
    )
    await drain(make_worker({"sm": env["sessionmaker"], "queue": env["queue"]}, dispatch(h)))
    assert await store.count_chunks(42, "a/one") == 0 and await store.count_chunks(42, "a/two") > 0


async def test_unknown_job_kind_is_dead_lettered_not_retried(wenv):
    from tests.helpers import add_job

    [jid] = await add_job(wenv["sm"], wenv["queue"])
    async with wenv["sm"]() as s:
        (await s.get(Job, jid)).kind = "mystery"
        await s.commit()
    await drain(make_worker(wenv, dispatch({})))
    assert (await load(wenv, jid)).status == JobStatus.DEAD


async def test_indexing_a_deleted_repo_is_permanent(wenv):
    gh, store, h = handlers()
    gh.repo_missing = True
    job = Job(idempotency_key="k", installation_id=1, repo_full_name="a/b", pr_number=0,
              head_sha="HEAD", delivery_id="d", correlation_id="c", kind="index")  # fmt: skip
    with pytest.raises(PermanentError):
        await h["index"](job)
