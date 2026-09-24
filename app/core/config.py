from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REVIEWLY_", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg://reviewly:reviewly@localhost:5432/reviewly"
    redis_url: str = "redis://localhost:6379/0"

    github_webhook_secret: str = "dev-secret"
    delivery_ttl_seconds: int = 7 * 24 * 3600

    job_stream: str = "reviewly:jobs"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
