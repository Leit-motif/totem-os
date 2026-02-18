from pathlib import Path

from totem.daemon_search.config import load_daemon_search_config


def test_search_vector_backend_falls_back_to_embeddings_backend(monkeypatch, tmp_path):
    vault_root = tmp_path / "daemon_vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "totem.daemon_search.config.resolve_daemon_vault_root",
        lambda cli_vault: vault_root,
    )
    monkeypatch.setattr(
        "totem.daemon_search.config._find_repo_root",
        lambda cwd: Path("/tmp/fake-repo"),
    )
    monkeypatch.setattr(
        "totem.daemon_search.config._load_repo_config_data",
        lambda repo_root: {
            "daemon": {
                "embeddings_backend": "openai",
                "embeddings_model": "text-embedding-3-small",
                "embeddings_dim": 1536,
            }
        },
    )

    cfg = load_daemon_search_config(cli_vault=None, cli_db_path=None)

    assert cfg.vector_backend == "openai"
    assert cfg.model == "text-embedding-3-small"
    assert cfg.dim == 1536


def test_search_vector_backend_override_takes_precedence(monkeypatch, tmp_path):
    vault_root = tmp_path / "daemon_vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "totem.daemon_search.config.resolve_daemon_vault_root",
        lambda cli_vault: vault_root,
    )
    monkeypatch.setattr(
        "totem.daemon_search.config._find_repo_root",
        lambda cwd: Path("/tmp/fake-repo"),
    )
    monkeypatch.setattr(
        "totem.daemon_search.config._load_repo_config_data",
        lambda repo_root: {
            "daemon": {
                "embeddings_backend": "openai",
                "search_vector_backend": "sqlite",
                "embeddings_model": "text-embedding-3-small",
                "embeddings_dim": 16,
            }
        },
    )

    cfg = load_daemon_search_config(cli_vault=None, cli_db_path=None)

    assert cfg.vector_backend == "sqlite"
