from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import current_session, require_installation, settings_of
from app.billing.stripe import StripeClient, StripeError
from app.core.session import Session
from app.dashboard.queries import findings_page, overview

router = APIRouter(prefix="/api")


@router.get("/me")
async def me(session: Session = Depends(current_session)) -> dict[str, Any]:
    return {"login": session.login, "installations": sorted(session.installations)}


@router.get("/installations/{installation_id}/overview")
async def installation_overview(
    request: Request, installation_id: int = Depends(require_installation)
) -> dict[str, Any]:
    return await overview(request.app.state.sessionmaker, settings_of(request), installation_id)


@router.get("/installations/{installation_id}/findings")
async def installation_findings(
    request: Request,
    installation_id: int = Depends(require_installation),
    feedback: str | None = Query(default=None, pattern="^(accepted|dismissed|pending)$"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return await findings_page(
        request.app.state.sessionmaker, installation_id, feedback=feedback, limit=limit
    )


@router.post("/installations/{installation_id}/billing/checkout")
async def start_checkout(
    request: Request, installation_id: int = Depends(require_installation)
) -> dict[str, str]:
    s = settings_of(request)
    if not (s.stripe_secret_key and s.stripe_price_id):
        raise HTTPException(status_code=503, detail="billing is not configured")
    try:
        session = await StripeClient(
            s.stripe_secret_key, request.app.state.http
        ).create_checkout_session(
            installation_id, s.stripe_price_id, f"{s.public_url}/?upgraded=1", f"{s.public_url}/"
        )
    except StripeError:
        raise HTTPException(status_code=502, detail="could not start checkout") from None
    return {"url": str(session["url"])}
