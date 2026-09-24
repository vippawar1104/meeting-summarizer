import asyncio
import hashlib

from app.github.client import GitHubNotFound
from app.rag.embeddings import HashingEmbedder


class SpyEmbedder(HashingEmbedder):
    def __init__(self, dimension: int = 768) -> None:
        super().__init__(dimension)
        self.calls: list[tuple[str, int]] = []
        self.fail = False

    async def embed(self, texts, *, task="document"):
        if self.fail:
            raise ConnectionError("embedding provider down")
        self.calls.append((task, len(texts)))
        return await super().embed(texts, task=task)

    def documents_embedded(self) -> int:
        return sum(n for task, n in self.calls if task == "document")


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeRepoGitHub:
    """Serves a repo tree and blobs. `files` maps path -> bytes and can be mutated between runs."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = files or {}
        self.truncated = False
        self.head = "commit1"
        self.default_branch = "main"
        self.missing: set[str] = set()
        self.blob_fetches: list[str] = []
        self.repo_missing = False
        self.in_flight = self.peak = 0
        self.delay = 0.0

    async def get_repo(self, inst, repo):
        if self.repo_missing:
            raise GitHubNotFound("gone")
        return {"default_branch": self.default_branch}

    async def get_branch_head(self, inst, repo, branch):
        assert branch == self.default_branch
        return self.head

    async def get_tree(self, inst, repo, sha):
        if self.repo_missing:
            raise GitHubNotFound("gone")
        entries = [
            {"path": p, "type": "blob", "sha": blob_sha(d), "size": len(d)}
            for p, d in sorted(self.files.items())
        ]
        entries.append({"path": "src", "type": "tree", "sha": "t"})
        return entries, self.truncated

    async def get_blob(self, inst, repo, sha):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            for path, data in self.files.items():
                if blob_sha(data) == sha:
                    if path in self.missing:
                        return None
                    self.blob_fetches.append(path)
                    return data
            return None
        finally:
            self.in_flight -= 1


PY_BILLING = b'''def charge_customer(customer, amount):
    """Charge the customer's card via the payment gateway."""
    validate_amount(amount)
    return gateway.charge(customer.card, amount)


def validate_amount(amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
'''

PY_ICONS = b'''def render_svg_icon(size):
    """Draw a vector icon."""
    return f"<svg width={size}></svg>"
'''

PY_USERS = b"""class UserRepository:
    def find_by_email(self, email):
        return self.db.query("select * from users where email = %s", email)
"""
