from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .models import FileRecord, Heading, Outlink


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(
          key TEXT PRIMARY KEY,
          value TEXT
        );

        CREATE TABLE IF NOT EXISTS files(
          id INTEGER PRIMARY KEY,
          rel_path TEXT UNIQUE NOT NULL,
          title TEXT,
          mtime_ns INTEGER NOT NULL,
          size_bytes INTEGER NOT NULL,
          content_hash TEXT NOT NULL,
          fm_journal_date TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS headings(
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL,
          ord INTEGER NOT NULL,
          level INTEGER NOT NULL,
          text TEXT NOT NULL,
          start_byte INTEGER NOT NULL,
          end_byte INTEGER NOT NULL,
          start_line INTEGER,
          end_line INTEGER,
          FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tags(
          id INTEGER PRIMARY KEY,
          name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_tags(
          file_id INTEGER NOT NULL,
          tag_id INTEGER NOT NULL,
          source TEXT NOT NULL,
          PRIMARY KEY(file_id, tag_id, source),
          FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
          FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS outlinks(
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL,
          ord INTEGER NOT NULL,
          target TEXT NOT NULL,
          section TEXT,
          alias TEXT,
          raw TEXT NOT NULL,
          start_byte INTEGER NOT NULL,
          end_byte INTEGER NOT NULL,
          start_line INTEGER,
          end_line INTEGER,
          FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_files_rel_path ON files(rel_path);
        CREATE INDEX IF NOT EXISTS idx_outlinks_target ON outlinks(target);
        CREATE INDEX IF NOT EXISTS idx_headings_file_id ON headings(file_id);
        CREATE INDEX IF NOT EXISTS idx_file_tags_tag_id ON file_tags(tag_id);
        """
    )


def drop_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS outlinks;
        DROP TABLE IF EXISTS file_tags;
        DROP TABLE IF EXISTS tags;
        DROP TABLE IF EXISTS headings;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS meta;
        """
    )


def get_file_row(conn: sqlite3.Connection, rel_path: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM files WHERE rel_path = ?", (rel_path,)).fetchone()


def list_file_rel_paths(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT rel_path FROM files ORDER BY rel_path").fetchall()
    return [r["rel_path"] for r in rows]


def upsert_file(conn: sqlite3.Connection, record: FileRecord, updated_at: str) -> int:
    row = conn.execute(
        """
        INSERT INTO files(rel_path, title, mtime_ns, size_bytes, content_hash, fm_journal_date, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
          title=excluded.title,
          mtime_ns=excluded.mtime_ns,
          size_bytes=excluded.size_bytes,
          content_hash=excluded.content_hash,
          fm_journal_date=excluded.fm_journal_date,
          updated_at=excluded.updated_at
        RETURNING id
        """,
        (
            record.rel_path,
            record.title,
            record.mtime_ns,
            record.size_bytes,
            record.content_hash,
            record.fm_journal_date,
            updated_at,
        ),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    # Fallback for older SQLite versions without RETURNING
    row2 = conn.execute("SELECT id FROM files WHERE rel_path = ?", (record.rel_path,)).fetchone()
    assert row2 is not None
    return int(row2["id"])


def update_file_metadata_only(
    conn: sqlite3.Connection,
    file_id: int,
    mtime_ns: int,
    size_bytes: int,
    updated_at: str,
) -> None:
    conn.execute(
        "UPDATE files SET mtime_ns = ?, size_bytes = ?, updated_at = ? WHERE id = ?",
        (mtime_ns, size_bytes, updated_at, file_id),
    )


def replace_headings(conn: sqlite3.Connection, file_id: int, headings: Iterable[Heading]) -> None:
    conn.execute("DELETE FROM headings WHERE file_id = ?", (file_id,))
    conn.executemany(
        """
        INSERT INTO headings(file_id, ord, level, text, start_byte, end_byte, start_line, end_line)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                file_id,
                h.ord,
                h.level,
                h.text,
                h.start_byte,
                h.end_byte,
                h.start_line,
                h.end_line,
            )
            for h in headings
        ],
    )


def replace_outlinks(conn: sqlite3.Connection, file_id: int, outlinks: Iterable[Outlink]) -> None:
    conn.execute("DELETE FROM outlinks WHERE file_id = ?", (file_id,))
    conn.executemany(
        """
        INSERT INTO outlinks(file_id, ord, target, section, alias, raw, start_byte, end_byte, start_line, end_line)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                file_id,
                o.ord,
                o.target,
                o.section,
                o.alias,
                o.raw,
                o.start_byte,
                o.end_byte,
                o.start_line,
                o.end_line,
            )
            for o in outlinks
        ],
    )


def _get_or_create_tag_id(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    assert row is not None
    return int(row["id"])


def replace_file_tags(
    conn: sqlite3.Connection,
    file_id: int,
    frontmatter_tags: Iterable[str],
    inline_tags: Iterable[str],
) -> None:
    conn.execute("DELETE FROM file_tags WHERE file_id = ?", (file_id,))

    rows: list[tuple[int, int, str]] = []
    for name in sorted(set(frontmatter_tags)):
        tag_id = _get_or_create_tag_id(conn, name)
        rows.append((file_id, tag_id, "frontmatter"))
    for name in sorted(set(inline_tags)):
        tag_id = _get_or_create_tag_id(conn, name)
        rows.append((file_id, tag_id, "inline"))

    conn.executemany(
        "INSERT OR IGNORE INTO file_tags(file_id, tag_id, source) VALUES(?, ?, ?)",
        rows,
    )


def delete_files_by_rel_path(conn: sqlite3.Connection, rel_paths: Iterable[str]) -> int:
    rel_paths = list(rel_paths)
    if not rel_paths:
        return 0
    conn.executemany("DELETE FROM files WHERE rel_path = ?", [(p,) for p in rel_paths])
    return len(rel_paths)

