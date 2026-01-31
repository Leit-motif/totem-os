from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from totem.config import _find_repo_root, _load_repo_config_data
from totem.daemon_index.config import resolve_daemon_vault_root

from .models import DaemonSearchConfig


def _as_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Invalid config: {name} must be an int")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"Invalid config: {name} must be an int")


def _as_float(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid config: {name} must be a float")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise ValueError(f"Invalid config: {name} must be a float")


def load_daemon_search_config(
    *,
    cli_vault: Optional[str],
    cli_db_path: Optional[str],
) -> DaemonSearchConfig:
    vault_root = resolve_daemon_vault_root(cli_vault)
    if not vault_root.exists():
        raise FileNotFoundError(f"Daemon vault path does not exist: {vault_root}")

    repo_root = _find_repo_root(Path.cwd())
    data = _load_repo_config_data(repo_root) or {}
    daemon_section = data.get("daemon") if isinstance(data, dict) else {}
    if not isinstance(daemon_section, dict):
        daemon_section = {}

    db_path_value = daemon_section.get("daemon_index_sqlite", "state/daemon_index.sqlite")
    if cli_db_path is not None:
        db_path_value = cli_db_path

    db_path = Path(str(db_path_value)).expanduser()
    if not db_path.is_absolute():
        db_path = (vault_root / db_path).resolve()

    model = str(daemon_section.get("search_model", daemon_section.get("embeddings_model", "dummy-sha256")))
    dim = _as_int(daemon_section.get("embeddings_dim", 0), name="[daemon].embeddings_dim")
    if dim <= 0:
        raise ValueError("Invalid config: [daemon].embeddings_dim must be > 0")

    return DaemonSearchConfig(
        vault_root=vault_root,
        db_path=db_path,
        top_k_default=_as_int(daemon_section.get("search_top_k_default", 10), name="[daemon].search_top_k_default"),
        excerpt_max_chars=_as_int(
            daemon_section.get("search_excerpt_max_chars", 400),
            name="[daemon].search_excerpt_max_chars",
        ),
        context_before_chars=_as_int(
            daemon_section.get("search_context_before_chars", 80),
            name="[daemon].search_context_before_chars",
        ),
        context_after_chars=_as_int(
            daemon_section.get("search_context_after_chars", 320),
            name="[daemon].search_context_after_chars",
        ),
        prefer_recent_half_life_days=_as_float(
            daemon_section.get("search_prefer_recent_half_life_days", 30),
            name="[daemon].search_prefer_recent_half_life_days",
        ),
        prefer_recent_weight=_as_float(
            daemon_section.get("search_prefer_recent_weight", 0.15),
            name="[daemon].search_prefer_recent_weight",
        ),
        hybrid_weight_lex=_as_float(
            daemon_section.get("search_hybrid_weight_lex", 0.5),
            name="[daemon].search_hybrid_weight_lex",
        ),
        hybrid_weight_vec=_as_float(
            daemon_section.get("search_hybrid_weight_vec", 0.5),
            name="[daemon].search_hybrid_weight_vec",
        ),
        expand_links_default=_as_int(
            daemon_section.get("search_expand_links_default", 0),
            name="[daemon].search_expand_links_default",
        ),
        expand_links_cap=_as_int(
            daemon_section.get("search_expand_links_cap", 10),
            name="[daemon].search_expand_links_cap",
        ),
        vector_backend=str(daemon_section.get("search_vector_backend", "sqlite")),
        model=model,
        dim=dim,
    )

