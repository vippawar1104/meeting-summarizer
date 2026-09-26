"""Response headers for the dashboard and API. The dashboard is a same-origin React build, so the
policy can be strict: only our own scripts, styles and connections, and no framing (clickjacking)."""

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",  # React sets a few inline style attributes
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# These pages load third-party assets or post to GitHub, so the strict policy would break them.
CSP_EXEMPT_PREFIXES = ("/setup", "/docs", "/redoc", "/openapi.json")


def security_headers(path: str, *, production: bool) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if not path.startswith(CSP_EXEMPT_PREFIXES):
        headers["Content-Security-Policy"] = CSP
    if production:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
