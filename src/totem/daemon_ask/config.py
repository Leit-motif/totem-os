from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from totem.config import _find_repo_root, _load_repo_config_data
from totem.daemon_index.config import resolve_daemon_vault_root


def _as_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Invalid config: {name} must be an int")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"Invalid config: {name} must be an int")


def _as_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"Invalid config: {name} must be a bool")


def _as_str(value: Any, *, name: str) -> str:
    if value is None:
        raise ValueError(f"Invalid config: {name} must be a string")
    if isinstance(value, str):
        return value
    return str(value)


@dataclass(frozen=True)
class DaemonAskConfig:
    vault_root: Path
    db_path: Path

    # Retrieval/packing budgets
    top_k: int
    per_file_cap: int
    packed_max_chars: int

    # Tracing / formatting
    traces_dir_rel: str
    include_why: bool
    # Graph expansion is added in Phase 3A / Milestone 2.


def load_daemon_ask_config(
    *,
    cli_vault: Optional[str],
    cli_db_path: Optional[str],
) -> DaemonAskConfig:
    vault_root = resolve_daemon_vault_root(cli_vault)
    if not vault_root.exists():
        raise FileNotFoundError(f"Daemon vault path does not exist: {vault_root}")

    repo_root = _find_repo_root(Path.cwd())
    data = _load_repo_config_data(repo_root) or {}
    daemon_section = data.get("daemon") if isinstance(data, dict) else {}
    if not isinstance(daemon_section, dict):
        daemon_section = {}
    ask_section = daemon_section.get("ask") if isinstance(daemon_section.get("ask"), dict) else {}
    # Temporal reasoning config is added in Phase 3B / Milestone 6.

    db_path_value = daemon_section.get("daemon_index_sqlite", "state/daemon_index.sqlite")
    if cli_db_path is not None:
        db_path_value = cli_db_path

    db_path = Path(str(db_path_value)).expanduser()
    if not db_path.is_absolute():
        db_path = (vault_root / db_path).resolve()

    return DaemonAskConfig(
        vault_root=vault_root,
        db_path=db_path,
        top_k=_as_int(ask_section.get("top_k", 10), name="[daemon.ask].top_k"),
        per_file_cap=_as_int(ask_section.get("per_file_cap", 3), name="[daemon.ask].per_file_cap"),
        packed_max_chars=_as_int(ask_section.get("packed_max_chars", 8000), name="[daemon.ask].packed_max_chars"),
        traces_dir_rel=str(ask_section.get("traces_dir_rel", "90_system/traces/daemon_ask")),
        include_why=_as_bool(ask_section.get("include_why", True), name="[daemon.ask].include_why"),
    )
