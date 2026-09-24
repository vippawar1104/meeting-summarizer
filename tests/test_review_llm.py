import json

import pytest

from app.llm.base import AllProvidersFailed, LLMResult
from app.pipeline.review_llm import review_group

GOOD = json.dumps(
    {
        "findings": [
            {
                "file": "a.py", "line": 3, "severity": "high", "category": "bug",
                "message": "bad", "confidence": 0.9,
            }
        ]
    }
)  # fmt: skip


class ScriptedRouter:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def complete(self, messages, *, json_mode=True):
        self.calls.append(list(messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResult(r, 100, 20, "fake", "m", 0.001)


async def test_valid_first_reply_needs_no_repair():
    router = ScriptedRouter(GOOD)
    out = await review_group(router, "sys", "user")
    assert len(out.findings) == 1 and not out.repaired and len(router.calls) == 1
    assert out.model == "fake/m"


async def test_invalid_reply_gets_exactly_one_repair_that_can_succeed():
    router = ScriptedRouter("I found a bug!", GOOD)
    out = await review_group(router, "sys", "user")
    assert out.repaired and len(out.findings) == 1 and not out.parse_failed
    assert (out.prompt_tokens, out.completion_tokens) == (200, 40)
    assert out.cost_usd == pytest.approx(0.002)


async def test_repair_prompt_includes_the_previous_reply_and_the_error():
    router = ScriptedRouter("I found a bug!", GOOD)
    await review_group(router, "sys", "user")
    second = router.calls[1]
    assert [m.role for m in second] == ["system", "user", "assistant", "user"]
    assert second[2].content == "I found a bug!"
    assert "not valid JSON" in second[3].content


async def test_repair_is_attempted_only_once():
    router = ScriptedRouter("nope", "still nope", GOOD)
    out = await review_group(router, "sys", "user")
    assert len(router.calls) == 2 and out.findings == [] and out.parse_failed


async def test_partially_invalid_reply_is_repaired_and_valid_items_kept():
    mixed = json.dumps({"findings": [json.loads(GOOD)["findings"][0], {"file": "x"}]})
    router = ScriptedRouter(mixed, GOOD)
    out = await review_group(router, "sys", "user")
    assert out.repaired and len(out.findings) == 1 and out.dropped_invalid == 0


async def test_still_partially_invalid_after_repair_keeps_valid_items():
    mixed = json.dumps({"findings": [json.loads(GOOD)["findings"][0], {"file": "x"}]})
    out = await review_group(ScriptedRouter(mixed, mixed), "sys", "user")
    assert len(out.findings) == 1 and out.dropped_invalid == 1 and not out.parse_failed


async def test_provider_outage_during_repair_keeps_first_pass_results():
    mixed = json.dumps({"findings": [json.loads(GOOD)["findings"][0], {"file": "x"}]})
    router = ScriptedRouter(mixed, AllProvidersFailed(["down"]))
    out = await review_group(router, "sys", "user")
    assert len(out.findings) == 1


async def test_provider_outage_on_first_call_propagates_so_the_job_retries():
    with pytest.raises(AllProvidersFailed):
        await review_group(ScriptedRouter(AllProvidersFailed(["down"])), "sys", "user")
