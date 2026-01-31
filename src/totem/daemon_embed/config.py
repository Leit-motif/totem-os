from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from totem.config import _find_repo_root, _load_repo_config_data
from totem.daemon_index.config import resolve_daemon_vault_root

from .models import ChunkingConfig, DaemonEmbedConfig, EmbeddingsConfig


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
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"Invalid config: {name} must be a bool")


def load_daemon_embed_config(
    *,
    cli_vault: Optional[str],
    cli_db_path: Optional[str],
) -> DaemonEmbedConfig:
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

    min_bytes = _as_int(daemon_section.get("chunks_min_bytes", 400), name="[daemon].chunks_min_bytes")
    max_bytes = _as_int(daemon_section.get("chunks_max_bytes", 4000), name="[daemon].chunks_max_bytes")
    if max_bytes <= 0 or min_bytes < 0 or max_bytes < 1:
        raise ValueError("Invalid chunk sizes")
    split_strategy = str(daemon_section.get("chunks_split_strategy", "paragraph_then_window"))
    include_preamble = _as_bool(daemon_section.get("chunks_include_preamble", False), name="[daemon].chunks_include_preamble")

    backend = str(daemon_section.get("embeddings_backend", "sqlite"))
    model = str(daemon_section.get("embeddings_model", "dummy-sha256"))
    dim = _as_int(daemon_section.get("embeddings_dim", 0), name="[daemon].embeddings_dim")
    if dim <= 0:
        raise ValueError("Invalid config: [daemon].embeddings_dim must be > 0")

    return DaemonEmbedConfig(
        vault_root=vault_root,
        db_path=db_path,
        chunking=ChunkingConfig(
            min_bytes=min_bytes,
            max_bytes=max_bytes,
            split_strategy=split_strategy,
            include_preamble=include_preamble,
        ),
        embeddings=EmbeddingsConfig(backend=backend, model=model, dim=dim),
    )

