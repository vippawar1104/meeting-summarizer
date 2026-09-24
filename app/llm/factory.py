from collections.abc import Callable

import httpx

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import GroqProvider, MistralProvider
from app.llm.router import LLMRouter


def build_router(settings: Settings, http: httpx.AsyncClient) -> LLMRouter:
    keys: dict[str, tuple[str | None, str, Callable[..., LLMProvider] | None]] = {
        "gemini": (settings.gemini_api_key, settings.gemini_model, GeminiProvider),
        "groq": (settings.groq_api_key, settings.groq_model, GroqProvider),
        "mistral": (settings.mistral_api_key, settings.mistral_model, MistralProvider),
    }
    providers: list[LLMProvider] = []
    for name in (n.strip() for n in settings.provider_order.split(",")):
        key, model, cls = keys.get(name, (None, "", None))
        if key and cls:
            providers.append(cls(key, model, http, timeout=settings.llm_timeout_s))
    return LLMRouter(
        providers,
        breaker_threshold=settings.breaker_threshold,
        breaker_cooldown_s=settings.breaker_cooldown_s,
    )
