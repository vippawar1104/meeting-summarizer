from app.db.models import Job
from app.rag.indexer import RepoIndexer
from app.rag.store import ChunkStore
from worker.runner import Handler


def make_index_handler(indexer: RepoIndexer) -> Handler:
    async def handler(job: Job) -> None:
        # "HEAD" means "whatever the default branch is now" (installation events carry no sha).
        sha = None if job.head_sha == "HEAD" else job.head_sha
        await indexer.index_repo(job.installation_id, job.repo_full_name, sha)

    return handler


def make_purge_handler(store: ChunkStore) -> Handler:
    async def handler(job: Job) -> None:
        if job.repo_full_name == "*":
            await store.delete_installation(job.installation_id)
        else:
            await store.delete_repo(job.installation_id, job.repo_full_name)

    return handler
