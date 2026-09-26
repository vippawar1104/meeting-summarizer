from typing import Any

from fastapi import APIRouter, Request

from app.api.auth import settings_of

router = APIRouter(prefix="/api")


@router.get("/config")
async def public_config(request: Request) -> dict[str, Any]:
    """Non-secret settings the dashboard needs before anyone is signed in."""
    s = settings_of(request)
    return {
        "app_install_url": f"https://github.com/apps/{s.github_app_slug}/installations/new" if s.github_app_slug else None,
        "github_login": bool(s.github_oauth_client_id and s.github_oauth_client_secret),
        # local development only: a login that needs no GitHub OAuth app (off unless env is dev)
        "dev_login": bool(s.dashboard_dev_login and s.env == "dev"),
    }  # fmt: skip
