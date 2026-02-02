from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from totem.config import _find_repo_root, _load_repo_config_data

from .models import DaemonIndexConfig


def _nested_get(data: Optional[dict[str, Any]], path: list[str]) -> Any:
    cur: Any = data or {}
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def resolve_daemon_vault_root(cli_vault: Optional[str]) -> Path:
    if cli_vault:
        return Path(cli_vault).expanduser().resolve()

    repo_root = _find_repo_root(Path.cwd())
    data = _load_repo_config_data(repo_root)
    daemon_path = _nested_get(data, ["obsidian", "vaults", "daemon_path"])
    if not isinstance(daemon_path, str) or not daemon_path.strip():
        raise FileNotFoundError(
            "Daemon vault path not configured. Provide --vault or set "
            ".totem/config.toml [obsidian.vaults].daemon_path."
        )
    return Path(daemon_path).expanduser().resolve()


def load_daemon_index_config(
    *,
    cli_vault: Optional[str],
    cli_db_path: Optional[str],
) -> DaemonIndexConfig:
    vault_root = resolve_daemon_vault_root(cli_vault)
    if not vault_root.exists():
        raise FileNotFoundError(f"Daemon vault path does not exist: {vault_root}")
    if not vault_root.is_dir():
        raise FileNotFoundError(f"Daemon vault path is not a directory: {vault_root}")

    repo_root = _find_repo_root(Path.cwd())
    data = _load_repo_config_data(repo_root) or {}

    daemon_section = data.get("daemon") if isinstance(data, dict) else {}
    if not isinstance(daemon_section, dict):
        daemon_section = {}

    db_path_value = daemon_section.get("daemon_index_sqlite", "state/daemon_index.sqlite")
    exclude_globs = daemon_section.get("exclude_globs", ["state/**", ".git/**"])
    fm_key = daemon_section.get("frontmatter_journal_date_key", "date")
    fm_formats = daemon_section.get("frontmatter_journal_date_formats", ["%Y-%m-%d", "%m-%d-%Y"])

    if cli_db_path is not None:
        db_path_value = cli_db_path

    db_path = Path(db_path_value).expanduser()
    if not db_path.is_absolute():
        db_path = (vault_root / db_path).resolve()

    if not isinstance(exclude_globs, list) or not all(isinstance(x, str) for x in exclude_globs):
        raise ValueError("Invalid config: [daemon].exclude_globs must be a list of strings")
    if not isinstance(fm_key, str) or not fm_key.strip():
        raise ValueError("Invalid config: [daemon].frontmatter_journal_date_key must be a non-empty string")
    if not isinstance(fm_formats, list) or not all(isinstance(x, str) for x in fm_formats):
        raise ValueError("Invalid config: [daemon].frontmatter_journal_date_formats must be a list of strings")

    return DaemonIndexConfig(
        vault_root=vault_root,
        db_path=db_path,
        exclude_globs=exclude_globs,
        frontmatter_journal_date_key=fm_key,
        frontmatter_journal_date_formats=fm_formats,
    )

