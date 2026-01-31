from pathlib import Path

from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_embed.models import ChunkingConfig, DaemonEmbedConfig, EmbeddingsConfig
from totem.daemon_embed.orchestrator import embed_daemon_vault
from totem.daemon_search.db import connect
from totem.daemon_search.vector import embed_query, vector_search


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


def test_vector_search_is_deterministic(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "a.md").write_text("# A\nalpha\n", encoding="utf-8")
    (idx_cfg.vault_root / "b.md").write_text("# B\nbeta\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg, model="m1", dim=16))

    conn = connect(idx_cfg.db_path)
    try:
        q = embed_query("alpha", dim=16)
        r1 = vector_search(conn, query_vector=q, model="m1", dim=16, top_k=5, allowed_chunk_hashes=None)
        r2 = vector_search(conn, query_vector=q, model="m1", dim=16, top_k=5, allowed_chunk_hashes=None)
        assert r1 == r2
        assert len(r1) > 0
    finally:
        conn.close()

