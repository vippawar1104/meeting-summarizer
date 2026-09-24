import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    TESTING = "testing"
    RISK = "risk"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"


SEVERITY_WEIGHT = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.INFO: 1,
}


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    severity: Severity
    category: Category
    message: str = Field(min_length=1, max_length=2000)
    suggested_patch: str | None = None
    confidence: float = Field(ge=0, le=1)

    @field_validator("severity", "category", mode="before")
    @classmethod
    def _lower(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _percent(cls, v: Any) -> Any:
        # Models sometimes answer 85 instead of 0.85.
        if isinstance(v, int | float) and not isinstance(v, bool) and 1 < v <= 100:
            return v / 100
        return v

    @field_validator("suggested_patch", mode="before")
    @classmethod
    def _blank_patch(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v


@dataclass
class ParseOutcome:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> Any:
    body = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in the reply") from None
        return json.loads(body[start : end + 1])


def parse_findings(text: str) -> ParseOutcome:
    out = ParseOutcome()
    try:
        obj = extract_json(text)
    except ValueError as exc:
        out.errors.append(f"reply is not valid JSON: {exc}")
        return out
    items = obj.get("findings") if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        out.errors.append('expected a JSON object like {"findings": [...]}')
        return out
    for i, item in enumerate(items):
        try:
            out.findings.append(Finding.model_validate(item))
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            out.errors.append(f"findings[{i}].{loc}: {first['msg']}")
    return out
