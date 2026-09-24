import secrets
import time
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.core.config import Settings
from app.core.session import COOKIE, STATE_COOKIE, Session, make_token, read_token

router = APIRouter()
AUTHORIZE = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def current_session(request: Request) -> Session:
    session = read_token(settings_of(request).dashboard_secret, request.cookies.get(COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return session


def require_installation(installation_id: int, session: Session = Depends(current_session)) -> int:
    """404, not 403: another tenant's installation should look like it does not exist."""
    if installation_id not in session.installations:
        raise HTTPException(status_code=404, detail="not found")
    return installation_id


def _set_session(
    response: Response, settings: Settings, login: str, installations: list[int]
) -> None:
    response.set_cookie(
        COOKIE,
        make_token(settings.dashboard_secret, login, installations),
        httponly=True,
        samesite="lax",
        secure=settings.public_url.startswith("https://"),
        max_age=12 * 3600,
        path="/",
    )


@router.get("/auth/github/login")
async def github_login(request: Request) -> Response:
    s = settings_of(request)
    if not s.github_oauth_client_id:
        raise HTTPException(status_code=503, detail="GitHub login is not configured")
    nonce = secrets.token_urlsafe(16)
    state = jwt.encode(
        {"n": nonce, "exp": int(time.time()) + 600}, s.dashboard_secret, algorithm="HS256"
    )
    url = (
        AUTHORIZE
        + "?"
        + urlencode(
            {
                "client_id": s.github_oauth_client_id,
                "redirect_uri": f"{s.public_url}/auth/github/callback",
                "state": nonce,
            }
        )
    )
    response = RedirectResponse(url)
    response.set_cookie(STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600, path="/")
    return response


@router.get("/auth/github/callback")
async def github_callback(request: Request, code: str = "", state: str = "") -> Response:
    s = settings_of(request)
    if not (s.github_oauth_client_id and s.github_oauth_client_secret):
        raise HTTPException(status_code=503, detail="GitHub login is not configured")
    # CSRF check: the state we sent must come back, and it must match the signed cookie we set.
    try:
        expected = jwt.decode(
            request.cookies.get(STATE_COOKIE, ""), s.dashboard_secret, algorithms=["HS256"]
        )["n"]
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(status_code=400, detail="login expired, try again") from None
    if not code or not state or not secrets.compare_digest(str(expected), state):
        raise HTTPException(status_code=400, detail="invalid login state")

    http = request.app.state.http
    tok = await http.post(
        TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": s.github_oauth_client_id,
            "client_secret": s.github_oauth_client_secret,
            "code": code,
            "redirect_uri": f"{s.public_url}/auth/github/callback",
        },
    )
    access = tok.json().get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="GitHub did not accept the login")
    headers = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
    user = (await http.get(f"{s.github_api_url}/user", headers=headers)).json()
    inst = (await http.get(f"{s.github_api_url}/user/installations", headers=headers)).json()
    ids = [int(i["id"]) for i in inst.get("installations", [])]
    response = RedirectResponse("/")
    _set_session(response, s, str(user.get("login", "unknown")), ids)
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.get("/auth/dev-login")
async def dev_login(request: Request, installation: int) -> Response:
    """Local development only: pretend to be a user of one installation. Off unless both the
    environment is `dev` and REVIEWLY_DASHBOARD_DEV_LOGIN is set, so it cannot exist in production."""
    s = settings_of(request)
    if not (s.dashboard_dev_login and s.env == "dev"):
        raise HTTPException(status_code=404, detail="not found")
    response = RedirectResponse("/")
    _set_session(response, s, "dev", [installation])
    return response


@router.get("/auth/logout")
async def logout() -> Response:
    response = RedirectResponse("/")
    response.delete_cookie(COOKIE, path="/")
    return response
