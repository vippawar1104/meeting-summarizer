from pathlib import Path

from app.pipeline.hunks import HunkGroup
from app.pipeline.sanitize import defang
from app.rag.feedback import FeedbackContext
from app.rag.retrieval import ContextChunk

__all__ = ["defang", "load_system_prompt", "render_user_prompt", "repair_prompt"]

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_system_prompt(version: str) -> str:
    path = PROMPT_DIR / version / "system.txt"
    if not path.is_file() or "/" in version or ".." in version:
        raise ValueError(f"unknown prompt version {version!r}")
    return path.read_text()


def render_context(chunks: list[ContextChunk]) -> str:
    lines = [
        "<untrusted_context>",
        "Related code from elsewhere in this repository (reference only):",
    ]
    for c in chunks:
        label = f"{c.kind} {c.name}" if c.name else c.kind
        lines += ["", f"--- {defang(c.path)}:{c.start_line}-{c.end_line} ({defang(label)})"]
        lines.append(defang(c.content))
    lines.append("</untrusted_context>")
    return "\n".join(lines)


def render_feedback(fb: FeedbackContext) -> str:
    lines = ["<untrusted_feedback>"]
    if fb.dismissed:
        lines.append("Dismissed by maintainers (do not raise similar findings):")
        lines += [f"- [{n.category}] {defang(n.file)}: {defang(n.message)}" for n in fb.dismissed]
    if fb.accepted:
        lines.append("Accepted by maintainers (examples of findings they found useful):")
        lines += [f"- [{n.category}] {defang(n.file)}: {defang(n.message)}" for n in fb.accepted]
    lines.append("</untrusted_feedback>")
    return "\n".join(lines)


def render_user_prompt(
    group: HunkGroup,
    *,
    pr_title: str,
    rules: list[str],
    context: list[ContextChunk] | None = None,
    feedback: FeedbackContext | None = None,
) -> str:
    parts = []
    if rules:
        parts.append("Repository rules (from the repository owner):")
        parts += [f"- {r}" for r in rules]
        parts.append("")
    parts += [f"<untrusted_pr_title>{defang(pr_title)[:300]}</untrusted_pr_title>", ""]
    if feedback:
        parts += [render_feedback(feedback), ""]
    if context:
        parts += [render_context(context), ""]
    parts += [
        "<untrusted_diff>",
        defang(group.text),
        "</untrusted_diff>",
        "",
        "Return the JSON object now.",
    ]
    return "\n".join(parts)


def repair_prompt(errors: list[str]) -> str:
    shown = "\n".join(f"- {e}" for e in errors[:8])
    return (
        "Your previous reply could not be used:\n"
        f"{shown}\n"
        "Reply again with ONLY a valid JSON object of the form "
        '{"findings": [...]} following the schema exactly.'
    )
