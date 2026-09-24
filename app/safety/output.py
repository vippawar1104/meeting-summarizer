"""Clean model-written text before it is posted to GitHub.

The model's output is influenced by untrusted code, so it is untrusted too: it must not be able to
ping people, load remote images (data exfiltration), plant links, or forge our hidden markers.
"""

import re

MAX_MESSAGE_CHARS = 2000

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_IMAGE_MD = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_MD = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)[^)]*\)")
_URL = re.compile(r"https?://[^\s)>\]]+")
_HTML_DANGEROUS = re.compile(
    r"</?(?:img|a|script|iframe|object|embed|form|input|style|link|meta)\b[^>]*>", re.I
)
_MENTION = re.compile(r"(?<![\w`/])@([A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[A-Za-z0-9_.-]+)?)")
_TRUSTED_HOSTS = ("github.com", "docs.github.com")


def _trusted(url: str) -> bool:
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    return host in _TRUSTED_HOSTS


def sanitize_message(text: str) -> str:
    text = _COMMENT.sub("", text)  # would let the model forge <!-- reviewly:review:... --> markers
    text = _IMAGE_MD.sub(lambda m: m[1], text)
    text = _HTML_DANGEROUS.sub("", text)
    text = _LINK_MD.sub(lambda m: m[0] if _trusted(m[2]) else m[1], text)
    text = _URL.sub(lambda m: m[0] if _trusted(m[0]) else "[link removed]", text)
    text = _MENTION.sub(lambda m: f"`@{m[1]}`", text)  # code-formatted mentions never notify
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return text


def fence_for(code: str) -> str:
    """A backtick fence longer than any run inside `code`, so it cannot break out of the block."""
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)
