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
        hybrid_weight_lex=0.5,
        hybrid_weight_vec=0.5,
        prefer_recent_half_life_days=30,
        prefer_recent_weight=0.15,
        excerpt_max_chars=200,
        context_before_chars=40,
        context_after_chars=160,
        top_k_default=10,
        expand_links_default=0,
        expand_links_cap=10,
        vector_backend="sqlite",
        model="m1",
        dim=16,
    )


def test_tag_filter_and_or_semantics(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "a.md").write_text("---\ntags: [alpha]\n---\n# A\nneedle\n", encoding="utf-8")
    (idx_cfg.vault_root / "b.md").write_text("---\ntags: [beta]\n---\n# B\nneedle\n", encoding="utf-8")
    (idx_cfg.vault_root / "c.md").write_text("---\ntags: [alpha, beta]\n---\n# C\nneedle\n", encoding="utf-8")
    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg))

    conn = connect(idx_cfg.db_path)
    try:
        rebuild_chunk_fts(conn, vault_root=idx_cfg.vault_root, full=True)
    finally:
        conn.close()

    cfg = _search_cfg(idx_cfg)
    # AND: must have both -> only c.md
    hits_and = search_daemon(
        cfg,
        query="needle",
        top_k=10,
        prefer_recent=False,
        filters=SearchFilters(tags=["alpha", "beta"], tag_or=False, date_from=None, date_to=None),
        expand_links=0,
    )
    assert {h.rel_path for h in hits_and} == {"c.md"}

    # OR: any tag -> a,b,c
    hits_or = search_daemon(
        cfg,
        query="needle",
        top_k=10,
        prefer_recent=False,
        filters=SearchFilters(tags=["alpha", "beta"], tag_or=True, date_from=None, date_to=None),
        expand_links=0,
    )
    assert {h.rel_path for h in hits_or} == {"a.md", "b.md", "c.md"}


def test_date_filter_uses_frontmatter_over_mtime(tmp_path: Path):
    idx_cfg = _index_cfg(tmp_path)
    a = idx_cfg.vault_root / "a.md"
    b = idx_cfg.vault_root / "b.md"
    a.write_text("---\ndate: 2026-01-01\n---\n# A\nneedle\n", encoding="utf-8")
    b.write_text("# B\nneedle\n", encoding="utf-8")
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
        top_k=10,
        prefer_recent=False,
        filters=SearchFilters(tags=[], tag_or=False, date_from="2026-01-01", date_to="2026-01-01"),
        expand_links=0,
    )
    assert {h.rel_path for h in hits} == {"a.md"}

