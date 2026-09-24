"""Fake secrets assembled at runtime so no realistic-looking token is ever committed literally."""

import random
import string

_R = random.Random(1234)


def rand(n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(_R.choice(alphabet) for _ in range(n))


def fake_secrets() -> dict[str, str]:
    return {
        "aws_access_key": "AK" + "IA" + rand(16, string.ascii_uppercase + string.digits),
        "github_token": "gh" + "p_" + rand(36),
        "slack_token": "xo" + "xb-" + rand(12, string.digits) + "-" + rand(24),
        "stripe_key": "sk" + "_live_" + rand(24),
        "google_api_key": "AI" + "za" + rand(35, string.ascii_letters + string.digits + "_-"),
        "jwt": "ey" + "J" + rand(20) + ".ey" + "J" + rand(20) + "." + rand(30),
        "anthropic_or_openai_key": "sk" + "-" + rand(40),
    }
