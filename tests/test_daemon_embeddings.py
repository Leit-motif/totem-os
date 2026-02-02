import sqlite3
from pathlib import Path

from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_embed.models import ChunkingConfig, DaemonEmbedConfig, EmbeddingsConfig
from totem.daemon_embed.orchestrator import embed_daemon_vault


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


def _embed_cfg(index_cfg: DaemonIndexConfig, *, model: str, dim: int) -> DaemonEmbedConfig:
    return DaemonEmbedConfig(
        vault_root=index_cfg.vault_root,
        db_path=index_cfg.db_path,
        chunking=ChunkingConfig(min_bytes=0, max_bytes=4000, split_strategy="paragraph_then_window", include_preamble=False),
        embeddings=EmbeddingsConfig(backend="sqlite", model=model, dim=dim),
    )


def _q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_incremental_rerun_no_changes_no_new_embeddings(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    note = idx_cfg.vault_root / "a.md"
    note.write_text("# A\nPara\n\nSee [[X]]\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)

    cfg = _embed_cfg(idx_cfg, model="m1", dim=32)
    s1 = embed_daemon_vault(cfg)
    assert s1.chunks_embedded > 0
    emb_count_1 = _q(idx_cfg.db_path, "SELECT COUNT(*) AS c FROM chunk_embeddings WHERE model = 'm1'")[0]["c"]

    s2 = embed_daemon_vault(cfg)
    emb_count_2 = _q(idx_cfg.db_path, "SELECT COUNT(*) AS c FROM chunk_embeddings WHERE model = 'm1'")[0]["c"]
    assert emb_count_2 == emb_count_1
    assert s2.chunks_embedded == 0


def test_content_change_updates_chunks_and_file_vector(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    note = idx_cfg.vault_root / "a.md"
    note.write_text("# A\nOne\n\nTwo\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)

    cfg = _embed_cfg(idx_cfg, model="m1", dim=32)
    embed_daemon_vault(cfg)
    file_vec_1 = _q(idx_cfg.db_path, "SELECT file_vec_hash FROM file_embeddings WHERE model = 'm1'")[0]["file_vec_hash"]
    chunk_count_1 = _q(idx_cfg.db_path, "SELECT COUNT(*) AS c FROM chunks")[0]["c"]

    note.write_text("# A\nOne\n\nTwo changed\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)
    embed_daemon_vault(cfg)

    file_vec_2 = _q(idx_cfg.db_path, "SELECT file_vec_hash FROM file_embeddings WHERE model = 'm1'")[0]["file_vec_hash"]
    chunk_count_2 = _q(idx_cfg.db_path, "SELECT COUNT(*) AS c FROM chunks")[0]["c"]
    assert chunk_count_2 == chunk_count_1  # same structure, different text
    assert file_vec_2 != file_vec_1


def test_model_change_creates_new_embeddings_without_overwriting(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    note = idx_cfg.vault_root / "a.md"
    note.write_text("# A\nOne\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)

    cfg1 = _embed_cfg(idx_cfg, model="m1", dim=16)
    cfg2 = _embed_cfg(idx_cfg, model="m2", dim=16)

    embed_daemon_vault(cfg1)
    embed_daemon_vault(cfg2)

    n1 = _q(idx_cfg.db_path, "SELECT COUNT(*) AS c FROM chunk_embeddings WHERE model = 'm1'")[0]["c"]
    n2 = _q(idx_cfg.db_path, "SELECT COUNT(*) AS c FROM chunk_embeddings WHERE model = 'm2'")[0]["c"]
    assert n1 > 0 and n2 > 0

    file_models = _q(idx_cfg.db_path, "SELECT model FROM file_embeddings ORDER BY model")
    assert [r["model"] for r in file_models] == ["m1", "m2"]

