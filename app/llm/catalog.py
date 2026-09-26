import re
from dataclasses import dataclass

import httpx

from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import CustomProvider, GroqProvider, MistralProvider, OpenAIProvider

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,99}$")


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    needs_base_url: bool
    models: tuple[str, ...]  # suggestions only: any model name the provider accepts is allowed
    key_url: str  # where a user gets a key


CATALOG: dict[str, ProviderInfo] = {
    p.id: p
    for p in (
        ProviderInfo("openai", "OpenAI", False, ("gpt-4.1", "gpt-4o-mini"), "https://platform.openai.com/api-keys"),
        ProviderInfo("anthropic", "Anthropic (Claude)", False, ("claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"), "https://console.anthropic.com/settings/keys"),
        ProviderInfo("gemini", "Google Gemini", False, ("gemini-2.5-flash", "gemini-2.5-pro"), "https://aistudio.google.com/apikey"),
        ProviderInfo("groq", "Groq", False, ("openai/gpt-oss-120b", "qwen/qwen3.8-27b"), "https://console.groq.com/keys"),
        ProviderInfo("mistral", "Mistral", False, ("mistral-large-latest",), "https://console.mistral.ai/api-keys"),
        ProviderInfo("custom", "OpenAI-compatible endpoint", True, (), ""),
    )
}  # fmt: skip


def build_provider(
    provider_id: str,
    model: str,
    api_key: str,
    base_url: str | None,
    http: httpx.AsyncClient,
    timeout: float = 90.0,
) -> LLMProvider:
    if provider_id == "openai":
        return OpenAIProvider(api_key, model, http, timeout=timeout)
    if provider_id == "anthropic":
        return AnthropicProvider(api_key, model, http, timeout=timeout)
    if provider_id == "gemini":
        return GeminiProvider(api_key, model, http, timeout=timeout)
    if provider_id == "groq":
        return GroqProvider(api_key, model, http, timeout=timeout)
    if provider_id == "mistral":
        return MistralProvider(api_key, model, http, timeout=timeout)
    if provider_id == "custom":
        if not base_url:
            raise ValueError("a custom endpoint needs a base URL")
        return CustomProvider(api_key, model, http, base_url=base_url, timeout=timeout)
    raise ValueError(f"unknown provider {provider_id!r}")
