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
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
