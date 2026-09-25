"""A tiny fake GitHub API, so the whole system can be exercised locally without a real GitHub App.

    python -m scripts.mock_github            # serves on :9000
    curl -X POST localhost:9000/_debug/pr -d '{"repo":"acme/w","number":1,"diff":"...","title":"..."}'

It checks the App JWT signature when MOCK_APP_PUBLIC_KEY is set, hands out installation tokens, serves
a registered PR's metadata and diff, and records every review and comment posted to it
(GET /_debug/posted). Local development only.
"""

import itertools
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response

app = FastAPI(title="mock-github")
PRS: dict[tuple[str, int], dict[str, Any]] = {}
POSTED: list[dict[str, Any]] = []
REVIEW_IDS = itertools.count(9000)
COMMENT_IDS = itertools.count(5_000_000_000)  # above 2**31, like real GitHub ids


def _pr(repo: str, number: int) -> dict[str, Any]:
    pr = PRS.get((repo, number))
    if pr is None:
        raise HTTPException(status_code=404, detail="no such PR registered")
    return pr


@app.post("/app/installations/{installation_id}/access_tokens", status_code=201)
async def access_token(
    installation_id: int, authorization: str = Header(default="")
) -> dict[str, str]:
    public = os.environ.get("MOCK_APP_PUBLIC_KEY")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing app JWT")
    if public:  # prove Reviewly signs a valid RS256 App JWT
        try:
            jwt.decode(
                authorization[7:], public, algorithms=["RS256"], options={"verify_aud": False}
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail=f"bad app JWT: {exc}") from exc
    expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"token": f"ghs_mock_{installation_id}", "expires_at": expires}


def _check_token(authorization: str) -> None:
    if not authorization.startswith("Bearer ghs_mock_"):
        raise HTTPException(status_code=401, detail="bad installation token")


@app.get("/repos/{owner}/{repo}/pulls/{number}")
async def get_pr(
    owner: str, repo: str, number: int, request: Request, authorization: str = Header(default="")
) -> Response:
    _check_token(authorization)
    pr = _pr(f"{owner}/{repo}", number)
    if "diff" in request.headers.get("accept", ""):
        return Response(pr["diff"], media_type="text/plain")
    import json

    body = {"number": number, "state": pr.get("state", "open"), "title": pr["title"],
            "head": {"sha": pr["sha"]}, "base": {"sha": "base0000"}}  # fmt: skip
    return Response(json.dumps(body), media_type="application/json")


@app.get("/repos/{owner}/{repo}/pulls/{number}/reviews")
async def list_reviews(
    owner: str, repo: str, number: int, authorization: str = Header(default="")
) -> list[dict[str, Any]]:
    _check_token(authorization)
    return _pr(f"{owner}/{repo}", number)["reviews"]


@app.post("/repos/{owner}/{repo}/pulls/{number}/reviews")
async def create_review(
    owner: str, repo: str, number: int, request: Request, authorization: str = Header(default="")
) -> dict[str, Any]:
    _check_token(authorization)
    pr = _pr(f"{owner}/{repo}", number)
    payload = await request.json()
    review_id = next(REVIEW_IDS)
    comments = [{"id": next(COMMENT_IDS), **c} for c in payload.get("comments", [])]
    pr["reviews"].append({"id": review_id, "body": payload.get("body", ""), "comments": comments})
    POSTED.append(
        {
            "kind": "review",
            "repo": f"{owner}/{repo}",
            "number": number,
            "id": review_id,
            **payload,
            "comment_ids": [c["id"] for c in comments],
        }
    )
    return {"id": review_id}


@app.get("/repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/comments")
async def review_comments(
    owner: str, repo: str, number: int, review_id: int, authorization: str = Header(default="")
) -> list[dict[str, Any]]:
    _check_token(authorization)
    for r in _pr(f"{owner}/{repo}", number)["reviews"]:
        if r["id"] == review_id:
            return r["comments"]
    raise HTTPException(status_code=404, detail="no such review")


@app.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
async def issue_comment(
    owner: str, repo: str, number: int, request: Request, authorization: str = Header(default="")
) -> dict[str, int]:
    _check_token(authorization)
    payload = await request.json()
    POSTED.append({"kind": "comment", "repo": f"{owner}/{repo}", "number": number, **payload})
    return {"id": next(COMMENT_IDS)}


@app.get("/repos/{owner}/{repo}/contents/{path:path}")
async def contents(
    owner: str, repo: str, path: str, authorization: str = Header(default="")
) -> Response:
    _check_token(authorization)
    raise HTTPException(status_code=404, detail="Not Found")  # no .reviewly.yml in the mock repo


@app.post("/_debug/pr")
async def register_pr(request: Request) -> dict[str, str]:
    body = await request.json()
    sha = body.get("sha") or f"{int(time.time()):x}abcdef"[:12]
    PRS[(body["repo"], int(body["number"]))] = {
        "title": body.get("title", "Update"),
        "diff": body["diff"],
        "sha": sha,
        "reviews": [],
        "state": "open",
    }
    return {"sha": sha}


@app.get("/_debug/posted")
async def posted() -> list[dict[str, Any]]:
    return POSTED


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "9000")))
