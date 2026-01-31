from __future__ import annotations

import sqlite3


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_milestones_1_2_present(conn: sqlite3.Connection) -> None:
    # Minimal existence checks.
    conn.execute("SELECT 1 FROM files LIMIT 1").fetchone()
    conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone()
    conn.execute("SELECT 1 FROM chunk_embeddings LIMIT 1").fetchone()


def resolve_allowed_file_ids_by_tags(
    conn: sqlite3.Connection,
    *,
    tags: list[str],
    tag_or: bool,
) -> list[int] | None:
    tags = [t.lstrip("#").strip() for t in tags if t.strip()]
    if not tags:
        return None

    qmarks = ",".join(["?"] * len(tags))
    if tag_or:
        rows = conn.execute(
            f"""
            SELECT DISTINCT ft.file_id AS file_id
            FROM file_tags ft
            JOIN tags t ON t.id = ft.tag_id
            WHERE t.name IN ({qmarks})
            ORDER BY ft.file_id ASC
            """,
            (*tags,),
        ).fetchall()
        return [int(r["file_id"]) for r in rows]

    # AND semantics: require all tags.
    rows = conn.execute(
        f"""
        SELECT ft.file_id AS file_id
        FROM file_tags ft
        JOIN tags t ON t.id = ft.tag_id
        WHERE t.name IN ({qmarks})
        GROUP BY ft.file_id
        HAVING COUNT(DISTINCT t.name) = ?
        ORDER BY ft.file_id ASC
        """,
        (*tags, len(tags)),
    ).fetchall()
    return [int(r["file_id"]) for r in rows]


def get_chunk_hashes_for_filters(
    conn: sqlite3.Connection,
    *,
    allowed_file_ids: list[int] | None,
    date_from: str | None,
    date_to: str | None,
) -> list[str] | None:
    where = []
    params: list[object] = []

    if allowed_file_ids is not None:
        if not allowed_file_ids:
            return []
        qmarks = ",".join(["?"] * len(allowed_file_ids))
        where.append(f"file_id IN ({qmarks})")
        params.extend(allowed_file_ids)

    if date_from is not None:
        where.append("effective_date >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append("effective_date <= ?")
        params.append(date_to)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    rows = conn.execute(
        f"""
        WITH base AS (
          SELECT
            c.chunk_hash AS chunk_hash,
            COALESCE(f.fm_journal_date, date(CAST(f.mtime_ns / 1000000000 AS INTEGER), 'unixepoch')) AS effective_date,
            f.id AS file_id
          FROM chunks c
          JOIN files f ON f.id = c.file_id
        )
        SELECT chunk_hash
        FROM base
        {where_sql}
        ORDER BY chunk_hash ASC
        """,
        params,
    ).fetchall()
    return [str(r["chunk_hash"]) for r in rows]


def get_expansion_neighbors(
    conn: sqlite3.Connection,
    *,
    file_ids: list[int],
    cap: int,
) -> list[sqlite3.Row]:
    if not file_ids or cap <= 0:
        return []

    qmarks = ",".join(["?"] * len(file_ids))
    # 1-hop expansion by outlinks + backlinks (match backlinks by files.title).
    return conn.execute(
        f"""
        WITH primary_files AS (
          SELECT id, title FROM files WHERE id IN ({qmarks})
        ),
        outlink_targets AS (
          SELECT DISTINCT o.target AS target
          FROM outlinks o
          WHERE o.file_id IN ({qmarks})
        ),
        backlinks AS (
          SELECT DISTINCT o.file_id AS file_id
          FROM outlinks o
          JOIN primary_files pf ON pf.title = o.target
        ),
        outlink_files AS (
          SELECT DISTINCT f.id AS file_id
          FROM files f
          JOIN outlink_targets t ON t.target = f.title
        ),
        neighbors AS (
          SELECT file_id FROM backlinks
          UNION
          SELECT file_id FROM outlink_files
        )
        SELECT f.id AS file_id, f.rel_path AS rel_path, f.title AS title, f.mtime_ns AS mtime_ns,
               COALESCE(f.fm_journal_date, date(CAST(f.mtime_ns / 1000000000 AS INTEGER), 'unixepoch')) AS effective_date
        FROM files f
        JOIN neighbors n ON n.file_id = f.id
        WHERE f.id NOT IN ({qmarks})
        ORDER BY f.mtime_ns DESC, f.rel_path ASC
        LIMIT ?
        """,
        (*file_ids, *file_ids, *file_ids, cap),
    ).fetchall()
