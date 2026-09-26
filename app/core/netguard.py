"""Guard for user-supplied URLs (a "custom OpenAI-compatible endpoint").

Without this, a user could point Reviewly at http://169.254.169.254/ (cloud metadata) or an internal
service and use our server as a proxy into the private network (SSRF). We accept only https URLs on
a public hostname whose every DNS answer is a globally routable address.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeURL(ValueError):
    pass


_BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_global or ip.is_multicast:
        raise UnsafeURL("that address is not on the public internet")


def check_shape(url: str, *, allow_http: bool = False) -> tuple[str, str, int | None]:
    parts = urlsplit(url.strip())
    if parts.scheme not in (("https", "http") if allow_http else ("https",)):
        raise UnsafeURL("the URL must start with https://")
    host = parts.hostname
    if not host:
        raise UnsafeURL("the URL has no host")
    if parts.username or parts.password:
        raise UnsafeURL("the URL must not contain credentials")
    if parts.fragment or parts.query:
        raise UnsafeURL("the URL must not contain a query or fragment")
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURL("that hostname is not on the public internet")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeURL("invalid port") from None
    return parts.scheme, host, port


async def resolve_public(host: str, port: int) -> list[str]:
    """Every address `host` resolves to, after checking that ALL of them are public.

    Raises UnsafeURL if any answer is private, loopback, link-local, etc. (one bad answer is
    enough: a hostile DNS server can mix public and internal addresses).
    """
    try:
        return [str(_ip_literal(host, checked=True))]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise UnsafeURL("that hostname could not be resolved") from None
    if not infos:
        raise UnsafeURL("that hostname could not be resolved")
    addresses: list[str] = []
    for info in infos:
        _check_ip(ipaddress.ip_address(info[4][0]))
        addresses.append(str(info[4][0]))
    return addresses


def _ip_literal(host: str, *, checked: bool) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(host)  # ValueError if `host` is a name, not an address
    if checked:
        _check_ip(ip)
    return ip


async def validate_base_url(url: str, *, allow_http: bool = False) -> str:
    """Returns the normalised URL (no trailing slash) or raises UnsafeURL."""
    _, host, port = check_shape(url, allow_http=allow_http)
    try:
        literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        _check_ip(literal)  # a literal IP address: check it directly
    else:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise UnsafeURL("that hostname could not be resolved") from None
        if not infos:
            raise UnsafeURL("that hostname could not be resolved")
        for info in infos:
            _check_ip(ipaddress.ip_address(info[4][0]))  # EVERY DNS answer must be public
    return url.strip().rstrip("/")
