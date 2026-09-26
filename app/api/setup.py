"""One-click GitHub App creation for whoever hosts Reviewly (GitHub's "manifest flow").

    1. Set REVIEWLY_SETUP_TOKEN and REVIEWLY_PUBLIC_URL, start the app, open /setup?token=...
    2. Press the button; GitHub shows the app with every setting pre-filled; confirm.
    3. GitHub redirects back here with a one-time code; we exchange it for the app's credentials and
       show them ONCE, formatted as environment variables.

Nothing is stored. The page only exists while REVIEWLY_SETUP_TOKEN is set and no app is configured.
"""

import html
import json
import secrets
import time

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.auth import settings_of
from app.core.config import Settings

router = APIRouter()
CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"
STATE_TTL_S = 600

STYLE = (
    "body{font:15px/1.5 system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 16px;color:#111}"
    "h1{font-size:22px}code,pre{background:#f1f1f3;border-radius:6px;padding:2px 6px}"
    "pre{padding:14px;overflow:auto;font-size:12.5px}button{font:inherit;padding:10px 16px;border-radius:8px;"
    "border:1px solid #111;background:#111;color:#fff;cursor:pointer}.warn{border:1px solid #111;border-radius:8px;padding:10px 14px}"
)


def _enabled(s: Settings) -> bool:
    return bool(s.setup_token) and not s.github_app_id


def _guard(request: Request) -> Settings:
    s = settings_of(request)
    if not _enabled(s):
        raise HTTPException(
            status_code=404, detail="not found"
        )  # looks like the page does not exist
    return s


def manifest(s: Settings) -> dict[str, object]:
    base = s.public_url.rstrip("/")
    return {
        "name": "Reviewly",
        "url": base,
        "hook_attributes": {"url": f"{base}/webhooks/github", "active": True},
        "redirect_url": f"{base}/setup/callback",
        "callback_urls": [f"{base}/auth/github/callback"],
        "public": True,
        # the least it needs: read code, read/write pull requests (reviews, comments, reactions)
        "default_permissions": {"contents": "read", "metadata": "read", "pull_requests": "write"},
        "default_events": [
            "pull_request",
            "push",
            "pull_request_review_comment",
            "pull_request_review_thread",
        ],
        "request_oauth_on_install": False,
        "setup_on_update": False,
    }


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title><style>{STYLE}</style>{body}",
        status_code=status,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, token: str = "") -> HTMLResponse:
    s = _guard(request)
    if not secrets.compare_digest(token, s.setup_token or ""):
        raise HTTPException(status_code=404, detail="not found")
    state = jwt.encode(
        {"exp": int(time.time()) + STATE_TTL_S, "n": secrets.token_urlsafe(8)},
        s.dashboard_secret,
        algorithm="HS256",
    )
    return _page(
        "Create the Reviewly GitHub App",
        f"""<h1>Create the Reviewly GitHub App</h1>
<p>This creates a GitHub App named Reviewly for <code>{html.escape(s.public_url)}</code> with the permissions it needs
(read code; read and write pull requests) and sends you back here with its credentials.</p>
<form method="post" action="https://github.com/settings/apps/new?state={html.escape(state)}">
<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest(s)))}">
<button type="submit">Create the GitHub App on GitHub</button></form>""",
    )


@router.get("/setup/callback", response_class=HTMLResponse)
async def setup_callback(request: Request, code: str = "", state: str = "") -> HTMLResponse:
    s = _guard(request)
    try:
        jwt.decode(state, s.dashboard_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _page(
            "Setup expired", "<h1>This setup link expired</h1><p>Open /setup again.</p>", 400
        )
    if not code:
        return _page("Setup failed", "<h1>GitHub did not send a code</h1>", 400)

    http: httpx.AsyncClient = request.app.state.http
    resp = await http.post(
        CONVERSION_URL.format(code=code), headers={"Accept": "application/vnd.github+json"}
    )
    if resp.status_code >= 400:
        return _page(
            "Setup failed",
            "<h1>GitHub would not finish creating the app</h1><p>The code may have expired or already been used. Open /setup and try again.</p>",
            400,
        )
    app = resp.json()
    pem = (
        str(app["pem"]).replace("\r", "").strip().replace("\n", "\\n")
    )  # one line, safe in an env var
    env = "\n".join(
        [
            f"REVIEWLY_GITHUB_APP_ID={app['id']}",
            f"REVIEWLY_GITHUB_APP_SLUG={app['slug']}",
            f'REVIEWLY_GITHUB_PRIVATE_KEY="{pem}"',
            f"REVIEWLY_GITHUB_WEBHOOK_SECRET={app['webhook_secret']}",
            f"REVIEWLY_GITHUB_OAUTH_CLIENT_ID={app['client_id']}",
            f"REVIEWLY_GITHUB_OAUTH_CLIENT_SECRET={app['client_secret']}",
        ]
    )
    install = f"https://github.com/apps/{app['slug']}/installations/new"
    return _page(
        "Reviewly GitHub App created",
        f"""<h1>The GitHub App was created</h1>
<p class="warn"><b>Copy these now.</b> They contain secrets, are not stored by Reviewly, and will not be shown again.
Put them in your host's environment (for Fly.io: <code>fly secrets set ...</code>), remove
<code>REVIEWLY_SETUP_TOKEN</code>, and restart.</p>
<pre>{html.escape(env)}</pre>
<p>Then install it on a repository: <a href="{html.escape(install)}">{html.escape(install)}</a></p>""",
    )
