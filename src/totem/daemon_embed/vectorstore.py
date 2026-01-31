from __future__ import annotations

import sqlite3
from typing import Iterable, Protocol

from .models import VectorRecord


class VectorStore(Protocol):
    def get_existing_chunk_hashes(self, chunk_hashes: list[str], *, model: str, dim: int) -> set[str]:
        ...

    def upsert(self, vectors: list[VectorRecord]) -> None:
        ...

    def delete_dangling_embeddings(self, *, model: str, dim: int) -> int:
        ...


class SqliteVectorStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_existing_chunk_hashes(self, chunk_hashes: list[str], *, model: str, dim: int) -> set[str]:
        if not chunk_hashes:
            return set()
        qmarks = ",".join(["?"] * len(chunk_hashes))
        rows = self._conn.execute(
            f"SELECT chunk_hash FROM chunk_embeddings WHERE model = ? AND dim = ? AND chunk_hash IN ({qmarks})",
            (model, dim, *chunk_hashes),
        ).fetchall()
        return {str(r["chunk_hash"]) for r in rows}

    def upsert(self, vectors: list[VectorRecord]) -> None:
        if not vectors:
            return
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO chunk_embeddings(chunk_hash, model, dim, vector, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            [(v.chunk_hash, v.model, v.dim, v.vector, _now_iso_utc()) for v in vectors],
        )

    def delete_dangling_embeddings(self, *, model: str, dim: int) -> int:
        cur = self._conn.execute(
            """
            DELETE FROM chunk_embeddings
            WHERE model = ? AND dim = ?
              AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.chunk_hash = chunk_embeddings.chunk_hash)
            """,
            (model, dim),
        )
        return int(cur.rowcount or 0)


def _now_iso_utc() -> str:
    # Avoid importing datetime in hot path for tests; keep deterministic format.
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

