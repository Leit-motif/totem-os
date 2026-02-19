from pathlib import Path

from totem.daemon_ask.config import load_daemon_ask_config


def test_daemon_ask_config_auto_retrieval_defaults(monkeypatch, tmp_path):
    vault_root = tmp_path / "daemon_vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("totem.daemon_ask.config.resolve_daemon_vault_root", lambda cli_vault: vault_root)
    monkeypatch.setattr("totem.daemon_ask.config._find_repo_root", lambda cwd: Path("/tmp/fake-repo"))
    monkeypatch.setattr(
        "totem.daemon_ask.config._load_repo_config_data",
        lambda repo_root: {"daemon": {"ask": {"top_k": 7}}},
    )

    cfg = load_daemon_ask_config(cli_vault=None, cli_db_path=None)
    assert cfg.auto_retrieve_enabled is True
    assert cfg.retrieval_top_k == 7
    assert cfg.inject_n == 7
    assert cfg.sources_mode_default == "auto"


def test_daemon_ask_config_sources_mode_validation(monkeypatch, tmp_path):
    vault_root = tmp_path / "daemon_vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("totem.daemon_ask.config.resolve_daemon_vault_root", lambda cli_vault: vault_root)
    monkeypatch.setattr("totem.daemon_ask.config._find_repo_root", lambda cwd: Path("/tmp/fake-repo"))
    monkeypatch.setattr(
        "totem.daemon_ask.config._load_repo_config_data",
        lambda repo_root: {"daemon": {"ask": {"sources_mode_default": "weird"}}},
    )

    try:
        load_daemon_ask_config(cli_vault=None, cli_db_path=None)
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "sources_mode_default" in str(e)
