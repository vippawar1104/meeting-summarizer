"""Record and replay LLM replies so an evaluation is reproducible offline and free to re-score."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.llm.base import Completer, LLMResult, Message, RetryableLLMError


class MissingRecording(Exception):
    pass


def request_key(model: str, messages: list[Message], json_mode: bool) -> str:
    blob = json.dumps([model, [(m.role, m.content) for m in messages], json_mode])
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Recording:
    text: str
    prompt_tokens: int
    completion_tokens: int
    provider: str
    model: str
    cost_usd: float
    latency_s: float


class RecordingStore:
    """One JSONL file per (model, prompt version): {"key": ..., "reply": {...}} per line, plus
    {"case": id, "latency_s": ...} lines with the wall-clock time of each whole case."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.replies: dict[str, Recording] = {}
        self.case_latency: dict[str, float] = {}
        if path.is_file():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if "key" in row:
                    self.replies[row["key"]] = Recording(**row["reply"])
                else:
                    self.case_latency[row["case"]] = row["latency_s"]

    def _append(self, row: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def add_reply(self, key: str, rec: Recording) -> None:
        self.replies[key] = rec
        self._append({"key": key, "reply": rec.__dict__})

    def add_case_latency(self, case_id: str, seconds: float) -> None:
        self.case_latency[case_id] = seconds
        self._append({"case": case_id, "latency_s": seconds})


class RecordingRouter:
    """Serves replies from a recording when it has them (`auto`/`replay`) and otherwise calls the
    real router (`auto`/`live`), retrying provider throttling with backoff."""

    def __init__(
        self,
        inner: Completer | None,
        store: RecordingStore,
        model: str,
        mode: str = "auto",
        *,
        retries: int = 10,
        rpm: float | None = None,
        max_hint_wait_s: float = 300.0,
    ) -> None:
        assert mode in ("auto", "live", "replay")
        self._inner, self._store, self._model, self._mode = inner, store, model, mode
        self._retries = retries
        self._max_hint_wait_s = max_hint_wait_s
        self._interval = (
            60.0 / rpm if rpm else 0.0
        )  # spacing that keeps us under a requests/minute cap
        self._pace_lock = asyncio.Lock()
        self._next_slot = 0.0
        self.providers: list[object] = [object()]  # non-empty: this router is "configured"

    def status(self) -> dict[str, str]:
        return {}

    async def _pace(self) -> None:
        if not self._interval:
            return
        async with self._pace_lock:
            loop = asyncio.get_running_loop()
            wait = self._next_slot - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = loop.time() + self._interval

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        key = request_key(self._model, messages, json_mode)
        if self._mode != "live" and key in self._store.replies:
            r = self._store.replies[key]
            return LLMResult(
                r.text, r.prompt_tokens, r.completion_tokens, r.provider, r.model, r.cost_usd
            )
        if self._mode == "replay" or self._inner is None:
            raise MissingRecording(f"no recorded reply for {self._model} (mode={self._mode})")
        start = asyncio.get_running_loop().time()
        for attempt in range(self._retries + 1):
            await self._pace()
            try:
                res = await self._inner.complete(messages, json_mode=json_mode)
                break
            except RetryableLLMError as exc:
                if attempt == self._retries:
                    raise
                hinted = getattr(exc, "retry_after_s", None)  # the provider says how long to wait
                if hinted and hinted > self._max_hint_wait_s:
                    raise  # e.g. a daily quota: waiting hours would just hang the run
                await asyncio.sleep(hinted + 1.0 if hinted else min(60.0, 2.0 * 2**attempt))
        elapsed = asyncio.get_running_loop().time() - start
        self._store.add_reply(
            key,
            Recording(
                res.text,
                res.prompt_tokens,
                res.completion_tokens,
                res.provider,
                res.model,
                res.cost_usd,
                elapsed,
            ),
        )
        return res
