"""Tokenising code for lexical search and hashing embeddings.

Identifiers are split on snake_case and camelCase so `getUserById` matches `get_user_by_id` and
"user". Every token is lowercase [a-z0-9] only, which keeps it safe to use in a tsquery.
"""

import re
from collections import Counter

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

STOPWORDS = frozenset(
    """
    the and for not with this that from are was but all any can has have had its into out use
    def class return import from self cls none true false null nil void int str string bool var let
    const function func public private protected static final new try except catch finally raise
    throw async await yield lambda elif else while pass break continue print len range
    """.split()
)


def subtokens(ident: str) -> list[str]:
    parts = [p.lower() for chunk in ident.split("_") if chunk for p in _PART.findall(chunk)]
    return [p for p in parts if len(p) >= 2]


def code_tokens(text: str) -> list[str]:
    out: list[str] = []
    for ident in _IDENT.findall(text):
        subs = subtokens(ident)
        if len(subs) > 1:
            out.append("".join(subs))  # getuserbyid: the whole identifier as one term
        out += subs
    return [t for t in out if t not in STOPWORDS and len(t) >= 3]


def search_text(path: str, name: str | None, content: str) -> str:
    return " ".join(code_tokens(f"{path} {name or ''} {content}"))


def query_terms(text: str, limit: int = 12) -> list[str]:
    """The most distinctive identifiers in `text`: frequent, and longer words beat short ones."""
    counts = Counter(code_tokens(text))
    ranked = sorted(counts, key=lambda t: (-(counts[t] + len(t) / 8), t))
    return ranked[:limit]
