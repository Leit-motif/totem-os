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


@dataclass(frozen=True)
class DaemonSessionConfig:
    vault_root: Path
    sessions_db_path: Path
    last_n_queries_cap: int
    last_n_sources_cap: int


def load_daemon_session_config(
    *,
    cli_vault: Optional[str],
    cli_sessions_db_path: Optional[str] = None,
) -> DaemonSessionConfig:
    vault_root = resolve_daemon_vault_root(cli_vault)
    if not vault_root.exists():
        raise FileNotFoundError(f"Daemon vault path does not exist: {vault_root}")

    repo_root = _find_repo_root(Path.cwd())
    data = _load_repo_config_data(repo_root) or {}
    daemon_section = data.get("daemon") if isinstance(data, dict) else {}
    if not isinstance(daemon_section, dict):
        daemon_section = {}
    sess_section = daemon_section.get("sessions") if isinstance(daemon_section.get("sessions"), dict) else {}

    db_path_value = sess_section.get("sqlite", "state/daemon_sessions.sqlite")
    if cli_sessions_db_path is not None:
        db_path_value = cli_sessions_db_path

    db_path = Path(str(db_path_value)).expanduser()
    if not db_path.is_absolute():
        db_path = (vault_root / db_path).resolve()

    return DaemonSessionConfig(
        vault_root=vault_root,
        sessions_db_path=db_path,
        last_n_queries_cap=_as_int(sess_section.get("last_n_queries_cap", 20), name="[daemon.sessions].last_n_queries_cap"),
        last_n_sources_cap=_as_int(sess_section.get("last_n_sources_cap", 30), name="[daemon.sessions].last_n_sources_cap"),
    )

