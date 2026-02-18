from pathlib import Path

from totem.daemon_embed.models import ChunkingConfig, DaemonEmbedConfig, EmbeddingsConfig
from totem.daemon_embed.orchestrator import embed_daemon_vault
from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_search.config import load_daemon_search_config
from totem.daemon_search.models import SearchFilters
from totem.daemon_search.retrieval import format_sources, retrieve_context


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
        chunking=ChunkingConfig(
            min_bytes=0,
            max_bytes=4000,
            split_strategy="paragraph_then_window",
            include_preamble=False,
        ),
        embeddings=EmbeddingsConfig(backend="sqlite", model=model, dim=dim),
    )


def _search_cfg_from_index(index_cfg: DaemonIndexConfig, monkeypatch) -> object:
    monkeypatch.setattr(
        "totem.daemon_search.config.resolve_daemon_vault_root",
        lambda cli_vault: index_cfg.vault_root,
    )
    monkeypatch.setattr(
        "totem.daemon_search.config._find_repo_root",
        lambda cwd: Path("/tmp/fake-repo"),
    )
    monkeypatch.setattr(
        "totem.daemon_search.config._load_repo_config_data",
        lambda repo_root: {
            "daemon": {
                "daemon_index_sqlite": str(index_cfg.db_path),
                "embeddings_backend": "sqlite",
                "embeddings_model": "m1",
                "embeddings_dim": 16,
            }
        },
    )
    return load_daemon_search_config(cli_vault=None, cli_db_path=None)


def test_retrieve_context_returns_snippets_with_citations(tmp_path: Path, monkeypatch):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "a.md").write_text("# A\nalpha signal\n", encoding="utf-8")
    (idx_cfg.vault_root / "b.md").write_text("# B\nbeta signal\n", encoding="utf-8")

    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg))

    search_cfg = _search_cfg_from_index(idx_cfg, monkeypatch)
    result = retrieve_context(
        search_cfg,
        query="alpha",
        top_k=3,
        filters=SearchFilters(tags=[], tag_or=False, date_from=None, date_to=None),
        prefer_recent=False,
        expand_links=0,
    )

    assert result.query == "alpha"
    assert len(result.snippets) > 0
    s0 = result.snippets[0]
    assert s0.rel_path in {"a.md", "b.md"}
    assert ":" in s0.citation
    assert s0.start_byte < s0.end_byte


def test_format_sources_is_bounded(tmp_path: Path, monkeypatch):
    idx_cfg = _index_cfg(tmp_path)
    (idx_cfg.vault_root / "a.md").write_text("# A\nalpha\n", encoding="utf-8")
    (idx_cfg.vault_root / "b.md").write_text("# B\nalpha\n", encoding="utf-8")
    (idx_cfg.vault_root / "c.md").write_text("# C\nalpha\n", encoding="utf-8")

    index_daemon_vault(idx_cfg)
    embed_daemon_vault(_embed_cfg(idx_cfg))

    search_cfg = _search_cfg_from_index(idx_cfg, monkeypatch)
    result = retrieve_context(
        search_cfg,
        query="alpha",
        top_k=5,
        filters=SearchFilters(tags=[], tag_or=False, date_from=None, date_to=None),
        prefer_recent=False,
        expand_links=0,
    )

    out = format_sources(result, max_items=2)
    assert out.startswith("Sources:\n")
    assert out.count("\n-") == 2
