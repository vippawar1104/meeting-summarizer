import base64
from typing import Any

import httpx

from app.github.auth import GitHubAppAuth
from app.github.diff import build_diff_from_files


class GitHubError(Exception):
    pass


class GitHubNotFound(GitHubError):
    pass


class GitHubValidation(GitHubError):
    """422: GitHub rejected the payload (e.g. an inline comment on a line it cannot place)."""


class GitHubRateLimited(GitHubError):
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class GitHubTransient(GitHubError):
    pass


class GitHubClient:
    def __init__(
        self,
        auth: GitHubAppAuth,
        http: httpx.AsyncClient,
        *,
        api_url: str = "https://api.github.com",
    ) -> None:
        self._auth, self._http, self._api = auth, http, api_url

    async def _request(
        self,
        installation_id: int,
        method: str,
        path: str,
        *,
        accept: str = "application/vnd.github+json",
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        for attempt in (1, 2):
            token = await self._auth.installation_token(installation_id)
            try:
                resp = await self._http.request(
                    method,
                    f"{self._api}{path}",
                    headers={"Authorization": f"Bearer {token}", "Accept": accept},
                    json=json,
                    params=params,
                    timeout=30,
                )
            except httpx.HTTPError as exc:
                raise GitHubTransient(f"network error: {exc}") from exc
            if resp.status_code == 401 and attempt == 1:
                await self._auth.invalidate(installation_id)  # token revoked or expired early
                continue
            break
        return self._check(resp)

    @staticmethod
    def _check(resp: httpx.Response) -> httpx.Response:
        code = resp.status_code
        if code < 400:
            return resp
        text = resp.text[:300]
        if code == 404:
            raise GitHubNotFound(text)
        if code == 429 or (code == 403 and _is_rate_limit(resp)):
            try:
                retry_after: float | None = float(resp.headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            raise GitHubRateLimited(text, retry_after)
        if code == 422:
            raise GitHubValidation(text)
        if code in (401, 403):
            raise GitHubError(f"access denied ({code}): {text}")
        raise GitHubTransient(f"HTTP {code}: {text}")

    async def get_pr(self, inst: int, repo: str, number: int) -> dict[str, Any]:
        resp = await self._request(inst, "GET", f"/repos/{repo}/pulls/{number}")
        data: dict[str, Any] = resp.json()
        return data

    async def get_repo(self, inst: int, repo: str) -> dict[str, Any]:
        data: dict[str, Any] = (await self._request(inst, "GET", f"/repos/{repo}")).json()
        return data

    async def get_branch_head(self, inst: int, repo: str, branch: str) -> str:
        resp = await self._request(inst, "GET", f"/repos/{repo}/commits/{branch}")
        return str(resp.json()["sha"])

    async def get_tree(self, inst: int, repo: str, sha: str) -> tuple[list[dict[str, Any]], bool]:
        """The whole repo tree at a commit. `truncated` is True when GitHub cut it short."""
        resp = await self._request(
            inst, "GET", f"/repos/{repo}/git/trees/{sha}", params={"recursive": 1}
        )
        data = resp.json()
        return list(data.get("tree", [])), bool(data.get("truncated", False))

    async def get_blob(self, inst: int, repo: str, blob_sha: str) -> bytes | None:
        try:
            resp = await self._request(inst, "GET", f"/repos/{repo}/git/blobs/{blob_sha}")
        except GitHubNotFound:
            return None
        data = resp.json()
        if data.get("encoding") != "base64":
            return str(data.get("content", "")).encode()
        return base64.b64decode(data["content"])

    async def get_diff(self, inst: int, repo: str, number: int) -> str:
        try:
            resp = await self._request(
                inst,
                "GET",
                f"/repos/{repo}/pulls/{number}",
                accept="application/vnd.github.v3.diff",
            )
            return resp.text
        except (GitHubValidation, GitHubTransient) as exc:
            # 406/422 "diff too large": rebuild it from the paginated files endpoint.
            if "too_large" not in str(exc) and "exceeded" not in str(exc) and "406" not in str(exc):
                raise
        return await self._diff_from_files(inst, repo, number)

    async def _diff_from_files(self, inst: int, repo: str, number: int) -> str:
        files: list[dict[str, Any]] = []
        for page in range(1, 31):  # GitHub caps the files endpoint at 3000 files
            resp = await self._request(
                inst,
                "GET",
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            batch = resp.json()
            files += batch
            if len(batch) < 100:
                break
        return build_diff_from_files(files)

    async def get_file(self, inst: int, repo: str, path: str, ref: str) -> str | None:
        try:
            resp = await self._request(
                inst,
                "GET",
                f"/repos/{repo}/contents/{path}",
                accept="application/vnd.github.raw+json",
                params={"ref": ref},
            )
        except GitHubNotFound:
            return None
        return resp.text

    async def list_reviews(self, inst: int, repo: str, number: int) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for page in range(1, 11):
            resp = await self._request(
                inst,
                "GET",
                f"/repos/{repo}/pulls/{number}/reviews",
                params={"per_page": 100, "page": page},
            )
            batch = resp.json()
            reviews += batch
            if len(batch) < 100:
                break
        return reviews

    async def create_review(
        self, inst: int, repo: str, number: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        resp = await self._request(
            inst, "POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload
        )
        data: dict[str, Any] = resp.json()
        return data


def _is_rate_limit(resp: httpx.Response) -> bool:
    if resp.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in resp.headers:
        return True
    return "rate limit" in resp.text.lower()
