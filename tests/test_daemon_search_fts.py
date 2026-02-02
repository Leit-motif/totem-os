import sqlite3
from pathlib import Path

from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_embed.models import ChunkingConfig, DaemonEmbedConfig, EmbeddingsConfig
from totem.daemon_embed.orchestrator import embed_daemon_vault
from totem.daemon_search.db import connect
from totem.daemon_search.fts import rebuild_chunk_fts


def _index_cfg(tmp_path: Path) -> DaemonIndexConfig:
    vault_root = tmp_path / "daemon_vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    db_path = vault_root / "state" / "daemon_index.sqlite"
    return DaemonIndexConfig(
        vault_root=vault_root,
        db_path=db_path,
        exclude_globs=["state/**", ".git/**"],
        frontmatter_journal_date_key="date",
        frontmatter_journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"],
    )


def _embed_cfg(index_cfg: DaemonIndexConfig, *, model: str = "m1", dim: int = 16) -> DaemonEmbedConfig:
    return DaemonEmbedConfig(
        vault_root=index_cfg.vault_root,
        db_path=index_cfg.db_path,
        chunking=ChunkingConfig(min_bytes=0, max_bytes=4000, split_strategy="paragraph_then_window", include_preamble=False),
        embeddings=EmbeddingsConfig(backend="sqlite", model=model, dim=dim),
    )


def test_fts_rebuild_and_lookup_is_deterministic(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "a.md").write_text("# A\nalpha zebra\n", encoding="utf-8")
    (idx_cfg.vault_root / "b.md").write_text("# B\nalpha yak\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg))

    conn = connect(idx_cfg.db_path)
    try:
        rebuild_chunk_fts(conn, vault_root=idx_cfg.vault_root, full=True)
        rows1 = conn.execute(
            """
            SELECT rel_path, chunk_id
            FROM chunk_fts
            WHERE chunk_fts MATCH ?
            ORDER BY rel_path ASC, chunk_id ASC
            """,
            ("alpha",),
        ).fetchall()
        rebuild_chunk_fts(conn, vault_root=idx_cfg.vault_root, full=False)
        rows2 = conn.execute(
            """
            SELECT rel_path, chunk_id
            FROM chunk_fts
            WHERE chunk_fts MATCH ?
            ORDER BY rel_path ASC, chunk_id ASC
            """,
            ("alpha",),
        ).fetchall()
        assert [(r["rel_path"], r["chunk_id"]) for r in rows1] == [(r["rel_path"], r["chunk_id"]) for r in rows2]
        assert {r["rel_path"] for r in rows1} == {"a.md", "b.md"}
    finally:
        conn.close()


def test_fts_query_sanitizes_punctuation(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "a.md").write_text("# A\nNietzsche\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg))

    conn = connect(idx_cfg.db_path)
    try:
        rebuild_chunk_fts(conn, vault_root=idx_cfg.vault_root, full=True)
        from totem.daemon_search.fts import fts_query

        rows = fts_query(conn, query="Nietzsche?", limit=10, allowed_file_ids=None, date_from=None, date_to=None)
        assert {r["rel_path"] for r in rows} == {"a.md"}
    finally:
        conn.close()
