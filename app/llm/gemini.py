import httpx

from app.llm.base import BadRequest, LLMResult, Message, ProviderUnavailable
from app.llm.http import post_json
from app.llm.pricing import estimate_cost


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        http: httpx.AsyncClient,
        *,
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout: float = 60.0,
    ) -> None:
        self._key, self.model, self._http = api_key, model, http
        self._url = f"{base_url}/v1beta/models/{model}:generateContent"
        self._timeout = timeout

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        body: dict[str, object] = {
            "contents": [
                {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
                for m in messages
                if m.role != "system"
            ],
            "generationConfig": {
                "temperature": 0.1,
                **({"responseMimeType": "application/json"} if json_mode else {}),
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        data = await post_json(
            self._http,
            self._url,
            headers={"x-goog-api-key": self._key},
            body=body,
            timeout_s=self._timeout,
            provider=self.name,
        )
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise BadRequest(f"gemini returned no answer ({reason})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            if candidates[0].get("finishReason") in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                raise BadRequest(f"gemini blocked the answer ({candidates[0]['finishReason']})")
            raise ProviderUnavailable("gemini returned an empty answer")

        usage = data.get("usageMetadata") or {}
        prompt = int(usage.get("promptTokenCount", 0))
        # Thinking tokens are billed as output tokens.
        completion = int(usage.get("candidatesTokenCount", 0)) + int(
            usage.get("thoughtsTokenCount", 0)
        )
        return LLMResult(text, prompt, completion, self.name, self.model,
                         estimate_cost(self.model, prompt, completion))  # fmt: skip
