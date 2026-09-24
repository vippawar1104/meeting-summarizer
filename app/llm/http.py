from typing import Any

import httpx

from app.llm.base import BadRequest, ProviderUnavailable, RateLimited


def raise_for_status(resp: httpx.Response, provider: str) -> None:
    code = resp.status_code
    if code < 400:
        return
    detail = f"{provider} HTTP {code}: {resp.text[:200]}"
    if code == 429:
        try:
            retry_after: float | None = float(resp.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        raise RateLimited(detail, retry_after)
    if code in (401, 403, 408) or code >= 500:
        # 401/403 = bad or revoked key: unusable until fixed, so treat like an outage.
        raise ProviderUnavailable(detail)
    raise BadRequest(detail)


async def post_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_s: float,
    provider: str,
) -> dict[str, Any]:
    try:
        resp = await http.post(url, json=body, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailable(f"{provider} timed out") from exc
    except httpx.TransportError as exc:
        raise ProviderUnavailable(f"{provider} network error: {exc}") from exc
    raise_for_status(resp, provider)
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderUnavailable(f"{provider} returned a non-JSON body") from exc
    if not isinstance(data, dict):
        raise ProviderUnavailable(f"{provider} returned an unexpected body")
    return data
