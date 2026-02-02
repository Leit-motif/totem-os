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

    def search(
        self,
        query_vector: bytes,
        *,
        model: str,
        dim: int,
        top_k: int,
        allowed_chunk_hashes: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        ...


class SqliteVectorStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_existing_chunk_hashes(self, chunk_hashes: list[str], *, model: str, dim: int) -> set[str]:
        if not chunk_hashes:
            return set()
        # SQLite has a limit on bound parameters; avoid huge IN () lists.
        # If the request is large, load the full set for (model,dim) and intersect.
        if len(chunk_hashes) > 900:
            rows = self._conn.execute(
                "SELECT chunk_hash FROM chunk_embeddings WHERE model = ? AND dim = ?",
                (model, dim),
            ).fetchall()
            existing = {str(r["chunk_hash"]) for r in rows}
            return existing.intersection(set(chunk_hashes))

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

    def search(
        self,
        query_vector: bytes,
        *,
        model: str,
        dim: int,
        top_k: int,
        allowed_chunk_hashes: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        import math
        import struct

        if top_k <= 0:
            return []

        q_vals = struct.unpack("<" + ("f" * dim), query_vector)
        q_norm = math.sqrt(sum((float(x) * float(x)) for x in q_vals))
        if q_norm == 0.0:
            return []

        rows = None
        allowed_set = None
        if allowed_chunk_hashes is not None:
            if not allowed_chunk_hashes:
                return []
            if len(allowed_chunk_hashes) > 900:
                # Avoid huge IN; filter in Python.
                allowed_set = set(allowed_chunk_hashes)
                rows = self._conn.execute(
                    "SELECT chunk_hash, vector FROM chunk_embeddings WHERE model = ? AND dim = ?",
                    (model, dim),
                ).fetchall()
            else:
                qmarks = ",".join(["?"] * len(allowed_chunk_hashes))
                rows = self._conn.execute(
                    f"SELECT chunk_hash, vector FROM chunk_embeddings WHERE model = ? AND dim = ? AND chunk_hash IN ({qmarks})",
                    (model, dim, *allowed_chunk_hashes),
                ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT chunk_hash, vector FROM chunk_embeddings WHERE model = ? AND dim = ?",
                (model, dim),
            ).fetchall()

        scored: list[tuple[str, float]] = []
        for r in rows:
            ch = str(r["chunk_hash"])
            if allowed_set is not None and ch not in allowed_set:
                continue
            vec = bytes(r["vector"])
            vals = struct.unpack("<" + ("f" * dim), vec)
            v_norm = math.sqrt(sum((float(x) * float(x)) for x in vals))
            if v_norm == 0.0:
                continue
            dot = 0.0
            for a, b in zip(q_vals, vals):
                dot += float(a) * float(b)
            scored.append((ch, dot / (q_norm * v_norm)))

        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored[:top_k]


def _now_iso_utc() -> str:
    # Avoid importing datetime in hot path for tests; keep deterministic format.
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
