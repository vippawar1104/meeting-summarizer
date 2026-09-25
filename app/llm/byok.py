"""Bring your own key: an installation's own provider, model and API key.

Rules that make this safe to offer:
  * the key is encrypted at rest, decrypted only to make a review's calls, and never returned by the API;
  * a custom endpoint must be a public https URL (see app.core.netguard), checked when saved AND used;
  * a BYOK installation NEVER falls back to Reviewly's own models. Their code goes only to the
    provider they chose, and a failing key must not silently spend the platform's tokens;
  * their own key means their own bill, so the platform's daily token budget and free-tier limit
    do not apply (the per-installation rate limit still does).
"""

import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import SecretBox
from app.core.netguard import UnsafeURL, validate_base_url
from app.cost.guard import GuardedRouter
from app.cost.ratelimit import RateLimiter
from app.db.models import LLMSettingsRow
from app.llm.base import (
    BadRequest,
    LLMError,
    LLMProvider,
    Message,
    ProviderUnavailable,
    RateLimited,
    RouterLike,
)
from app.llm.catalog import CATALOG, MODEL_RE, build_provider
from app.llm.router import LLMRouter

MIN_KEY, MAX_KEY = 8, 500


class ByokError(Exception):
    """A problem the user can act on; the message is safe to show them."""


def key_hint(api_key: str) -> str:
    return "..." + api_key[-4:]


def scrub(text: str, secret: str) -> str:
    """Providers sometimes echo (part of) the key in an error; never pass that on."""
    return text.replace(secret, "***") if secret else text


async def get_row(
    sm: async_sessionmaker[AsyncSession], installation_id: int
) -> LLMSettingsRow | None:
    async with sm() as s:
        return await s.get(LLMSettingsRow, installation_id)


async def delete_settings(sm: async_sessionmaker[AsyncSession], installation_id: int) -> bool:
    async with sm() as s:
        row = await s.get(LLMSettingsRow, installation_id)
        if row is None:
            return False
        await s.delete(row)
        await s.commit()
        return True


async def validate_input(
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    *,
    have_key: bool,
    allow_http: bool,
) -> str | None:
    """Checks what the user typed; returns the normalised base URL (or None)."""
    info = CATALOG.get(provider)
    if info is None:
        raise ByokError("Choose one of the supported providers.")
    if not MODEL_RE.match(model or ""):
        raise ByokError(
            "Enter the model name exactly as your provider spells it (letters, digits, . _ : / -)."
        )
    if api_key is None and not have_key:
        raise ByokError("Enter your API key.")
    if api_key is not None and not (
        MIN_KEY <= len(api_key) <= MAX_KEY and api_key.strip() == api_key and " " not in api_key
    ):
        raise ByokError("That does not look like an API key (no spaces, 8 to 500 characters).")
    if info.needs_base_url:
        if not base_url:
            raise ByokError(
                "Enter the base URL of your OpenAI-compatible endpoint, e.g. https://openrouter.ai/api/v1"
            )
        try:
            return await validate_base_url(base_url, allow_http=allow_http)
        except UnsafeURL as exc:
            raise ByokError(f"That base URL cannot be used: {exc}.") from None
    return None


async def check_provider(provider: LLMProvider, secret: str) -> tuple[bool, str | None]:
    """One tiny real call, so a wrong key or model is caught when it is saved, not on a PR."""
    messages = [
        Message("system", "Reply with a JSON object."),
        Message("user", 'Return {"ok": true}'),
    ]
    try:
        await asyncio.wait_for(provider.complete(messages, json_mode=True), timeout=45)
    except BadRequest as exc:
        return False, "The provider rejected the request. Check the model name. " + scrub(
            str(exc), secret
        )[:160]
    except RateLimited:
        return False, "Your provider account is rate limited or out of quota."
    except ProviderUnavailable as exc:
        text = str(exc)
        if "HTTP 401" in text or "HTTP 403" in text:
            return False, "The provider rejected this API key (or it lacks access to that model)."
        return False, "Could not get an answer from the provider: " + scrub(text, secret)[:160]
    except (TimeoutError, LLMError, httpx.HTTPError):
        return False, "Could not reach the provider. Check the endpoint and try again."
    return True, None


async def save_settings(
    sm: async_sessionmaker[AsyncSession],
    box: SecretBox,
    installation_id: int,
    *,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    ok: bool,
    error: str | None,
) -> LLMSettingsRow:
    async with sm() as s:
        row = await s.get(LLMSettingsRow, installation_id)
        if row is None:
            assert api_key is not None
            row = LLMSettingsRow(
                installation_id=installation_id, provider=provider, model=model, base_url=base_url,
                api_key_encrypted=box.encrypt(api_key), key_hint=key_hint(api_key),
            )  # fmt: skip
        else:
            row.provider, row.model, row.base_url = provider, model, base_url
            if api_key is not None:  # omitted = keep the stored key
                row.api_key_encrypted, row.key_hint = box.encrypt(api_key), key_hint(api_key)
        row.enabled = True
        row.last_test_ok, row.last_test_error, row.last_test_at = ok, error, datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        s.add(row)
        await s.commit()
        return row


class ByokResolver:
    """Turns an installation's saved settings into a router, or None if it uses the built-in models."""

    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        box: SecretBox,
        http: httpx.AsyncClient,
        limiter: RateLimiter | None = None,
        *,
        allow_http: bool = False,
    ) -> None:
        self._sm, self._box, self._http, self._limiter, self._allow_http = (
            sm,
            box,
            http,
            limiter,
            allow_http,
        )

    async def provider_for(self, installation_id: int) -> tuple[LLMProvider, str] | None:
        row = await get_row(self._sm, installation_id)
        if row is None or not row.enabled:
            return None
        try:
            secret = self._box.decrypt(row.api_key_encrypted)
        except ValueError:
            raise ByokError(
                "Your saved API key can no longer be read. Please enter it again in the dashboard."
            ) from None
        base = None
        if row.provider == "custom":
            try:
                base = await validate_base_url(row.base_url or "", allow_http=self._allow_http)
            except UnsafeURL as exc:
                raise ByokError(f"Your custom endpoint is no longer allowed: {exc}.") from None
        return build_provider(
            row.provider, row.model, secret, base, self._http
        ), f"{row.provider}:{row.model}"

    async def router_for(self, installation_id: int) -> tuple[RouterLike, str] | None:
        found = await self.provider_for(installation_id)
        if found is None:
            return None
        provider, tag = found
        # One provider, no fallback; no platform budget (their key, their bill); redaction still applies.
        return GuardedRouter(
            LLMRouter([provider]), budget=None, limiter=self._limiter, redact=True
        ), tag
