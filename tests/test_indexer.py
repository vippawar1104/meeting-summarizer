from datetime import UTC, datetime, timedelta

import pytest

from app.queue.errors import PermanentError
from app.rag.indexer import RepoIndexer, purge_expired
from app.rag.store import MemoryChunkStore
from tests.helpers import make_settings
from tests.rag_helpers import PY_BILLING, PY_ICONS, PY_USERS, FakeRepoGitHub, SpyEmbedder

REPO = "acme/shop"


def make(files=None, **settings):
    gh = FakeRepoGitHub(
        files if files is not None else {"billing.py": PY_BILLING, "icons.py": PY_ICONS}
    )
    emb, store = SpyEmbedder(), MemoryChunkStore()
    return gh, emb, store, RepoIndexer(gh, emb, store, make_settings(**settings))


async def test_first_index_chunks_embeds_and_stores_every_file():
    gh, emb, store, indexer = make()
    report = await indexer.index_repo(1, REPO)
    assert report.files_indexed == 2 and report.chunks >= 3
    assert set(await store.file_shas(1, REPO)) == {"billing.py", "icons.py"}
    assert emb.documents_embedded() == report.chunks
    assert report.head_sha == "commit1"


async def test_repo_state_is_recorded():
    gh, _, store, indexer = make()
    await indexer.index_repo(1, REPO)
    state = await store.get_repo_state(1, REPO)
    assert (state.head_sha, state.file_count, state.truncated) == ("commit1", 2, False)


async def test_reindexing_an_unchanged_repo_fetches_and_embeds_nothing():
    gh, emb, store, indexer = make()
    await indexer.index_repo(1, REPO)
    gh.blob_fetches.clear()
    before = emb.documents_embedded()
    report = await indexer.index_repo(1, REPO)
    assert gh.blob_fetches == [] and emb.documents_embedded() == before
    assert report.files_indexed == 0 and report.files_unchanged == 2


async def test_only_the_changed_file_is_refetched_and_reembedded():
    gh, emb, store, indexer = make()
    await indexer.index_repo(1, REPO)
    gh.blob_fetches.clear()
    gh.files["icons.py"] = PY_ICONS + b"\n\ndef extra():\n    return 1\n"
    report = await indexer.index_repo(1, REPO)
    assert gh.blob_fetches == ["icons.py"] and report.files_indexed == 1
    assert "extra" in " ".join(
        h.chunk.content for h in await store.lexical_search(1, REPO, ["extra"], 5)
    )


async def test_changed_file_replaces_its_old_chunks():
    gh, _, store, indexer = make()
    await indexer.index_repo(1, REPO)
    gh.files["icons.py"] = b"def only_one():\n    return 1\n"
    await indexer.index_repo(1, REPO)
    assert await store.lexical_search(1, REPO, ["svg"], 5) == []


async def test_deleted_files_are_removed_from_the_index():
    gh, _, store, indexer = make()
    await indexer.index_repo(1, REPO)
    del gh.files["icons.py"]
    report = await indexer.index_repo(1, REPO)
    assert report.files_deleted == 1 and list(await store.file_shas(1, REPO)) == ["billing.py"]


async def test_nothing_is_deleted_when_the_tree_is_truncated():
    gh, _, store, indexer = make()
    await indexer.index_repo(1, REPO)
    gh.files = {"billing.py": PY_BILLING}  # icons.py is merely cut off, not deleted
    gh.truncated = True
    report = await indexer.index_repo(1, REPO)
    assert report.truncated and report.files_deleted == 0
    assert "icons.py" in await store.file_shas(1, REPO)
    assert (await store.get_repo_state(1, REPO)).truncated


async def test_files_that_should_not_be_indexed_are_skipped_and_counted():
    files = {
        "ok.py": PY_ICONS,
        "uv.lock": b"x", "logo.png": b"x", "node_modules/a.js": b"x", "dist/b.js": b"x",
        "data.bin": b"x", "big.py": b"x = 1\n" * 100_000, "static/app.min.js": b"x",
    }  # fmt: skip
    gh, _, store, indexer = make(files, max_file_bytes=1000)
    report = await indexer.index_repo(1, REPO)
    assert list(await store.file_shas(1, REPO)) == ["ok.py"]
    assert report.skipped["lock file"] == 1 and report.skipped["binary file"] == 1
    assert report.skipped["vendored or build output"] == 2 and report.skipped["file too large"] == 1
    assert report.skipped["unsupported file type"] == 1 and report.skipped["generated file"] == 1


async def test_binary_and_non_utf8_content_is_skipped_not_stored():
    files = {
        "nul.py": b"x = 1\x00\x01\x02",
        "latin.py": "café = 1".encode("latin-1"),
        "ok.py": PY_ICONS,
    }
    gh, _, store, indexer = make(files)
    report = await indexer.index_repo(1, REPO)
    assert (
        list(await store.file_shas(1, REPO)) == ["ok.py"]
        and report.skipped["binary or unreadable"] == 2
    )


async def test_a_blob_that_disappears_is_skipped_and_the_rest_still_index():
    gh, _, store, indexer = make()
    gh.missing.add("icons.py")
    report = await indexer.index_repo(1, REPO)
    assert list(await store.file_shas(1, REPO)) == ["billing.py"] and report.files_indexed == 1


async def test_over_the_file_limit_real_code_beats_docs():
    files = {f"doc{i}.md": b"# doc\n" * 3 for i in range(5)} | {
        "a.py": PY_ICONS,
        "b.py": PY_BILLING,
    }
    gh, _, store, indexer = make(files, max_index_files=3)
    report = await indexer.index_repo(1, REPO)
    shas = set(await store.file_shas(1, REPO))
    assert {"a.py", "b.py"} <= shas and len(shas) == 3 and report.skipped["over file limit"] == 4


async def test_embedding_outage_raises_and_a_retry_resumes_without_redoing_finished_files():
    gh, emb, store, indexer = make(
        {"a.py": PY_ICONS, "b.py": PY_BILLING, "c.py": PY_USERS}, index_concurrency=1
    )
    emb.fail = True
    with pytest.raises(ExceptionGroup):
        await indexer.index_repo(1, REPO)
    assert await store.file_shas(1, REPO) == {}
    emb.fail = False
    report = await indexer.index_repo(1, REPO)
    assert report.files_indexed == 3


async def test_partial_progress_survives_a_mid_run_failure():
    gh, emb, store, indexer = make({"a.py": PY_ICONS, "b.py": PY_BILLING}, index_concurrency=1)
    real = emb.embed
    state = {"n": 0}

    async def flaky(texts, *, task="document"):
        state["n"] += 1
        if state["n"] == 2:
            raise ConnectionError("blip")
        return await real(texts, task=task)

    emb.embed = flaky
    with pytest.raises(ExceptionGroup):
        await indexer.index_repo(1, REPO)
    done = set(await store.file_shas(1, REPO))
    assert len(done) == 1
    emb.embed = real
    gh.blob_fetches.clear()
    await indexer.index_repo(1, REPO)
    assert len(gh.blob_fetches) == 1  # only the file that had not finished


async def test_default_branch_head_is_resolved_when_no_sha_is_given():
    gh, _, _, indexer = make()
    gh.head, gh.default_branch = "deadbeef", "trunk"
    assert (await indexer.index_repo(1, REPO)).head_sha == "deadbeef"


async def test_explicit_sha_skips_branch_lookup():
    gh, _, _, indexer = make()
    assert (await indexer.index_repo(1, REPO, "cafe")).head_sha == "cafe"


async def test_missing_repo_is_a_permanent_error():
    gh, _, _, indexer = make()
    gh.repo_missing = True
    with pytest.raises(PermanentError):
        await indexer.index_repo(1, REPO)


async def test_blob_fetch_concurrency_is_bounded():
    files = {f"f{i}.py": f"def f{i}():\n    return {i}\n".encode() for i in range(12)}
    gh, _, _, indexer = make(files, index_concurrency=3)
    gh.delay = 0.01
    await indexer.index_repo(1, REPO)
    assert gh.peak <= 3 and gh.peak > 1


async def test_installations_index_independently():
    gh, emb, store, indexer = make()
    await indexer.index_repo(1, REPO)
    await indexer.index_repo(2, REPO)
    assert await store.count_chunks(1, REPO) == await store.count_chunks(2, REPO) > 0
    await store.delete_installation(1)
    assert await store.count_chunks(2, REPO) > 0


async def test_chunks_are_embedded_with_their_path_and_name():
    gh, emb, store, indexer = make({"billing.py": PY_BILLING})
    seen = []
    real = emb.embed

    async def spy(texts, *, task="document"):
        seen.extend(texts)
        return await real(texts, task=task)

    emb.embed = spy
    await indexer.index_repo(1, REPO)
    assert any(t.startswith("billing.py\nfunction charge_customer") for t in seen)


async def test_retention_purge_removes_only_code_nobody_has_seen_lately():
    now = {"t": datetime(2026, 1, 1, tzinfo=UTC)}
    gh, emb = FakeRepoGitHub({"a.py": PY_ICONS, "b.py": PY_BILLING}), SpyEmbedder()
    store = MemoryChunkStore(clock=lambda: now["t"])
    indexer = RepoIndexer(gh, emb, store, make_settings())
    await indexer.index_repo(1, REPO)
    now["t"] = datetime(2026, 2, 15, tzinfo=UTC)
    del gh.files["b.py"]
    await indexer.index_repo(1, REPO)  # a.py still exists -> refreshed; b.py is deleted
    now["t"] = datetime(2026, 3, 1, tzinfo=UTC)
    assert await purge_expired(store, 30, now=now["t"]) == 0  # a.py was seen 14 days ago
    now["t"] = datetime(2026, 4, 1, tzinfo=UTC)
    assert await purge_expired(store, 30, now=now["t"]) > 0
    assert await store.count_chunks(1, REPO) == 0


async def test_unchanged_files_are_kept_alive_by_reindexing():
    now = {"t": datetime(2026, 1, 1, tzinfo=UTC)}
    gh, emb = FakeRepoGitHub({"a.py": PY_ICONS}), SpyEmbedder()
    store = MemoryChunkStore(clock=lambda: now["t"])
    indexer = RepoIndexer(gh, emb, store, make_settings())
    await indexer.index_repo(1, REPO)
    now["t"] += timedelta(days=25)
    await indexer.index_repo(1, REPO)  # nothing changed, but the file was seen again
    now["t"] += timedelta(days=25)  # 50 days after first index, 25 after last sight
    assert await purge_expired(store, 30, now=now["t"]) == 0
