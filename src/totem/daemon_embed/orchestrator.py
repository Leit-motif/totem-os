from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import db as schema_db
from .chunking import (
    compute_chunk_plan_hash,
    compute_headings_signature,
    load_headings_for_file,
    plan_chunks_for_file,
)
from .embedder import DeterministicSha256Embedder, mean_float32_le
from .models import DaemonEmbedConfig, DaemonEmbedSummary, PlannedChunk, VectorRecord
from .vectorstore import SqliteVectorStore


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_hex_str(s: str) -> str:
    h = hashlib.sha256()
    h.update(s.encode("utf-8"))
    return h.hexdigest()


def embed_daemon_vault(
    cfg: DaemonEmbedConfig,
    *,
    full: bool = False,
    limit: Optional[int] = None,
) -> DaemonEmbedSummary:
    conn = sqlite3.connect(str(cfg.db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        schema_db.ensure_schema(conn)

        # Ensure Milestone 1 tables exist (source of truth).
        try:
            conn.execute("SELECT 1 FROM files LIMIT 1").fetchone()
        except sqlite3.OperationalError as e:
            raise RuntimeError("Index DB missing Milestone 1 schema. Run `totem daemon index` first.") from e

        if full:
            with conn:
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM chunk_state")
                conn.execute("DELETE FROM file_embeddings WHERE model = ?", (cfg.embeddings.model,))

        files = conn.execute(
            "SELECT id, rel_path, content_hash, size_bytes FROM files ORDER BY rel_path ASC, id ASC"
        ).fetchall()

        files_considered = 0
        files_rechunked = 0
        chunks_upserted = 0
        chunks_embedded = 0
        files_embedded = 0

        # Determine rechunk set.
        rechunk_file_ids: list[int] = []
        chunk_plan_by_file: dict[int, str] = {}
        planned_chunks_by_file: dict[int, list[PlannedChunk]] = {}

        for f in files:
            files_considered += 1
            file_id = int(f["id"])
            rel_path = str(f["rel_path"])
            content_hash = str(f["content_hash"])
            size_bytes = int(f["size_bytes"])

            headings = load_headings_for_file(conn, file_id)
            headings_sig = compute_headings_signature(headings)
            plan_hash = compute_chunk_plan_hash(
                file_content_hash=content_hash,
                headings_signature=headings_sig,
                chunking=cfg.chunking,
                embeddings_model=cfg.embeddings.model,
                embeddings_dim=cfg.embeddings.dim,
            )
            chunk_plan_by_file[file_id] = plan_hash

            state_row = conn.execute("SELECT chunk_plan_hash FROM chunk_state WHERE file_id = ?", (file_id,)).fetchone()
            if state_row is not None and str(state_row["chunk_plan_hash"]) == plan_hash and not full:
                continue

            planned = plan_chunks_for_file(
                vault_root=cfg.vault_root,
                rel_path=rel_path,
                file_id=file_id,
                file_size_bytes=size_bytes,
                headings=headings,
                chunking=cfg.chunking,
                embeddings_model=cfg.embeddings.model,
            )
            planned_chunks_by_file[file_id] = planned
            rechunk_file_ids.append(file_id)

        # Upsert chunks for rechunked files.
        if rechunk_file_ids:
            now = _now_iso_utc()
            with conn:
                for file_id in rechunk_file_ids:
                    conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                    planned = planned_chunks_by_file[file_id]
                    conn.executemany(
                        """
                        INSERT INTO chunks(
                          file_id, heading_path, heading_id, ord, start_byte, end_byte,
                          text_hash, chunk_id, chunk_hash, created_at, updated_at
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                c.file_id,
                                c.heading_path,
                                c.heading_id,
                                c.ord,
                                c.start_byte,
                                c.end_byte,
                                c.text_hash,
                                c.chunk_id,
                                c.chunk_hash,
                                now,
                                now,
                            )
                            for c in planned
                        ],
                    )
                    conn.execute(
                        """
                        INSERT INTO chunk_state(file_id, chunk_plan_hash, updated_at)
                        VALUES(?, ?, ?)
                        ON CONFLICT(file_id) DO UPDATE SET
                          chunk_plan_hash=excluded.chunk_plan_hash,
                          updated_at=excluded.updated_at
                        """,
                        (file_id, chunk_plan_by_file[file_id], now),
                    )
                    chunks_upserted += len(planned)
                    files_rechunked += 1

        # Determine missing embeddings, deterministic order: rel_path ASC, chunk.ord ASC.
        rows = conn.execute(
            """
            SELECT c.chunk_hash, f.rel_path, c.file_id, c.start_byte, c.end_byte, c.ord
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            WHERE c.chunk_hash IS NOT NULL
            ORDER BY f.rel_path ASC, c.ord ASC, c.start_byte ASC
            """
        ).fetchall()
        all_chunk_hashes = [str(r["chunk_hash"]) for r in rows]

        store = SqliteVectorStore(conn)
        existing = store.get_existing_chunk_hashes(all_chunk_hashes, model=cfg.embeddings.model, dim=cfg.embeddings.dim)

        missing_rows = [r for r in rows if str(r["chunk_hash"]) not in existing]
        if limit is not None:
            missing_rows = missing_rows[: max(0, int(limit))]

        embedder = DeterministicSha256Embedder(cfg.embeddings.dim)

        vectors_to_upsert: list[VectorRecord] = []
        for r in missing_rows:
            file_id = int(r["file_id"])
            rel_path = str(r["rel_path"])
            start_byte = int(r["start_byte"])
            end_byte = int(r["end_byte"])
            chunk_hash = str(r["chunk_hash"])

            data = (cfg.vault_root / rel_path).read_bytes()
            chunk_bytes = data[start_byte:end_byte]
            text = chunk_bytes.decode("utf-8", errors="strict")
            vector = embedder.embed_text(text)
            vectors_to_upsert.append(
                VectorRecord(
                    chunk_hash=chunk_hash,
                    model=cfg.embeddings.model,
                    dim=cfg.embeddings.dim,
                    vector=vector,
                )
            )

        if vectors_to_upsert:
            with conn:
                store.upsert(vectors_to_upsert)
            chunks_embedded += len(vectors_to_upsert)

        # Update file-level vectors for files whose chunk list is complete in the embedding table.
        # Locked policy: weighted mean by chunk byte length.
        affected_file_ids = set(rechunk_file_ids)
        affected_file_ids.update({int(r["file_id"]) for r in missing_rows})

        if affected_file_ids:
            now = _now_iso_utc()
            for file_id in sorted(affected_file_ids):
                chunk_rows = conn.execute(
                    """
                    SELECT ord, chunk_hash, (end_byte - start_byte) AS byte_len
                    FROM chunks
                    WHERE file_id = ?
                    ORDER BY ord ASC
                    """,
                    (file_id,),
                ).fetchall()
                if not chunk_rows:
                    continue
                chunk_hashes = [str(r["chunk_hash"]) for r in chunk_rows]
                file_vec_hash = _sha256_hex_str(cfg.embeddings.model + ":" + "|".join(chunk_hashes))

                existing_row = conn.execute(
                    "SELECT file_vec_hash FROM file_embeddings WHERE file_id = ? AND model = ?",
                    (file_id, cfg.embeddings.model),
                ).fetchone()
                if existing_row is not None and str(existing_row["file_vec_hash"]) == file_vec_hash:
                    continue

                existing_hashes = store.get_existing_chunk_hashes(
                    chunk_hashes, model=cfg.embeddings.model, dim=cfg.embeddings.dim
                )
                if len(existing_hashes) != len(chunk_hashes):
                    # Not all chunk embeddings are present yet (likely due to --limit); skip.
                    continue

                qmarks = ",".join(["?"] * len(chunk_hashes))
                vec_rows = conn.execute(
                    f"SELECT chunk_hash, vector FROM chunk_embeddings WHERE model = ? AND dim = ? AND chunk_hash IN ({qmarks})",
                    (cfg.embeddings.model, cfg.embeddings.dim, *chunk_hashes),
                ).fetchall()
                vec_by_hash = {str(r["chunk_hash"]): bytes(r["vector"]) for r in vec_rows}
                vectors = [vec_by_hash[h] for h in chunk_hashes]
                weights = [float(int(r["byte_len"])) for r in chunk_rows]
                file_vec = mean_float32_le(vectors, dim=cfg.embeddings.dim, weights=weights)

                with conn:
                    conn.execute(
                        """
                        INSERT INTO file_embeddings(file_id, model, dim, vector, file_vec_hash, updated_at)
                        VALUES(?, ?, ?, ?, ?, ?)
                        ON CONFLICT(file_id, model) DO UPDATE SET
                          dim=excluded.dim,
                          vector=excluded.vector,
                          file_vec_hash=excluded.file_vec_hash,
                          updated_at=excluded.updated_at
                        """,
                        (file_id, cfg.embeddings.model, cfg.embeddings.dim, file_vec, file_vec_hash, now),
                    )
                files_embedded += 1

        dangling_deleted = 0
        with conn:
            dangling_deleted = store.delete_dangling_embeddings(model=cfg.embeddings.model, dim=cfg.embeddings.dim)

        return DaemonEmbedSummary(
            files_considered=files_considered,
            files_rechunked=files_rechunked,
            chunks_upserted=chunks_upserted,
            chunks_embedded=chunks_embedded,
            files_embedded=files_embedded,
            dangling_embeddings_deleted=dangling_deleted,
        )
    finally:
        conn.close()
