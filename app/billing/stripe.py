"""Minimal Stripe client (Checkout + webhooks) over httpx, so the app needs no SDK.

Works with test-mode keys; nothing here has been run against Stripe itself (no keys were available
when this was written), only against mocks.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import SubscriptionRow

API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE_S = 300


class StripeError(Exception):
    pass


def verify_signature(
    payload: bytes,
    header: str | None,
    secret: str,
    *,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
    now: float | None = None,
) -> bool:
    """Stripe-Signature: `t=<unix>,v1=<hex hmac of "<t>.<payload>">[,v1=...]`. The timestamp check
    stops an old, legitimately signed payload from being replayed."""
    if not header:
        return False
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    timestamps = [v for k, v in parts if k == "t"]
    signatures = [v for k, v in parts if k == "v1"]
    if len(timestamps) != 1 or not signatures:
        return False
    try:
        ts = int(timestamps[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


class StripeClient:
    def __init__(self, secret_key: str, http: httpx.AsyncClient) -> None:
        self._key, self._http = secret_key, http

    async def _post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        resp = await self._http.post(f"{API}{path}", data=data, auth=(self._key, ""), timeout=30)
        if resp.status_code >= 400:
            raise StripeError(f"Stripe HTTP {resp.status_code}: {resp.text[:200]}")
        body: dict[str, Any] = resp.json()
        return body

    async def create_checkout_session(
        self, installation_id: int, price_id: str, success_url: str, cancel_url: str
    ) -> dict[str, Any]:
        return await self._post(
            "/checkout/sessions",
            {
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": str(installation_id),
                "subscription_data[metadata][installation_id]": str(installation_id),
            },
        )


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


async def apply_event(sm: async_sessionmaker[AsyncSession], event: dict[str, Any]) -> str:
    """Apply one Stripe event. Every branch is an idempotent upsert: Stripe redelivers and reorders."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if kind == "checkout.session.completed":
        ref = obj.get("client_reference_id")
        if not ref or not str(ref).isdigit():
            return "ignored"
        async with sm() as s:
            created = await s.get(SubscriptionRow, int(ref)) or SubscriptionRow(
                installation_id=int(ref)
            )
            created.plan, created.status = "pro", "active"
            created.stripe_customer_id = obj.get("customer")
            created.stripe_subscription_id = obj.get("subscription")
            created.updated_at = datetime.now(UTC)
            s.add(created)
            await s.commit()
        return "subscribed"

    if kind in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_id = obj.get("id")
        meta = obj.get("metadata") or {}
        async with sm() as s:
            row: SubscriptionRow | None = None
            if str(meta.get("installation_id", "")).isdigit():
                row = await s.get(SubscriptionRow, int(meta["installation_id"]))
            if row is None and sub_id:
                from sqlalchemy import select
                from sqlmodel import col

                row = (
                    await s.execute(
                        select(SubscriptionRow).where(
                            col(SubscriptionRow.stripe_subscription_id) == sub_id
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                return "ignored"
            row.status = "canceled" if kind.endswith("deleted") else obj.get("status", row.status)
            row.stripe_subscription_id = sub_id or row.stripe_subscription_id
            row.current_period_end = _ts(obj.get("current_period_end")) or row.current_period_end
            row.updated_at = datetime.now(UTC)
            s.add(row)
            await s.commit()
        return "updated"

    return "ignored"
