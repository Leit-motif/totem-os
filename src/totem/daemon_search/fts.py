from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def ensure_chunk_fts_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    # Store chunk text directly in FTS table (no external content mode).
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
          chunk_id UNINDEXED,
          chunk_hash UNINDEXED,
          rel_path UNINDEXED,
          heading_path,
          content
        )
        """
    )


def rebuild_chunk_fts(
    conn: sqlite3.Connection,
    *,
    vault_root,
    full: bool,
) -> dict[str, int]:
    ensure_chunk_fts_schema(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Deterministic ordering for rebuild/upserts.
    chunks = conn.execute(
        """
        SELECT c.chunk_id, c.chunk_hash, f.rel_path, c.heading_path, c.start_byte, c.end_byte
        FROM chunks c
        JOIN files f ON f.id = c.file_id
        ORDER BY f.rel_path ASC, c.ord ASC, c.start_byte ASC
        """
    ).fetchall()

    existing = {}
    if not full:
        rows = conn.execute("SELECT chunk_id, chunk_hash FROM chunk_fts").fetchall()
        existing = {str(r["chunk_id"]): str(r["chunk_hash"]) for r in rows}

    inserted = 0
    updated = 0
    skipped = 0

    disk_seen = set()

    with conn:
        if full:
            conn.execute("DELETE FROM chunk_fts")

        for r in chunks:
            chunk_id = str(r["chunk_id"])
            chunk_hash = str(r["chunk_hash"])
            rel_path = str(r["rel_path"])
            heading_path = str(r["heading_path"] or "")
            start_byte = int(r["start_byte"])
            end_byte = int(r["end_byte"])
            disk_seen.add(chunk_id)

            if not full and existing.get(chunk_id) == chunk_hash:
                skipped += 1
                continue

            data = (vault_root / rel_path).read_bytes()
            chunk_bytes = data[start_byte:end_byte]
            content = chunk_bytes.decode("utf-8", errors="strict")

            # Deterministic upsert: delete then insert.
            if not full and chunk_id in existing:
                conn.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (chunk_id,))
                updated += 1
            else:
                inserted += 1

            conn.execute(
                "INSERT INTO chunk_fts(chunk_id, chunk_hash, rel_path, heading_path, content) VALUES(?, ?, ?, ?, ?)",
                (chunk_id, chunk_hash, rel_path, heading_path, content),
            )

        if not full:
            # Delete FTS rows whose chunks no longer exist.
            stale_ids = sorted(set(existing.keys()) - disk_seen)
            if stale_ids:
                conn.executemany("DELETE FROM chunk_fts WHERE chunk_id = ?", [(cid,) for cid in stale_ids])

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "ts": now}


def fts_query(
    conn: sqlite3.Connection,
    *,
    query: str,
    limit: int,
    allowed_file_ids: list[int] | None,
    date_from: str | None,
    date_to: str | None,
) -> list[sqlite3.Row]:
    ensure_chunk_fts_schema(conn)
    match_query = _to_fts5_query(query)
    where = ["chunk_fts MATCH ?"]
    params: list[object] = [match_query]

    join_filter = ""
    if allowed_file_ids is not None:
        if not allowed_file_ids:
            return []
        qmarks = ",".join(["?"] * len(allowed_file_ids))
        join_filter += f" AND f.id IN ({qmarks})"
        params.extend(allowed_file_ids)

    if date_from is not None:
        where.append("effective_date >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append("effective_date <= ?")
        params.append(date_to)

    where_sql = " AND ".join(where)

    return conn.execute(
        f"""
        WITH candidates AS (
          SELECT
            c.file_id AS file_id,
            f.rel_path AS rel_path,
            f.title AS title,
            f.mtime_ns AS mtime_ns,
            COALESCE(f.fm_journal_date, date(CAST(f.mtime_ns / 1000000000 AS INTEGER), 'unixepoch')) AS effective_date,
            c.chunk_id AS chunk_id,
            c.chunk_hash AS chunk_hash,
            c.heading_path AS heading_path,
            c.start_byte AS start_byte,
            c.end_byte AS end_byte,
            bm25(chunk_fts) AS bm25
          FROM chunk_fts
          JOIN chunks c ON c.chunk_id = chunk_fts.chunk_id
          JOIN files f ON f.id = c.file_id
          WHERE {where_sql}{join_filter}
        )
        SELECT *
        FROM candidates
        ORDER BY bm25 ASC, mtime_ns DESC, rel_path ASC, start_byte ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


def _to_fts5_query(query: str) -> str:
    """Convert arbitrary user text into a safe FTS5 MATCH expression.

    Deterministic default: treat the user's query as a literal phrase (sanitized).
    """
    q = (query or "").strip()
    if not q:
        return '""'

    # Escape double quotes for FTS5 phrase syntax.
    q = q.replace('"', '""')
    return f"\"{q}\""
