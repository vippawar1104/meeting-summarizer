import json

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from app.api.auth import settings_of
from app.billing.stripe import apply_event, verify_signature

router = APIRouter()
log = structlog.get_logger()


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request, stripe_signature: str | None = Header(default=None)
) -> dict[str, str]:
    s = settings_of(request)
    if not s.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="billing is not configured")
    body = await request.body()
    if not verify_signature(body, stripe_signature, s.stripe_webhook_secret):
        raise HTTPException(status_code=400, detail="invalid signature")
    try:
        result = await apply_event(request.app.state.sessionmaker, json.loads(body))
    except Exception:
        log.exception("stripe_event_failed")
        raise HTTPException(
            status_code=503, detail="temporarily unavailable"
        ) from None  # Stripe retries
    return {"status": result}
