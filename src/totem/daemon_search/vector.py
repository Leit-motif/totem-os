from __future__ import annotations

import sqlite3

from totem.daemon_embed.embedder import DeterministicSha256Embedder
from totem.daemon_embed.vectorstore import SqliteVectorStore


def embed_query(query: str, *, dim: int) -> bytes:
    embedder = DeterministicSha256Embedder(dim)
    return embedder.embed_text(query)


def vector_search(
    conn: sqlite3.Connection,
    *,
    query_vector: bytes,
    model: str,
    dim: int,
    top_k: int,
    allowed_chunk_hashes: list[str] | None,
) -> list[tuple[str, float]]:
    store = SqliteVectorStore(conn)
    return store.search(
        query_vector,
        model=model,
        dim=dim,
        top_k=top_k,
        allowed_chunk_hashes=allowed_chunk_hashes,
    )

