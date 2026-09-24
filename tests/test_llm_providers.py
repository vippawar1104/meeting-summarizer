import json

import httpx
import pytest

from app.llm.base import BadRequest, Message, ProviderUnavailable, RateLimited
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import GroqProvider, MistralProvider
from app.llm.pricing import estimate_cost

MSGS = [Message("system", "be strict"), Message("user", "review this"), Message("assistant", "ok")]


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def gemini_ok(text='{"findings": []}', **usage):
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, **usage},
    }


async def test_gemini_parses_text_tokens_and_cost():
    async with client(lambda r: httpx.Response(200, json=gemini_ok())) as http:
        res = await GeminiProvider("k", "gemini-2.5-flash", http).complete(MSGS)
    assert res.text == '{"findings": []}'
    assert (res.prompt_tokens, res.completion_tokens) == (100, 20)
    assert (res.provider, res.model) == ("gemini", "gemini-2.5-flash")
    assert res.cost_usd == pytest.approx(estimate_cost("gemini-2.5-flash", 100, 20))


async def test_gemini_request_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"] = str(req.url), req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=gemini_ok())

    async with client(handler) as http:
        await GeminiProvider("secret", "gemini-2.5-flash", http).complete(MSGS)
    assert seen["url"].endswith("/v1beta/models/gemini-2.5-flash:generateContent")
    assert seen["key"] == "secret" and "secret" not in seen["url"]
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "be strict"
    assert [c["role"] for c in body["contents"]] == ["user", "model"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"


async def test_gemini_json_mode_off_omits_mime_type():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=gemini_ok("hi"))

    async with client(handler) as http:
        await GeminiProvider("k", "m", http).complete(MSGS, json_mode=False)
    assert "responseMimeType" not in seen["body"]["generationConfig"]


async def test_gemini_thinking_tokens_count_as_output():
    async with client(lambda r: httpx.Response(200, json=gemini_ok(thoughtsTokenCount=50))) as http:
        res = await GeminiProvider("k", "gemini-2.5-flash", http).complete(MSGS)
    assert res.completion_tokens == 70


@pytest.mark.parametrize(
    "status,exc",
    [
        (429, RateLimited),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
        (401, ProviderUnavailable),
        (403, ProviderUnavailable),
        (400, BadRequest),
        (404, BadRequest),
    ],
)
async def test_gemini_http_status_mapping(status, exc):
    async with client(lambda r: httpx.Response(status, text="nope")) as http:
        with pytest.raises(exc):
            await GeminiProvider("k", "m", http).complete(MSGS)


async def test_rate_limit_carries_retry_after():
    async with client(lambda r: httpx.Response(429, headers={"retry-after": "7"})) as http:
        with pytest.raises(RateLimited) as ei:
            await GeminiProvider("k", "m", http).complete(MSGS)
    assert ei.value.retry_after_s == 7


async def test_timeout_and_network_errors_are_provider_unavailable():
    def timeout(req):
        raise httpx.ReadTimeout("slow")

    def down(req):
        raise httpx.ConnectError("refused")

    for handler in (timeout, down):
        async with client(handler) as http:
            with pytest.raises(ProviderUnavailable):
                await GeminiProvider("k", "m", http).complete(MSGS)


async def test_gemini_safety_block_is_a_bad_request_not_an_outage():
    body = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    async with client(lambda r: httpx.Response(200, json=body)) as http:
        with pytest.raises(BadRequest, match="SAFETY"):
            await GeminiProvider("k", "m", http).complete(MSGS)


async def test_gemini_empty_text_and_garbage_body_are_unavailable():
    empty = {"candidates": [{"content": {"parts": [{"text": " "}]}, "finishReason": "STOP"}]}
    async with client(lambda r: httpx.Response(200, json=empty)) as http:
        with pytest.raises(ProviderUnavailable):
            await GeminiProvider("k", "m", http).complete(MSGS)
    async with client(lambda r: httpx.Response(200, text="<html>")) as http:
        with pytest.raises(ProviderUnavailable):
            await GeminiProvider("k", "m", http).complete(MSGS)


def chat_ok(text="{}"):
    return {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.mark.parametrize(
    "cls,host,model",
    [
        (GroqProvider, "api.groq.com", "llama-3.3-70b-versatile"),
        (MistralProvider, "api.mistral.ai", "mistral-large-latest"),
    ],
)
async def test_openai_compatible_providers(cls, host, model):
    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=chat_ok('{"findings": []}'))

    async with client(handler) as http:
        res = await cls("sk", model, http).complete(MSGS)
    assert host in seen["url"] and seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer sk"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == model
    assert (res.prompt_tokens, res.completion_tokens, res.provider) == (10, 5, cls.name)


@pytest.mark.parametrize(
    "status,exc", [(429, RateLimited), (502, ProviderUnavailable), (422, BadRequest)]
)
async def test_openai_compatible_status_mapping(status, exc):
    async with client(lambda r: httpx.Response(status)) as http:
        with pytest.raises(exc):
            await GroqProvider("k", "m", http).complete(MSGS)


async def test_openai_compatible_content_filter_and_no_choices():
    filtered = {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}
    async with client(lambda r: httpx.Response(200, json=filtered)) as http:
        with pytest.raises(BadRequest):
            await MistralProvider("k", "m", http).complete(MSGS)
    async with client(lambda r: httpx.Response(200, json={"choices": []})) as http:
        with pytest.raises(ProviderUnavailable):
            await MistralProvider("k", "m", http).complete(MSGS)


def test_cost_estimate_and_unknown_model_is_zero_not_a_guess():
    assert estimate_cost("gemini-2.5-flash", 1_000_000, 0) == pytest.approx(0.30)
    assert estimate_cost("gemini-2.5-flash", 0, 1_000_000) == pytest.approx(2.50)
    assert estimate_cost("some-new-model", 10_000, 10_000) == 0.0
