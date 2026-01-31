from __future__ import annotations

import sqlite3


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks(
          id INTEGER PRIMARY KEY,
          file_id INTEGER NOT NULL,
          heading_path TEXT,
          heading_id INTEGER,
          ord INTEGER NOT NULL,
          start_byte INTEGER NOT NULL,
          end_byte INTEGER NOT NULL,
          text_hash TEXT NOT NULL,
          chunk_id TEXT UNIQUE NOT NULL,
          chunk_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chunk_state(
          file_id INTEGER PRIMARY KEY,
          chunk_plan_hash TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chunk_embeddings(
          chunk_hash TEXT PRIMARY KEY,
          model TEXT NOT NULL,
          dim INTEGER NOT NULL,
          vector BLOB NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_embeddings(
          file_id INTEGER NOT NULL,
          model TEXT NOT NULL,
          dim INTEGER NOT NULL,
          vector BLOB NOT NULL,
          file_vec_hash TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(file_id, model),
          FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_file_ord ON chunks(file_id, ord);
        CREATE INDEX IF NOT EXISTS idx_chunks_chunk_hash ON chunks(chunk_hash);
        CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model ON chunk_embeddings(model);
        CREATE INDEX IF NOT EXISTS idx_file_embeddings_model ON file_embeddings(model);
        """
    )

