from pathlib import Path

from app.pipeline.hunks import HunkGroup

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_system_prompt(version: str) -> str:
    path = PROMPT_DIR / version / "system.txt"
    if not path.is_file() or "/" in version or ".." in version:
        raise ValueError(f"unknown prompt version {version!r}")
    return path.read_text()


def defang(text: str) -> str:
    """Stop untrusted text from closing or forging our data delimiters."""
    return text.replace("<untrusted_", "<untrusted-").replace("</untrusted_", "</untrusted-")


def render_user_prompt(group: HunkGroup, *, pr_title: str, rules: list[str]) -> str:
    parts = []
    if rules:
        parts.append("Repository rules (from the repository owner):")
        parts += [f"- {r}" for r in rules]
        parts.append("")
    parts += [
        f"<untrusted_pr_title>{defang(pr_title)[:300]}</untrusted_pr_title>",
        "",
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
