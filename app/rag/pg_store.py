"""Postgres chunk store: pgvector for semantic search, tsvector for lexical (BM25-style) search.

Vector search is an exact scan restricted to one (installation, repo) by a btree index. That keeps
results correct for small tenants: an ANN index (HNSW) applies its filter *after* picking
candidates, so a small repo in a large shared table could get back nothing. Repos are small enough
that an exact scan is fast; revisit if a single repo grows past ~100k chunks.
"""

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import MetaData, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.rag.chunker import Chunk
from app.rag.store import Hit, RepoState, StoredChunk
from app.rag.text import search_text

EMBEDDING_DIM = 768  # fixed by the migration; changing models/dims needs a re-embed migration

rag_metadata = MetaData()

code_chunks = sa.Table(
    "code_chunks",
    rag_metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("installation_id", sa.BigInteger, nullable=False),
    sa.Column("repo_full_name", sa.String, nullable=False),
    sa.Column("path", sa.String, nullable=False),
    sa.Column("start_line", sa.Integer, nullable=False),
    sa.Column("end_line", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=True),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("search_text", sa.Text, nullable=False),
    sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    sa.Column("embedding_model", sa.String, nullable=False),
    sa.Column(
        "tsv",
        postgresql.TSVECTOR,
        sa.Computed("to_tsvector('simple', search_text)", persisted=True),
    ),
    sa.Index("ix_code_chunks_repo_path", "installation_id", "repo_full_name", "path"),
    sa.Index("ix_code_chunks_tsv", "tsv", postgresql_using="gin"),
)

indexed_files = sa.Table(
    "indexed_files",
    rag_metadata,
    sa.Column("installation_id", sa.BigInteger, primary_key=True),
    sa.Column("repo_full_name", sa.String, primary_key=True),
    sa.Column("path", sa.String, primary_key=True),
    sa.Column("blob_sha", sa.String, nullable=False),
    sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
)

repo_index = sa.Table(
    "repo_index",
    rag_metadata,
    sa.Column("installation_id", sa.BigInteger, primary_key=True),
    sa.Column("repo_full_name", sa.String, primary_key=True),
    sa.Column("head_sha", sa.String, nullable=False),
    sa.Column("file_count", sa.Integer, nullable=False),
    sa.Column("truncated", sa.Boolean, nullable=False),
    sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
)

RAG_TABLES = {"code_chunks", "indexed_files", "repo_index"}
_TERM = re.compile(r"^[a-z0-9]+$")


def _rows_affected(res: object) -> int:
    assert isinstance(res, CursorResult)
    return int(res.rowcount)


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def _hit(row: sa.Row, score: float) -> Hit:  # type: ignore[type-arg]
    m = row._mapping
    chunk = StoredChunk(
        m["id"], m["path"], m["start_line"], m["end_line"], m["kind"], m["name"], m["content"]
    )
    return Hit(chunk, float(score))


class PgChunkStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def file_shas(self, inst: int, repo: str) -> dict[str, str]:
        q = sa.select(indexed_files.c.path, indexed_files.c.blob_sha).where(
            indexed_files.c.installation_id == inst, indexed_files.c.repo_full_name == repo
        )
        async with self._sm() as s:
            return {p: sha for p, sha in (await s.execute(q)).all()}

    async def replace_file(
        self,
        inst: int,
        repo: str,
        path: str,
        blob_sha: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        model: str,
    ) -> None:
        assert len(chunks) == len(embeddings)
        rows = [
            {
                "id": uuid4().hex,
                "installation_id": inst,
                "repo_full_name": repo,
                "path": c.path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "kind": c.kind,
                "name": c.name,
                "content": c.content,
                "search_text": search_text(c.path, c.name, c.content),
                "embedding": e,
                "embedding_model": model,
            }
            for c, e in zip(chunks, embeddings, strict=True)
        ]
        now = datetime.now(UTC)
        async with self._sm.begin() as s:
            await s.execute(
                sa.delete(code_chunks).where(
                    code_chunks.c.installation_id == inst,
                    code_chunks.c.repo_full_name == repo,
                    code_chunks.c.path == path,
                )
            )
            if rows:
                await s.execute(sa.insert(code_chunks), rows)
            stmt = postgresql.insert(indexed_files).values(
                installation_id=inst,
                repo_full_name=repo,
                path=path,
                blob_sha=blob_sha,
                last_seen=now,
            )
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["installation_id", "repo_full_name", "path"],
                    set_={"blob_sha": blob_sha, "last_seen": now},
                )
            )

    async def delete_files(self, inst: int, repo: str, paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        async with self._sm.begin() as s:
            await s.execute(
                sa.delete(code_chunks).where(
                    code_chunks.c.installation_id == inst,
                    code_chunks.c.repo_full_name == repo,
                    code_chunks.c.path.in_(paths),
                )
            )
            res = await s.execute(
                sa.delete(indexed_files).where(
                    indexed_files.c.installation_id == inst,
                    indexed_files.c.repo_full_name == repo,
                    indexed_files.c.path.in_(paths),
                )
            )
            return _rows_affected(res)

    async def touch_files(self, inst: int, repo: str, paths: Iterable[str]) -> None:
        paths = list(paths)
        if not paths:
            return
        async with self._sm.begin() as s:
            await s.execute(
                sa.update(indexed_files)
                .where(
                    indexed_files.c.installation_id == inst,
                    indexed_files.c.repo_full_name == repo,
                    indexed_files.c.path.in_(paths),
                )
                .values(last_seen=datetime.now(UTC))
            )

    async def vector_search(
        self, inst: int, repo: str, embedding: list[float], model: str, k: int
    ) -> list[Hit]:
        sql = text(
            """
            SELECT id, path, start_line, end_line, kind, name, content,
                   1 - (embedding <=> CAST(:q AS vector)) AS score
            FROM code_chunks
            WHERE installation_id = :inst AND repo_full_name = :repo AND embedding_model = :model
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
            """
        )
        async with self._sm() as s:
            rows = (
                await s.execute(
                    sql, {"q": _vec(embedding), "inst": inst, "repo": repo, "model": model, "k": k}
                )
            ).all()
        return [_hit(r, r._mapping["score"]) for r in rows]

    async def lexical_search(self, inst: int, repo: str, terms: list[str], k: int) -> list[Hit]:
        safe = [t for t in terms if _TERM.match(t)]
        if not safe:
            return []
        sql = text(
            """
            SELECT id, path, start_line, end_line, kind, name, content,
                   ts_rank_cd(tsv, to_tsquery('simple', :q)) AS score
            FROM code_chunks
            WHERE installation_id = :inst AND repo_full_name = :repo
              AND tsv @@ to_tsquery('simple', :q)
            ORDER BY score DESC
            LIMIT :k
            """
        )
        async with self._sm() as s:
            rows = (
                await s.execute(sql, {"q": " | ".join(safe), "inst": inst, "repo": repo, "k": k})
            ).all()
        return [_hit(r, r._mapping["score"]) for r in rows]

    async def set_repo_state(
        self, inst: int, repo: str, head_sha: str, file_count: int, truncated: bool
    ) -> None:
        now = datetime.now(UTC)
        stmt = postgresql.insert(repo_index).values(
            installation_id=inst,
            repo_full_name=repo,
            head_sha=head_sha,
            file_count=file_count,
            truncated=truncated,
            indexed_at=now,
        )
        async with self._sm.begin() as s:
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["installation_id", "repo_full_name"],
                    set_={
                        "head_sha": head_sha,
                        "file_count": file_count,
                        "truncated": truncated,
                        "indexed_at": now,
                    },
                )
            )

    async def get_repo_state(self, inst: int, repo: str) -> RepoState | None:
        q = sa.select(repo_index).where(
            repo_index.c.installation_id == inst, repo_index.c.repo_full_name == repo
        )
        async with self._sm() as s:
            row = (await s.execute(q)).first()
        if row is None:
            return None
        return RepoState(row.head_sha, row.file_count, row.truncated, row.indexed_at)

    async def delete_repo(self, inst: int, repo: str) -> int:
        async with self._sm.begin() as s:
            res = await s.execute(
                sa.delete(code_chunks).where(
                    code_chunks.c.installation_id == inst, code_chunks.c.repo_full_name == repo
                )
            )
            for t in (indexed_files, repo_index):
                await s.execute(
                    sa.delete(t).where(t.c.installation_id == inst, t.c.repo_full_name == repo)
                )
            return _rows_affected(res)

    async def delete_installation(self, inst: int) -> int:
        async with self._sm.begin() as s:
            res = await s.execute(
                sa.delete(code_chunks).where(code_chunks.c.installation_id == inst)
            )
            for t in (indexed_files, repo_index):
                await s.execute(sa.delete(t).where(t.c.installation_id == inst))
            return _rows_affected(res)

    async def purge_older_than(self, cutoff: datetime) -> int:
        async with self._sm.begin() as s:
            res = await s.execute(
                text(
                    """
                    DELETE FROM code_chunks c USING indexed_files f
                    WHERE c.installation_id = f.installation_id
                      AND c.repo_full_name = f.repo_full_name AND c.path = f.path
                      AND f.last_seen < :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
            await s.execute(sa.delete(indexed_files).where(indexed_files.c.last_seen < cutoff))
            await s.execute(sa.delete(repo_index).where(repo_index.c.indexed_at < cutoff))
            return _rows_affected(res)

    async def count_chunks(self, inst: int, repo: str) -> int:
        q = (
            sa.select(sa.func.count())
            .select_from(code_chunks)
            .where(code_chunks.c.installation_id == inst, code_chunks.c.repo_full_name == repo)
        )
        async with self._sm() as s:
            return int((await s.execute(q)).scalar_one())
