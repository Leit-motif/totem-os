from pathlib import Path

from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_embed.models import ChunkingConfig, DaemonEmbedConfig, EmbeddingsConfig
from totem.daemon_embed.orchestrator import embed_daemon_vault
from totem.daemon_search.config import DaemonSearchConfig
from totem.daemon_search.db import connect
from totem.daemon_search.engine import search_daemon
from totem.daemon_search.fts import rebuild_chunk_fts
from totem.daemon_search.models import SearchFilters


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


def _search_cfg(index_cfg: DaemonIndexConfig) -> DaemonSearchConfig:
    return DaemonSearchConfig(
        vault_root=index_cfg.vault_root,
        db_path=index_cfg.db_path,
        hybrid_weight_lex=1.0,
        hybrid_weight_vec=0.0,
        prefer_recent_half_life_days=30,
        prefer_recent_weight=0.0,
        excerpt_max_chars=120,
        context_before_chars=30,
        context_after_chars=90,
        top_k_default=10,
        expand_links_default=0,
        expand_links_cap=2,
        vector_backend="sqlite",
        model="m1",
        dim=16,
    )


def test_expand_links_appends_and_caps(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "A.md").write_text("# A\nneedle [[B]]\n", encoding="utf-8")
    (idx_cfg.vault_root / "B.md").write_text("# B\ncontext\n", encoding="utf-8")
    (idx_cfg.vault_root / "C.md").write_text("# C\n[[A]]\n", encoding="utf-8")  # backlink to A via title match

    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg))
    conn = connect(idx_cfg.db_path)
    try:
        rebuild_chunk_fts(conn, vault_root=idx_cfg.vault_root, full=True)
    finally:
        conn.close()

    cfg = _search_cfg(idx_cfg)
    hits = search_daemon(
        cfg,
        query="needle",
        top_k=1,
        prefer_recent=False,
        filters=SearchFilters(tags=[], tag_or=False, date_from=None, date_to=None),
        expand_links=1,
    )
    assert len(hits) >= 1
    assert hits[0].expanded_context is False
    expanded = [h for h in hits[1:] if h.expanded_context]
    assert len(expanded) <= 2

