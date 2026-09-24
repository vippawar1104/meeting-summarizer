import httpx

from app.llm.base import BadRequest, LLMResult, Message, ProviderUnavailable
from app.llm.http import post_json
from app.llm.pricing import estimate_cost


class OpenAICompatProvider:
    """Chat-completions API shared by Groq and Mistral."""

    name = "openai_compat"
    default_base_url = ""

    def __init__(
        self,
        api_key: str,
        model: str,
        http: httpx.AsyncClient,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._key, self.model, self._http = api_key, model, http
        self._url = f"{base_url or self.default_base_url}/chat/completions"
        self._timeout = timeout

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        body: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0.1,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = await post_json(
            self._http,
            self._url,
            headers={"Authorization": f"Bearer {self._key}"},
            body=body,
            timeout_s=self._timeout,
            provider=self.name,
        )
        choices = data.get("choices") or []
        if not choices:
            raise ProviderUnavailable(f"{self.name} returned no choices")
        text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            if choices[0].get("finish_reason") == "content_filter":
                raise BadRequest(f"{self.name} filtered the answer")
            raise ProviderUnavailable(f"{self.name} returned an empty answer")
        usage = data.get("usage") or {}
        prompt, completion = (
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )
        return LLMResult(text, prompt, completion, self.name, self.model,
                         estimate_cost(self.model, prompt, completion))  # fmt: skip


class GroqProvider(OpenAICompatProvider):
    name = "groq"
    default_base_url = "https://api.groq.com/openai/v1"


class MistralProvider(OpenAICompatProvider):
    name = "mistral"
    default_base_url = "https://api.mistral.ai/v1"
