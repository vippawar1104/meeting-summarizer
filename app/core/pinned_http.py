"""An HTTP client that can only connect to public internet addresses, checked at connect time.

netguard.validate_base_url checks a custom endpoint's DNS answers when the URL is saved and again
before each use, but the HTTP library then resolves the name a third time to connect. A hostile DNS
server can answer "public" to the check and "127.0.0.1" or "169.254.169.254" to the connection
(DNS rebinding). Here the connection itself resolves the name once, checks every answer, and
connects to that exact address. TLS still uses the original hostname (SNI and certificate check),
so a certificate for the real name is still required.

Environment proxies are ignored (trust_env=False): a proxy would connect on our behalf and skip
the check. Redirects are not followed.
"""

import ssl
from collections.abc import Iterable

import httpcore
import httpx

from app.core.netguard import UnsafeURL, resolve_public

SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (signature fixed by httpcore)
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        last: Exception | None = None
        try:
            addresses = await resolve_public(host, port)
        except UnsafeURL as exc:  # reported as a failed connection, like any unreachable host
            raise httpcore.ConnectError(f"connection blocked: {exc}") from None
        for ip in addresses:
            try:
                return await self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                last = exc
        raise last or httpcore.ConnectError(f"could not connect to {host}")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def make_pinned_client(
    *, timeout: float = 60.0, backend: httpcore.AsyncNetworkBackend | None = None
) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(  # httpx has no public hook for the backend
        ssl_context=ssl.create_default_context(),
        max_connections=20,
        max_keepalive_connections=10,
        network_backend=PinnedBackend(backend),
    )
    return httpx.AsyncClient(
        transport=transport, timeout=timeout, trust_env=False, follow_redirects=False
    )
