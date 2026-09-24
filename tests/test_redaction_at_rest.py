import fakeredis

from app.cost.cache import ReviewCache
from app.db.models import Job
from app.rag.embeddings import HashingEmbedder, RedactingEmbedder
from app.rag.indexer import RepoIndexer
from app.rag.store import MemoryChunkStore
from tests.helpers import make_settings
from tests.rag_helpers import FakeRepoGitHub, SpyEmbedder
from tests.secrets_fixtures import fake_secrets
from worker.handlers import make_purge_handler

SECRET = fake_secrets()["github_token"]
SOURCE = f'import os\n\nTOKEN = "{SECRET}"\n\n\ndef connect():\n    return TOKEN\n'.encode()


async def test_secrets_are_redacted_before_code_is_stored_or_embedded():
    gh, emb, store = FakeRepoGitHub({"cfg.py": SOURCE}), SpyEmbedder(), MemoryChunkStore()
    seen = []
    real = emb.embed

    async def spy(texts, *, task="document"):
        seen.extend(texts)
        return await real(texts, task=task)

    emb.embed = spy
    await RepoIndexer(gh, emb, store, make_settings()).index_repo(1, "o/r")
    stored = " ".join(r.chunk.content for f in store._files[(1, "o/r")].values() for r in f.rows)
    assert SECRET not in stored and SECRET not in " ".join(seen) and "[REDACTED:" in stored


async def test_redaction_keeps_chunk_line_numbers_exact():
    gh, emb, store = FakeRepoGitHub({"cfg.py": SOURCE}), SpyEmbedder(), MemoryChunkStore()
    await RepoIndexer(gh, emb, store, make_settings()).index_repo(1, "o/r")
    rows = store._files[(1, "o/r")]["cfg.py"].rows
    connect = next(r.chunk for r in rows if r.chunk.name == "connect")
    assert (connect.start_line, connect.end_line) == (6, 7)


async def test_control_with_redaction_off_the_secret_is_stored():
    gh, emb, store = FakeRepoGitHub({"cfg.py": SOURCE}), SpyEmbedder(), MemoryChunkStore()
    await RepoIndexer(gh, emb, store, make_settings(redaction_enabled=False)).index_repo(1, "o/r")
    assert SECRET in " ".join(r.chunk.content for r in store._files[(1, "o/r")]["cfg.py"].rows)


async def test_the_redacting_embedder_scrubs_whatever_it_is_given():
    inner = SpyEmbedder()
    seen = []
    real = inner.embed

    async def spy(texts, *, task="document"):
        seen.extend(texts)
        return await real(texts, task=task)

    inner.embed = spy
    wrapped = RedactingEmbedder(inner)
    vectors = await wrapped.embed([f"x = '{SECRET}'", "plain text"], task="query")
    assert SECRET not in "".join(seen) and len(vectors) == 2 and len(vectors[0]) == inner.dimension
    assert (wrapped.model, wrapped.dimension) == (inner.model, inner.dimension)


async def test_uninstalling_clears_cached_replies_as_well_as_the_index():
    store, cache = MemoryChunkStore(), ReviewCache(fakeredis.FakeAsyncRedis(), 7)
    await cache.put(5, "k", "reply derived from customer code")
    await cache.put(6, "k", "another tenant")
    job = Job(idempotency_key="p", installation_id=5, repo_full_name="*", pr_number=0, head_sha="-",
              delivery_id="d", correlation_id="c", kind="purge")  # fmt: skip
    await make_purge_handler(store, cache)(job)
    assert await cache.get(5, "k") is None and await cache.get(6, "k") == "another tenant"


async def test_removing_a_single_repo_leaves_the_installation_cache_alone():
    store, cache = MemoryChunkStore(), ReviewCache(fakeredis.FakeAsyncRedis(), 7)
    await cache.put(5, "k", "x")
    job = Job(idempotency_key="p", installation_id=5, repo_full_name="a/b", pr_number=0, head_sha="-",
              delivery_id="d", correlation_id="c", kind="purge")  # fmt: skip
    await make_purge_handler(store, cache)(job)
    assert await cache.get(5, "k") == "x"


def test_hashing_embedder_import_is_used():
    assert HashingEmbedder(8).dimension == 8
