from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

MIN_CONFIDENCE = {"low": 0.8, "medium": 0.6, "high": 0.4}


class RepoConfig(BaseModel):
    """Per-repo `.reviewly.yml`, read from the PR's base branch (never from the PR itself)."""

    ignore: list[str] = Field(default_factory=list, max_length=100)
    strictness: Literal["low", "medium", "high"] = "medium"
    rules: list[str] = Field(default_factory=list, max_length=20)
    max_comments: int | None = Field(default=None, ge=1, le=100)

    @field_validator("rules")
    @classmethod
    def _rule_length(cls, v: list[str]) -> list[str]:
        return [r.strip()[:300] for r in v if r.strip()]

    @property
    def min_confidence(self) -> float:
        return MIN_CONFIDENCE[self.strictness]


def parse_repo_config(text: str | None) -> tuple[RepoConfig, str | None]:
    """Returns (config, warning). A broken config never blocks a review: fall back to defaults."""
    if not text or not text.strip():
        return RepoConfig(), None
    try:
        data = yaml.safe_load(text)
        if data is None:
            return RepoConfig(), None
        if not isinstance(data, dict):
            return RepoConfig(), ".reviewly.yml must be a mapping; using defaults"
        return RepoConfig.model_validate(data), None
    except yaml.YAMLError:
        return RepoConfig(), ".reviewly.yml is not valid YAML; using defaults"
    except ValidationError as exc:
        loc = ".".join(str(p) for p in exc.errors()[0]["loc"])
        return RepoConfig(), f".reviewly.yml invalid ({loc}); using defaults"
