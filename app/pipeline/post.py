import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.findings import SEVERITY_WEIGHT, Finding

SEVERITY_LABEL = {
    "critical": "🔴 Critical",
    "high": "🟠 High",
    "medium": "🟡 Medium",
    "low": "🔵 Low",
    "info": "⚪ Note",
}


def review_marker(head_sha: str) -> str:
    return f"<!-- reviewly:review:{head_sha} -->"


def fingerprint(f: Finding) -> str:
    """Stable id for 'the same finding' so a dismissed false positive can be suppressed later."""
    norm = re.sub(r"[^a-z0-9 ]", "", f.message.lower())
    norm = " ".join(norm.split()[:12])
    return hashlib.sha1(f"{f.category}|{f.file}|{norm}".encode()).hexdigest()[:12]  # noqa: S324


def format_comment(f: Finding) -> str:
    parts = [f"**{SEVERITY_LABEL[f.severity]} · {f.category}**", "", f.message.strip()]
    patch = (f.suggested_patch or "").rstrip("\n")
    if patch:
        # A ```suggestion block replaces exactly the commented line, so only single-line patches
        # may use it; anything longer is shown as plain code instead of risking a wrong edit.
        fence = "suggestion" if "\n" not in patch else ""
        parts += ["", f"```{fence}", patch, "```"]
    parts += ["", f"<sub>Reviewly · confidence {round(f.confidence * 100)}%</sub>"]
    parts.append(f"<!-- reviewly:finding:{fingerprint(f)} -->")
    return "\n".join(parts)


@dataclass
class SummaryInfo:
    files_reviewed: int
    groups_total: int
    groups_failed: int = 0
    skipped_for_size: list[str] = field(default_factory=list)
    not_shown: int = 0
    config_warning: str | None = None
    model: str = ""
    prompt_version: str = ""


def build_summary(findings: list[Finding], info: SummaryInfo, head_sha: str) -> str:
    lines = ["## 🤖 Reviewly review", ""]
    if findings:
        counts = Counter(f.severity for f in findings)
        by_sev = " · ".join(
            f"{counts[s]} {s}" for s in sorted(counts, key=lambda s: -SEVERITY_WEIGHT[s])
        )
        lines.append(
            f"Found **{len(findings)}** issue(s) in {info.files_reviewed} file(s): {by_sev}."
        )
    else:
        lines.append(f"No issues found in the {info.files_reviewed} file(s) reviewed.")
    if info.not_shown:
        lines.append(
            f"\n{info.not_shown} lower-priority finding(s) were not posted (comment limit)."
        )
    if info.skipped_for_size:
        shown = ", ".join(f"`{p}`" for p in info.skipped_for_size[:10])
        more = (
            f" and {len(info.skipped_for_size) - 10} more"
            if len(info.skipped_for_size) > 10
            else ""
        )
        lines.append(
            f"\n⚠️ **Partial review:** this PR is large, so these were skipped: {shown}{more}."
        )
    if info.groups_failed:
        lines.append(
            f"\n⚠️ {info.groups_failed} of {info.groups_total} section(s) could not be reviewed "
            "because the AI provider was unavailable."
        )
    if info.config_warning:
        lines.append(f"\nℹ️ {info.config_warning}")
    if info.model:
        lines.append(f"\n<sub>{info.model} · prompt {info.prompt_version}</sub>")
    lines.append(review_marker(head_sha))
    return "\n".join(lines)


def build_review_payload(
    findings: list[Finding], summary: str, head_sha: str, *, inline: bool = True
) -> dict[str, Any]:
    payload: dict[str, Any] = {"commit_id": head_sha, "event": "COMMENT", "body": summary}
    if inline:
        payload["comments"] = [
            {"path": f.file, "line": f.line, "side": "RIGHT", "body": format_comment(f)}
            for f in findings
        ]
    else:
        # Fallback when GitHub rejects inline placement: keep the findings, drop the anchors.
        listing = "\n\n".join(f"**`{f.file}:{f.line}`**\n{format_comment(f)}" for f in findings)
        payload["body"] = f"{summary}\n\n---\n\n{listing}" if findings else summary
    return payload


def has_review_marker(reviews: list[dict[str, Any]], head_sha: str) -> bool:
    marker = review_marker(head_sha)
    return any(marker in (r.get("body") or "") for r in reviews)
