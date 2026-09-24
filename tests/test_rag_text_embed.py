import json
import math

import httpx
import pytest

from app.core.config import Settings
from app.llm.base import ProviderUnavailable, RateLimited
from app.rag.embeddings import GeminiEmbedder, HashingEmbedder, build_embedder
from app.rag.text import code_tokens, query_terms, search_text, subtokens


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_subtokens_split_snake_and_camel_case():
    assert subtokens("get_user_by_id") == ["get", "user", "by", "id"]
    assert subtokens("getUserById") == ["get", "user", "by", "id"]
    assert subtokens("HTTPServerError") == ["http", "server", "error"]
    assert subtokens("parseJSON2Text") == ["parse", "json", "text"]


def test_code_tokens_include_the_joined_identifier_and_drop_stopwords():
    toks = code_tokens("def getUserById(self, user_id): return None")
    assert "getuserbyid" in toks and "user" in toks and "id" not in toks  # 'id' is too short
    assert not {"def", "self", "return", "none"} & set(toks)


def test_tokens_are_safe_for_tsquery():
    for tok in code_tokens("weird_name$ 'quote' a|b (paren) café x-y"):
        assert tok.isalnum() and tok == tok.lower()


def test_search_text_includes_path_and_name():
    assert "billing" in search_text("app/billing/invoice.py", "make_invoice", "pass")
    assert "makeinvoice" in search_text("f.py", "make_invoice", "pass")


def test_query_terms_prefer_frequent_and_distinctive_terms():
    text = "payment payment payment charge_customer x y z " + "misc " * 1
    terms = query_terms(text, limit=3)
    assert terms[0] == "payment" and "chargecustomer" in terms and len(terms) == 3


def test_query_terms_empty_input():
    assert query_terms("", 5) == [] and query_terms("a b c", 5) == []


async def test_hashing_embedder_is_deterministic_normalised_and_right_sized():
    e = HashingEmbedder(768)
    [a1], [a2] = (
        await e.embed(["def charge_customer(): pass"]),
        await e.embed(["def charge_customer(): pass"]),
    )
    assert a1 == a2 and len(a1) == 768
    assert math.isclose(math.sqrt(sum(v * v for v in a1)), 1.0, rel_tol=1e-9)


async def test_hashing_embedder_ranks_shared_vocabulary_higher():
    e = HashingEmbedder()
    q, near, far = await e.embed(
        [
            "charge the customer payment",
            "def charge_customer_payment(): ...",
            "def render_svg_icon(): ...",
        ]
    )
    assert cosine(q, near) > cosine(q, far)


async def test_hashing_embedder_handles_empty_text():
    [v] = await HashingEmbedder(16).embed([""])
    assert v == [0.0] * 16


async def test_hashing_embedder_ignores_the_task():
    e = HashingEmbedder()
    assert await e.embed(["abc def"], task="query") == await e.embed(["abc def"], task="document")


def gemini(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiEmbedder("key", "gemini-embedding-001", 4, http, **kw)


def reply(n, dim=4):
    return httpx.Response(200, json={"embeddings": [{"values": [3.0] + [0.0] * (dim - 1)}] * n})


async def test_gemini_embedder_request_shape_and_normalisation():
    seen = {}

    def handler(req):
        seen["url"], seen["key"] = str(req.url), req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return reply(2)

    out = await gemini(handler).embed(["a", "b"], task="query")
    assert seen["url"].endswith("gemini-embedding-001:batchEmbedContents") and seen["key"] == "key"
    req0 = seen["body"]["requests"][0]
    assert req0["taskType"] == "RETRIEVAL_QUERY" and req0["outputDimensionality"] == 4
    assert req0["model"] == "models/gemini-embedding-001"
    assert out[0] == [1.0, 0.0, 0.0, 0.0]  # 3.0 scaled to unit length


async def test_gemini_embedder_documents_use_the_document_task():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return reply(1)

    await gemini(handler).embed(["a"])
    assert seen["body"]["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"


async def test_gemini_embedder_splits_large_inputs_into_batches():
    sizes = []

    def handler(req):
        n = len(json.loads(req.content)["requests"])
        sizes.append(n)
        return reply(n)

    out = await gemini(handler, batch_size=3).embed([str(i) for i in range(7)])
    assert sizes == [3, 3, 1] and len(out) == 7


async def test_gemini_embedder_batch_size_is_capped_at_the_api_limit():
    sizes = []

    def handler(req):
        n = len(json.loads(req.content)["requests"])
        sizes.append(n)
        return reply(n)

    await gemini(handler, batch_size=500).embed([str(i) for i in range(150)])
    assert sizes == [100, 50]


async def test_gemini_embedder_truncates_huge_texts():
    seen = {}

    def handler(req):
        seen["len"] = len(json.loads(req.content)["requests"][0]["content"]["parts"][0]["text"])
        return reply(1)

    await gemini(handler).embed(["x" * 50_000])
    assert seen["len"] == 8000


async def test_gemini_embedder_rejects_a_wrong_sized_response():
    with pytest.raises(ProviderUnavailable):
        await gemini(lambda req: reply(1)).embed(["a", "b"])


async def test_gemini_embedder_maps_rate_limits():
    with pytest.raises(RateLimited):
        await gemini(lambda req: httpx.Response(429)).embed(["a"])


def test_build_embedder_picks_gemini_when_keyed_and_hashing_otherwise():
    http = httpx.AsyncClient()
    assert isinstance(build_embedder(Settings(gemini_api_key="k"), http), GeminiEmbedder)
    fallback = build_embedder(Settings(gemini_api_key=None), http)
    assert isinstance(fallback, HashingEmbedder) and fallback.dimension == 768
