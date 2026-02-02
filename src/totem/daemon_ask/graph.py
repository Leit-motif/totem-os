from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from totem.daemon_search import db as search_db
from totem.daemon_search.excerpts import ExcerptConfig, make_excerpt
from totem.daemon_search.models import DaemonSearchConfig, SearchHit


@dataclass(frozen=True)
class GraphExpandConfig:
    expand_cap: int
    rep_chunk_ord: int


def graph_expand(
    conn: sqlite3.Connection,
    *,
    search_cfg: DaemonSearchConfig,
    query: str,
    primary_hits: list[SearchHit],
    cfg: GraphExpandConfig,
) -> list[SearchHit]:
    """Deterministic 1-hop expansion over the daemon vault link graph.

    Properties:
    - append-only: returned hits are meant to be appended after primary hits
    - bounded: aggressive caps
    - deterministic: stable ordering + stable representative chunk choice
    """
    cap = max(0, int(cfg.expand_cap))
    if cap <= 0 or not primary_hits:
        return []

    file_ids = []
    for h in primary_hits:
        row = conn.execute("SELECT id FROM files WHERE rel_path = ? LIMIT 1", (h.rel_path,)).fetchone()
        if row is None:
            continue
        file_ids.append(int(row["id"]))
    if not file_ids:
        return []

    neighbors = search_db.get_expansion_neighbors(conn, file_ids=file_ids, cap=cap)
    if not neighbors:
        return []

    ex_cfg = ExcerptConfig(
        max_chars=search_cfg.excerpt_max_chars,
        before_chars=search_cfg.context_before_chars,
        after_chars=search_cfg.context_after_chars,
    )

    expanded: list[SearchHit] = []
    for n in neighbors:
        fid = int(n["file_id"])
        rep = conn.execute(
            """
            SELECT heading_path, start_byte, end_byte
            FROM chunks
            WHERE file_id = ? AND ord = ?
            ORDER BY start_byte ASC
            LIMIT 1
            """,
            (fid, int(cfg.rep_chunk_ord)),
        ).fetchone()
        if rep is None:
            # Fallback: first chunk by ord ASC
            rep = conn.execute(
                """
                SELECT heading_path, start_byte, end_byte
                FROM chunks
                WHERE file_id = ?
                ORDER BY ord ASC, start_byte ASC
                LIMIT 1
                """,
                (fid,),
            ).fetchone()
        if rep is None:
            continue

        rel_path = str(n["rel_path"])
        data = (search_cfg.vault_root / rel_path).read_bytes()
        excerpt = make_excerpt(
            file_bytes=data,
            start_byte=int(rep["start_byte"]),
            end_byte=int(rep["end_byte"]),
            query=query,
            cfg=ex_cfg,
        )
        expanded.append(
            SearchHit(
                score=0.0,
                rel_path=rel_path,
                title=str(n["title"] or ""),
                heading_path=str(rep["heading_path"] or ""),
                start_byte=int(rep["start_byte"]),
                end_byte=int(rep["end_byte"]),
                effective_date=str(n["effective_date"]),
                mtime_ns=int(n["mtime_ns"]),
                excerpt=excerpt,
                expanded_context=True,
            )
        )

    return expanded

