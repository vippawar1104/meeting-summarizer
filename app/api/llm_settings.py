from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import require_installation, settings_of
from app.llm.byok import (
    ByokError,
    check_provider,
    delete_settings,
    get_row,
    save_settings,
    validate_input,
)
from app.llm.catalog import CATALOG, build_provider

router = APIRouter(prefix="/api")
log = structlog.get_logger()
TESTS_PER_HOUR = 10  # each test is a real call to a user-chosen endpoint: keep it from being abused


class LLMSettingsIn(BaseModel):
    provider: str
    model: str = Field(max_length=100)
    api_key: str | None = Field(default=None, max_length=600)  # omit to keep the stored key
    base_url: str | None = Field(default=None, max_length=300)


def _view(row: Any) -> dict[str, Any]:
    """What the dashboard may see: never the key, only its last four characters."""
    return {
        "configured": row is not None,
        "provider": row.provider if row else None,
        "model": row.model if row else None,
        "base_url": row.base_url if row else None,
        "key_hint": row.key_hint if row else None,
        "enabled": row.enabled if row else False,
        "last_test_ok": row.last_test_ok if row else None,
        "last_test_error": row.last_test_error if row else None,
        "last_test_at": row.last_test_at.isoformat() if row and row.last_test_at else None,
        "providers": [
            {"id": p.id, "label": p.label, "needs_base_url": p.needs_base_url, "models": list(p.models), "key_url": p.key_url}
            for p in CATALOG.values()
        ],
    }  # fmt: skip


async def _allow_test(request: Request, installation_id: int) -> None:
    redis = request.app.state.redis
    key = f"llmtest:{installation_id}:{datetime.now(UTC):%Y%m%d%H}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3700)
    if count > TESTS_PER_HOUR:
        raise HTTPException(
            status_code=429, detail="Too many key tests this hour. Try again later."
        )


@router.get("/installations/{installation_id}/llm")
async def read_llm_settings(
    request: Request, installation_id: int = Depends(require_installation)
) -> dict[str, Any]:
    return _view(await get_row(request.app.state.sessionmaker, installation_id))


@router.put("/installations/{installation_id}/llm")
async def save_llm_settings(
    request: Request, body: LLMSettingsIn, installation_id: int = Depends(require_installation)
) -> dict[str, Any]:
    s = settings_of(request)
    sm, box, http = request.app.state.sessionmaker, request.app.state.box, request.app.state.http
    existing = await get_row(sm, installation_id)
    allow_http = s.env == "dev"
    try:
        base = await validate_input(
            body.provider,
            body.model,
            body.api_key,
            body.base_url,
            have_key=existing is not None,
            allow_http=allow_http,
        )
    except ByokError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await _allow_test(request, installation_id)
    secret = body.api_key if body.api_key is not None else box.decrypt(existing.api_key_encrypted)  # type: ignore[union-attr]
    provider = build_provider(body.provider, body.model, secret, base, http, timeout=45.0)
    ok, error = await check_provider(provider, secret)
    if not ok:  # a key that does not work is never saved: it would only break the next pull request
        raise HTTPException(status_code=400, detail=error)
    row = await save_settings(
        sm, box, installation_id, provider=body.provider, model=body.model, api_key=body.api_key,
        base_url=base, ok=True, error=None,
    )  # fmt: skip
    log.info(
        "llm_settings_saved", installation=installation_id, provider=body.provider, model=body.model
    )
    return _view(row)


@router.post("/installations/{installation_id}/llm/test")
async def test_llm_settings(
    request: Request, installation_id: int = Depends(require_installation)
) -> dict[str, Any]:
    sm, box, http = request.app.state.sessionmaker, request.app.state.box, request.app.state.http
    row = await get_row(sm, installation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No API key saved.")
    await _allow_test(request, installation_id)
    secret = box.decrypt(row.api_key_encrypted)
    try:
        base = await validate_input(
            row.provider,
            row.model,
            None,
            row.base_url,
            have_key=True,
            allow_http=settings_of(request).env == "dev",
        )
    except ByokError as exc:
        return {"ok": False, "error": str(exc)}
    ok, error = await check_provider(
        build_provider(row.provider, row.model, secret, base, http, timeout=45.0), secret
    )
    saved = await save_settings(
        sm,
        box,
        installation_id,
        provider=row.provider,
        model=row.model,
        api_key=None,
        base_url=row.base_url,
        ok=ok,
        error=error,
    )
    return {"ok": ok, "error": error, "settings": _view(saved)}


@router.delete("/installations/{installation_id}/llm")
async def remove_llm_settings(
    request: Request, installation_id: int = Depends(require_installation)
) -> dict[str, Any]:
    await delete_settings(request.app.state.sessionmaker, installation_id)
    log.info("llm_settings_removed", installation=installation_id)
    return _view(None)
