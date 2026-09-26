"""Signed session cookies for the dashboard. A session lists the installations its user may see."""

import time
from dataclasses import dataclass
from typing import Any

import jwt

COOKIE = "reviewly_session"
STATE_COOKIE = "reviewly_oauth_state"
SESSION_TTL_S = 12 * 3600


@dataclass(frozen=True)
class Session:
    login: str
    installations: frozenset[int]


def make_token(
    secret: str,
    login: str,
    installations: list[int],
    *,
    ttl_s: int = SESSION_TTL_S,
    now: float | None = None,
) -> str:
    issued = int(now if now is not None else time.time())
    claims: dict[str, Any] = {
        "sub": login,
        "inst": sorted(installations),
        "iat": issued,
        "exp": issued + ttl_s,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def read_token(secret: str, token: str | None, *, now: float | None = None) -> Session | None:
    """Any problem (missing, forged, expired, malformed) is simply 'not logged in'."""
    if not token:
        return None
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"], "verify_exp": now is None},
        )
        if now is not None and claims["exp"] <= now:
            return None
        return Session(str(claims["sub"]), frozenset(int(i) for i in claims.get("inst", [])))
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
