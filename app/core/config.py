from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REVIEWLY_", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg://reviewly:reviewly@localhost:5432/reviewly"
    redis_url: str = "redis://localhost:6379/0"

    github_webhook_secret: str = "dev-secret"
    delivery_ttl_seconds: int = 7 * 24 * 3600

    queue_prefix: str = "rq:"
    worker_concurrency: int = 4
    per_installation_cap: int = 2  # max in-flight jobs per installation, across all workers
    visibility_timeout_s: int = 120  # a job whose worker stops heartbeating is requeued
    job_timeout_s: float = 90
    max_attempts: int = 5
    backoff_base_s: float = 5.0
    backoff_cap_s: float = 300.0
    poll_interval_s: float = 0.2
    reconcile_interval_s: float = 30.0
    reconcile_grace_s: float = 60.0  # only re-enqueue jobs untouched for this long
    shutdown_grace_s: float = 25.0

    # GitHub App
    github_api_url: str = "https://api.github.com"
    github_app_id: str | None = None
    github_private_key: str | None = None  # PEM contents

    # LLM providers, tried in this order. Each entry is `provider` or `provider:model`, so the same
    # provider can appear twice with different models. Entries without a key are skipped.
    provider_order: str = "groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b,gemini,mistral"
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    mistral_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    groq_model: str = "openai/gpt-oss-120b"
    mistral_model: str = "mistral-large-latest"
    llm_timeout_s: float = 60.0
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 30.0

    # Repo context (RAG). Off by default until the eval harness shows it improves precision.
    rag_enabled: bool = False
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    embed_batch_size: int = 64
    chunk_max_chars: int = 2000
    max_index_files: int = 2000
    max_file_bytes: int = 200_000
    index_concurrency: int = 8
    retention_days: int = 30  # indexed code not seen for this long is deleted
    retrieval_k: int = 20  # candidates per retriever before fusion
    rrf_k: int = 60
    context_chunks: int = 4
    context_chars: int = 6000
    rerank_enabled: bool = True

    # Cost and abuse control
    daily_token_budget: int = 500_000  # per installation per UTC day; 0 = unlimited
    llm_rate_per_min: float = 120.0  # LLM calls per installation
    llm_rate_burst: int = 20
    llm_rate_max_wait_s: float = 5.0
    cache_enabled: bool = True
    cache_ttl_days: int = 7
    redaction_enabled: bool = True
    injection_findings: bool = True  # flag added lines that try to instruct the AI reviewer

    # Billing (Stripe test mode) and free tier
    free_reviews_per_month: int = (
        20  # per installation; 0 = unlimited. A placeholder, not a pricing decision
    )
    public_url: str = "http://localhost:8000"
    stripe_secret_key: str | None = None
    stripe_price_id: str | None = None
    stripe_webhook_secret: str | None = None

    # Dashboard
    dashboard_secret: str = "dev-dashboard-secret-change-me"  # signs session cookies
    dashboard_dev_login: bool = False  # local-only login without a GitHub OAuth app
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None

    # Review pipeline
    prompt_version: str = "v3"  # v3 scored significantly better than v1 (see README, eval/)
    max_group_chars: int = 12_000  # one LLM call reviews at most this much diff text
    max_review_chars: int = 120_000  # beyond this the review is partial
    review_concurrency: int = 4
    max_comments: int = 25
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
