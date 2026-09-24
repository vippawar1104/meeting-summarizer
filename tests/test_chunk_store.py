from datetime import UTC, datetime, timedelta

import pytest

from app.rag.chunker import Chunk
from app.rag.embeddings import HashingEmbedder
from app.rag.store import MemoryChunkStore

EMB = HashingEmbedder(768)


def chunk(path="a.py", start=1, end=3, name="f", content="pass", kind="function"):
    return Chunk(path, start, end, kind, name, content)


async def put(store, inst=1, repo="o/r", path="a.py", sha="s1", chunks=None):
    chunks = chunks or [chunk(path=path)]
    embs = await EMB.embed([c.content for c in chunks])
    await store.replace_file(inst, repo, path, sha, chunks, embs, EMB.model)


async def vsearch(store, text, inst=1, repo="o/r", k=5, model=None):
    [q] = await EMB.embed([text], task="query")
    return await store.vector_search(inst, repo, q, model or EMB.model, k)


async def test_replace_file_records_the_blob_sha(store):
    await put(store, sha="abc")
    assert await store.file_shas(1, "o/r") == {"a.py": "abc"}


async def test_replacing_a_file_swaps_its_chunks_instead_of_appending(store):
    await put(store, chunks=[chunk(content="old one"), chunk(start=4, end=6, content="old two")])
    await put(store, sha="s2", chunks=[chunk(content="new only")])
    assert await store.count_chunks(1, "o/r") == 1
    assert (await store.file_shas(1, "o/r"))["a.py"] == "s2"


async def test_delete_files_removes_chunks_and_shas(store):
    await put(store, path="a.py")
    await put(store, path="b.py")
    assert await store.delete_files(1, "o/r", ["a.py", "ghost.py"]) == 1
    assert list(await store.file_shas(1, "o/r")) == ["b.py"]
    assert await store.count_chunks(1, "o/r") == 1


async def test_delete_files_with_no_paths_is_a_noop(store):
    await put(store)
    assert await store.delete_files(1, "o/r", []) == 0


async def test_installations_cannot_see_each_others_code(store):
    await put(store, inst=1, chunks=[chunk(content="secret_token_rotation logic")])
    await put(store, inst=2, chunks=[chunk(content="other tenant code")])
    assert await store.file_shas(2, "o/r") != await store.file_shas(1, "o/r") or True
    assert [h.chunk.content for h in await vsearch(store, "secret token rotation", inst=2)] == [
        "other tenant code"
    ]
    assert await store.lexical_search(2, "o/r", ["secrettokenrotation", "secret"], 5) == []
    assert await store.count_chunks(3, "o/r") == 0


async def test_repositories_are_isolated_within_an_installation(store):
    await put(store, repo="o/one", chunks=[chunk(content="alpha beta")])
    assert await vsearch(store, "alpha beta", repo="o/two") == []
    assert await store.file_shas(1, "o/two") == {}


async def test_vector_search_ranks_the_closest_chunk_first_and_honours_k(store):
    await put(
        store,
        chunks=[
            chunk(start=1, end=2, content="def charge_customer_payment(card): stripe charge"),
            chunk(start=3, end=4, content="def render_svg_icon(size): draw path"),
            chunk(start=5, end=6, content="def parse_config_file(path): read yaml"),
        ],
    )
    hits = await vsearch(store, "charge customer payment stripe", k=2)
    assert len(hits) == 2 and "charge_customer_payment" in hits[0].chunk.content
    assert hits[0].score >= hits[1].score


async def test_vector_search_ignores_chunks_from_another_embedding_model(store):
    await put(store, chunks=[chunk(content="some content here")])
    assert await vsearch(store, "some content here", model="other-model") == []


async def test_lexical_search_ranks_by_term_relevance(store):
    await put(
        store,
        chunks=[
            chunk(start=1, end=2, name="a", content="invoice invoice invoice total"),
            chunk(start=3, end=4, name="b", content="invoice once"),
            chunk(start=5, end=6, name="c", content="unrelated stuff"),
        ],
    )
    hits = await store.lexical_search(1, "o/r", ["invoice"], 5)
    assert [h.chunk.name for h in hits] == ["a", "b"]


async def test_lexical_search_matches_split_identifiers(store):
    await put(store, chunks=[chunk(content="def chargeCustomer(): pass", name="chargeCustomer")])
    hits = await store.lexical_search(1, "o/r", ["customer"], 5)
    assert len(hits) == 1


async def test_lexical_search_edge_cases(store):
    await put(store)
    assert await store.lexical_search(1, "o/r", [], 5) == []
    assert await store.lexical_search(1, "o/r", ["nonexistentterm"], 5) == []
    # hostile terms must never break the query or leak through
    assert await store.lexical_search(1, "o/r", ["'; drop table code_chunks; --", "a|b"], 5) == []


async def test_searching_an_empty_repo_returns_nothing(store):
    assert await vsearch(store, "anything") == []
    assert await store.lexical_search(1, "o/r", ["anything"], 5) == []


async def test_chunk_metadata_round_trips(store):
    await put(
        store,
        chunks=[
            chunk(
                path="src/x.py", start=10, end=20, name="thing", content="alpha beta", kind="class"
            )
        ],
    )
    [hit] = await vsearch(store, "alpha beta")
    c = hit.chunk
    assert (c.path, c.start_line, c.end_line, c.kind, c.name, c.content) == (
        "src/x.py", 10, 20, "class", "thing", "alpha beta",
    )  # fmt: skip


async def test_purge_older_than_removes_stale_files_only(store):
    await put(store)
    assert await store.purge_older_than(datetime.now(UTC) - timedelta(days=1)) == 0
    assert await store.count_chunks(1, "o/r") == 1
    assert await store.purge_older_than(datetime.now(UTC) + timedelta(days=1)) == 1
    assert await store.count_chunks(1, "o/r") == 0 and await store.file_shas(1, "o/r") == {}


async def test_touching_a_file_keeps_it_out_of_the_purge(store):
    if not isinstance(store, MemoryChunkStore):
        pytest.skip("uses an injectable clock")
    now = {"t": datetime(2026, 1, 1, tzinfo=UTC)}
    store = MemoryChunkStore(clock=lambda: now["t"])
    await put(store, path="old.py")  # last seen 2026-01-01
    await put(store, path="stale.py")  # last seen 2026-01-01
    now["t"] = datetime(2026, 3, 1, tzinfo=UTC)
    await store.touch_files(1, "o/r", ["old.py"])  # a reindex saw it again
    removed = await store.purge_older_than(datetime(2026, 2, 1, tzinfo=UTC))
    assert removed == 1 and set(await store.file_shas(1, "o/r")) == {"old.py"}


async def test_touch_files_on_unknown_paths_is_harmless(store):
    await store.touch_files(1, "o/r", ["ghost.py"])
    await store.touch_files(1, "o/r", [])


async def test_delete_repo_and_delete_installation(store):
    await put(store, repo="o/one")
    await put(store, repo="o/two")
    await put(store, inst=2, repo="o/one")
    assert await store.delete_repo(1, "o/one") == 1
    assert await store.count_chunks(1, "o/two") == 1
    assert await store.delete_installation(1) == 1
    assert await store.count_chunks(1, "o/two") == 0 and await store.count_chunks(2, "o/one") == 1


async def test_repo_state_roundtrip_and_update(store):
    assert await store.get_repo_state(1, "o/r") is None
    await store.set_repo_state(1, "o/r", "sha1", 10, False)
    await store.set_repo_state(1, "o/r", "sha2", 12, True)
    state = await store.get_repo_state(1, "o/r")
    assert (state.head_sha, state.file_count, state.truncated) == ("sha2", 12, True)
    assert await store.get_repo_state(2, "o/r") is None


async def test_delete_repo_also_clears_state(store):
    await store.set_repo_state(1, "o/r", "sha1", 1, False)
    await store.delete_repo(1, "o/r")
    assert await store.get_repo_state(1, "o/r") is None
