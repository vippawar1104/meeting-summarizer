import httpx

from app.llm.base import LLMResult, Message, ProviderUnavailable
from app.llm.http import post_json
from app.llm.pricing import estimate_cost

ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 4096


class AnthropicProvider:
    """Anthropic Messages API. It has no JSON mode, so JSON is requested in the system prompt (our
    parser already tolerates fences and surrounding prose) and a malformed reply goes through the
    normal repair retry."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        http: httpx.AsyncClient,
        *,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 60.0,
    ) -> None:
        self._key, self.model, self._http, self._timeout = api_key, model, http, timeout
        self._url = f"{base_url.rstrip('/')}/v1/messages"

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        if json_mode:
            system += "\n\nRespond with a single JSON object and nothing else."
        body: dict[str, object] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.1,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
        }
        if system.strip():
            body["system"] = system
        data = await post_json(
            self._http,
            self._url,
            headers={"x-api-key": self._key, "anthropic-version": ANTHROPIC_VERSION},
            body=body,
            timeout_s=self._timeout,
            provider=self.name,
        )
        text = "".join(
            b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text"
        )
        if not text.strip():
            raise ProviderUnavailable("anthropic returned an empty answer")
        usage = data.get("usage") or {}
        prompt, completion = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        return LLMResult(
            text,
            prompt,
            completion,
            self.name,
            self.model,
            estimate_cost(self.model, prompt, completion),
        )
