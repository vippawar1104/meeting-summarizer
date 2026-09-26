"""A fake OpenAI-compatible LLM server for load tests, with latency and failures you can change live.

    python -m loadtest.stub_llm                       # :9001
    curl -X POST localhost:9001/_control -d '{"latency_ms": 1500, "fail_models": ["stub-primary"]}'

Hammering a real provider would measure that provider's rate limits, not Reviewly. Every reply is
an empty findings list: the point is to exercise the queue, worker, GitHub calls and bookkeeping
at a known, controllable model latency.
"""

import asyncio
import json
import os
import random
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="stub-llm")
STATE: dict[str, Any] = {
    "latency_ms": 1000,
    "jitter_ms": 200,
    "fail_rate": 0.0,  # fraction of calls answered 503
    "fail_models": [],  # these models always answer 503
    "calls": {},  # model -> count
    "failed": 0,
}


@app.post("/_control")
async def control(request: Request) -> dict[str, Any]:
    STATE.update(await request.json())
    return STATE


@app.get("/_stats")
async def stats() -> dict[str, Any]:
    return STATE


@app.post("/v1/chat/completions")
async def chat(request: Request) -> dict[str, Any]:
    body = await request.json()
    model = body.get("model", "?")
    STATE["calls"][model] = STATE["calls"].get(model, 0) + 1
    await asyncio.sleep(
        max(0.0, STATE["latency_ms"] + random.uniform(-1, 1) * STATE["jitter_ms"]) / 1000
    )
    if model in STATE["fail_models"] or random.random() < STATE["fail_rate"]:
        STATE["failed"] += 1
        raise HTTPException(status_code=503, detail="stub LLM is down")
    return {
        "choices": [
            {"message": {"content": json.dumps({"findings": []})}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 40},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "9001")), log_level="warning")
