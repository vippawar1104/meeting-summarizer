from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    provider: str
    model: str
    cost_usd: float


class LLMError(Exception):
    pass


class RetryableLLMError(LLMError):
    """Provider unhealthy or throttling: try another one and count it against the breaker."""


class RateLimited(RetryableLLMError):
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderUnavailable(RetryableLLMError):
    pass


class BadRequest(LLMError):
    """The provider answered but refused this request (invalid, or blocked by a safety filter)."""


class AllProvidersFailed(LLMError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("all LLM providers failed: " + "; ".join(errors))
        self.errors = errors


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, messages: list[Message], *, json_mode: bool = True) -> LLMResult: ...
