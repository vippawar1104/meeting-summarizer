import math
import zlib
from collections import Counter
from typing import Literal, Protocol

import httpx
import structlog

from app.core.config import Settings
from app.llm.http import post_json
from app.rag.text import code_tokens

log = structlog.get_logger()

Task = Literal["document", "query"]
MAX_EMBED_CHARS = 8000


class Embedder(Protocol):
    model: str
    dimension: int

    async def embed(self, texts: list[str], *, task: Task = "document") -> list[list[float]]: ...


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class HashingEmbedder:
    """Deterministic bag-of-identifiers embedding (feature hashing). No network, no key.

    Captures shared vocabulary, not meaning. Used for tests and for running without an embedding
    provider; real semantic quality needs GeminiEmbedder.
    """

    model = "hashing-v1"

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str], *, task: Task = "document") -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token, count in Counter(code_tokens(text)).items():
            h = zlib.crc32(token.encode())
            sign = 1.0 if (h >> 31) & 1 else -1.0
            vec[h % self.dimension] += sign * (1 + math.log(count))
        return _normalize(vec)


class GeminiEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        dimension: int,
        http: httpx.AsyncClient,
        *,
        base_url: str = "https://generativelanguage.googleapis.com",
        batch_size: int = 64,
        timeout_s: float = 60.0,
    ) -> None:
        self.model, self.dimension = model, dimension
        self._key, self._http, self._timeout = api_key, http, timeout_s
        self._url = f"{base_url}/v1beta/models/{model}:batchEmbedContents"
        self._batch = min(batch_size, 100)  # the API accepts at most 100 texts per request

    async def embed(self, texts: list[str], *, task: Task = "document") -> list[list[float]]:
        task_type = "RETRIEVAL_DOCUMENT" if task == "document" else "RETRIEVAL_QUERY"
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = texts[i : i + self._batch]
            body = {
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": t[:MAX_EMBED_CHARS]}]},
                        "taskType": task_type,
                        "outputDimensionality": self.dimension,
                    }
                    for t in batch
                ]
            }
            data = await post_json(
                self._http,
                self._url,
                headers={"x-goog-api-key": self._key},
                body=body,
                timeout_s=self._timeout,
                provider="gemini-embedding",
            )
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(batch):
                from app.llm.base import ProviderUnavailable

                raise ProviderUnavailable("gemini-embedding returned the wrong number of vectors")
            # Truncated Gemini embeddings are not unit length; cosine search expects them to be.
            out += [_normalize([float(v) for v in e["values"]]) for e in embeddings]
        return out


def build_embedder(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    if settings.gemini_api_key:
        return GeminiEmbedder(
            settings.gemini_api_key,
            settings.embedding_model,
            settings.embedding_dim,
            http,
            batch_size=settings.embed_batch_size,
            timeout_s=settings.llm_timeout_s,
        )
    log.warning("no_embedding_provider", fallback="hashing-v1")
    return HashingEmbedder(settings.embedding_dim)
