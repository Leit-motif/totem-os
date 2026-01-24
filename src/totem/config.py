"""Configuration management for Totem OS."""

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Python < 3.11


def _find_repo_root(start_dir: Path) -> Path:
    """Find repository root by walking upward looking for .git or pyproject.toml."""
    current_dir = start_dir

    while True:
        # Check for common repo indicators
        if (current_dir / ".git").exists() or (current_dir / "pyproject.toml").exists():
            return current_dir

        # Move up one directory
        parent_dir = current_dir.parent

        # Stop if we reach filesystem root
        if parent_dir == current_dir:
            # No repo found, return original directory
            return start_dir

        current_dir = parent_dir


def _load_repo_config_data(repo_root: Path) -> Optional[dict]:
    """Load repo config data from .totem/config.toml if it exists."""
    config_file = repo_root / ".totem" / "config.toml"

    if not config_file.exists():
        return None

    try:
        with open(config_file, "rb") as f:
            return tomllib.load(f)
    except Exception:
        # If config file is malformed, ignore it
        return None


def _load_repo_config(repo_root: Path) -> Optional[Path]:
    """Load vault_root from .totem/config.toml if it exists."""
    data = _load_repo_config_data(repo_root)
    if not data:
        return None
    vault_root_str = data.get("vault_root")
    if vault_root_str:
        return Path(vault_root_str).resolve()
    return None


def _get_repo_config_value(data: Optional[dict], keys: list[str]) -> Optional[str]:
    """Safely get a nested repo config value."""
    if not data:
        return None
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, str):
        return current
    return None


def _has_vault_markers(vault_path: Path) -> bool:
    """Check if a directory contains Totem vault markers."""
    system_dir = vault_path / "90_system"
    config_file = system_dir / "config.yaml"
    return config_file.exists()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_vault_root(
    mode: Literal["use_existing", "create_ok"],
    cli_vault_path: Optional[str] = None
) -> Path:
    """Resolve vault root path with the following precedence:

    1. CLI --vault option (if provided)
    2. repo-local .totem/config.toml (walk upward from CWD)
    3. TOTEM_VAULT environment variable
    4. Auto-discovery by walking up from cwd looking for vault indicators
    5. Error with helpful message

    Args:
        mode: "use_existing" requires vault markers to exist, "create_ok" allows new vaults
        cli_vault_path: Vault path from CLI --vault option

    Returns:
        Absolute path to vault root directory

    Raises:
        FileNotFoundError: If vault cannot be found and mode is "use_existing"
    """
    # 1. CLI option takes highest precedence
    if cli_vault_path:
        vault_path = Path(cli_vault_path).resolve()
        if mode == "use_existing" and not vault_path.exists():
            raise FileNotFoundError(f"Specified vault path does not exist: {vault_path}")
        elif mode == "create_ok" and not vault_path.exists():
            # For create_ok, path doesn't need to exist yet
            pass
        elif vault_path.exists() and mode == "use_existing" and not _has_vault_markers(vault_path):
            raise FileNotFoundError(f"Specified path exists but is not a Totem vault: {vault_path}")
        return vault_path

    # 2. Repo-local config (.totem/config.toml)
    repo_root = _find_repo_root(Path.cwd())
    repo_config_vault = _load_repo_config(repo_root)
    if repo_config_vault:
        if mode == "use_existing":
            if not repo_config_vault.exists():
                raise FileNotFoundError(f"Vault path from .totem/config.toml does not exist: {repo_config_vault}")
            if not _has_vault_markers(repo_config_vault):
                raise FileNotFoundError(f"Vault path from .totem/config.toml is not a valid Totem vault: {repo_config_vault}")
        return repo_config_vault

    # 3. Environment variable
    env_vault = os.environ.get("TOTEM_VAULT")
    if env_vault:
        vault_path = Path(env_vault).resolve()
        if mode == "use_existing" and not vault_path.exists():
            raise FileNotFoundError(f"TOTEM_VAULT path does not exist: {vault_path}")
        elif mode == "create_ok" and not vault_path.exists():
            # For create_ok, path doesn't need to exist yet
            pass
        elif vault_path.exists() and mode == "use_existing" and not _has_vault_markers(vault_path):
            raise FileNotFoundError(f"TOTEM_VAULT path exists but is not a Totem vault: {vault_path}")
        return vault_path

    # 4. Auto-discovery by walking up from cwd
    current_dir = Path.cwd()

    while True:
        # Check if current directory contains 90_system/config.yaml (is vault root)
        if _has_vault_markers(current_dir):
            return current_dir

        # Check if current directory contains totem_vault/90_system/config.yaml
        totem_vault_dir = current_dir / "totem_vault"
        if _has_vault_markers(totem_vault_dir):
            return totem_vault_dir

        # Move up one directory
        parent_dir = current_dir.parent

        # Stop if we reach filesystem root
        if parent_dir == current_dir:
            break

        current_dir = parent_dir

    # 5. If we get here, no vault was found
    if mode == "use_existing":
        raise FileNotFoundError(
            "Vault not found. Searched for:\n"
            f"  - Vault markers upward from {Path.cwd()}\n"
            f"  - .totem/config.toml in repo at {_find_repo_root(Path.cwd())}\n"
            f"  - TOTEM_VAULT environment variable\n"
            "Try one of:\n"
            "  • totem link-vault \"/path/to/vault\" (link existing vault to repo)\n"
            "  • totem --vault \"/path/to/vault\" <command> (override for single command)\n"
            "  • export TOTEM_VAULT=\"/path/to/vault\" (set environment variable)\n"
            "  • cd into vault directory (auto-discovery)"
        )
    else:
        # For create_ok, we shouldn't reach here since we would have fallen back to defaults
        # But if we do, use a reasonable default
        return Path.cwd() / "totem_vault"


class ChatGptSummaryConfig(BaseModel):
    """Configuration for ChatGPT conversation metadata generation."""

    enabled: bool = Field(default=True)
    provider: Literal["auto", "gemini", "openai"] = Field(default="auto")
    model: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.2)
    timeout_seconds: int = Field(default=45)
    max_input_chars: int = Field(default=14000)
    include_open_question_in_daily: bool = Field(default=True)
    version: int = Field(default=1)
    backfill_enabled: bool = Field(default=True)
    backfill_batch_size: int = Field(default=25)
    backfill_limit: Optional[int] = Field(default=None)
    backfill_sleep_ms: int = Field(default=0)


class ChatGptExportConfig(BaseModel):
    """Configuration for ChatGPT export ingestion."""

    gmail_query: str = Field(
        default='newer_than:14d from:noreply@tm.openai.com subject:"Your data export is ready"'
    )
    max_results: int = Field(default=10)
    state_file: str = Field(default="state/chatgpt_export_ingest_state.json")
    staging_dir: str = Field(default="state/chatgpt_exports")
    obsidian_chatgpt_dir: str = Field(default="40_chatgpt/conversations")
    obsidian_daily_dir: str = Field(default="40_chatgpt/daily")
    timezone: str = Field(default="America/Chicago")
    summary: ChatGptSummaryConfig = Field(default_factory=ChatGptSummaryConfig)


class LaunchdConfig(BaseModel):
    """Configuration for launchd scheduling."""

    label: str = Field(default="com.totem.chatgpt.export.ingest")
    interval_seconds: int = Field(default=21600)  # 6 hours


class TotemConfig(BaseModel):
    """Configuration for Totem OS vault and operations."""

    vault_path: Path = Field(
        default_factory=lambda: Path(os.environ.get("TOTEM_VAULT_PATH", "./totem_vault"))
    )
    route_confidence_min: float = Field(default=0.70)
    router_high_confidence_threshold: float = Field(default=0.90)
    distill_confidence_min: float = Field(default=0.75)
    entity_confidence_min: float = Field(default=0.70)

    # ChatGPT export ingestion
    chatgpt_export: ChatGptExportConfig = Field(default_factory=ChatGptExportConfig)
    launchd: LaunchdConfig = Field(default_factory=LaunchdConfig)

    model_config = {"frozen": False}

    @classmethod
    def from_env(cls, cli_vault_path: Optional[str] = None, mode: Literal["use_existing", "create_ok"] = "use_existing") -> "TotemConfig":
        """Load configuration from environment variables or defaults.

        Args:
            cli_vault_path: Vault path from CLI --vault option (highest precedence)
            mode: Vault resolution mode - "use_existing" requires markers, "create_ok" allows new vaults
        """
        # Resolve vault path using the new resolver
        vault_path = resolve_vault_root(mode, cli_vault_path)
        repo_config = _load_repo_config_data(_find_repo_root(Path.cwd()))
        repo_summary_provider = _get_repo_config_value(repo_config, ["chatgpt", "summary", "provider"])
        repo_summary_model = _get_repo_config_value(repo_config, ["chatgpt", "summary", "model"])
        summary_provider_env = os.environ.get("TOTEM_CHATGPT_SUMMARY_PROVIDER")
        summary_model_env = os.environ.get("TOTEM_CHATGPT_SUMMARY_MODEL")

        return cls(
            vault_path=vault_path,
            route_confidence_min=float(os.environ.get("TOTEM_ROUTE_CONFIDENCE_MIN", "0.70")),
            router_high_confidence_threshold=float(os.environ.get("TOTEM_ROUTER_HIGH_CONFIDENCE_THRESHOLD", "0.90")),
            distill_confidence_min=float(os.environ.get("TOTEM_DISTILL_CONFIDENCE_MIN", "0.75")),
            entity_confidence_min=float(os.environ.get("TOTEM_ENTITY_CONFIDENCE_MIN", "0.70")),
            chatgpt_export=ChatGptExportConfig(
                gmail_query=os.environ.get(
                    "TOTEM_CHATGPT_GMAIL_QUERY",
                    'newer_than:14d from:noreply@tm.openai.com subject:"Your data export is ready"'
                ),
                max_results=int(os.environ.get("TOTEM_CHATGPT_MAX_RESULTS", "10")),
                state_file=os.environ.get("TOTEM_CHATGPT_STATE_FILE", "state/chatgpt_export_ingest_state.json"),
                staging_dir=os.environ.get("TOTEM_CHATGPT_STAGING_DIR", "state/chatgpt_exports"),
                obsidian_chatgpt_dir=os.environ.get(
                    "TOTEM_CHATGPT_OBSIDIAN_DIR",
                    "40_chatgpt/conversations"
                ),
                obsidian_daily_dir=os.environ.get(
                    "TOTEM_CHATGPT_DAILY_DIR",
                    "40_chatgpt/daily"
                ),
                timezone=os.environ.get("TOTEM_CHATGPT_TIMEZONE", "America/Chicago"),
                summary=ChatGptSummaryConfig(
                    enabled=_env_bool("TOTEM_CHATGPT_SUMMARY_ENABLED", True),
                    provider=summary_provider_env or repo_summary_provider or "auto",
                    model=summary_model_env or repo_summary_model,
                    temperature=float(os.environ.get("TOTEM_CHATGPT_SUMMARY_TEMPERATURE", "0.2")),
                    timeout_seconds=int(os.environ.get("TOTEM_CHATGPT_SUMMARY_TIMEOUT_SECONDS", "45")),
                    max_input_chars=int(os.environ.get("TOTEM_CHATGPT_SUMMARY_MAX_INPUT_CHARS", "14000")),
                    include_open_question_in_daily=_env_bool(
                        "TOTEM_CHATGPT_SUMMARY_INCLUDE_OPEN_QUESTION_IN_DAILY",
                        True,
                    ),
                    version=int(os.environ.get("TOTEM_CHATGPT_SUMMARY_VERSION", "1")),
                    backfill_enabled=_env_bool("TOTEM_CHATGPT_SUMMARY_BACKFILL_ENABLED", True),
                    backfill_batch_size=int(os.environ.get("TOTEM_CHATGPT_SUMMARY_BACKFILL_BATCH_SIZE", "25")),
                    backfill_limit=(
                        int(os.environ["TOTEM_CHATGPT_SUMMARY_BACKFILL_LIMIT"])
                        if os.environ.get("TOTEM_CHATGPT_SUMMARY_BACKFILL_LIMIT")
                        else None
                    ),
                    backfill_sleep_ms=int(os.environ.get("TOTEM_CHATGPT_SUMMARY_BACKFILL_SLEEP_MS", "0")),
                ),
            ),
            launchd=LaunchdConfig(
                label=os.environ.get("TOTEM_LAUNCHD_LABEL", "com.totem.chatgpt.export.ingest"),
                interval_seconds=int(os.environ.get("TOTEM_LAUNCHD_INTERVAL", "21600")),
            ),
        )

    def to_yaml_str(self) -> str:
        """Generate YAML configuration string."""
        return f"""# Totem OS Configuration

vault_path: {self.vault_path}

# Confidence thresholds for routing and distillation
route_confidence_min: {self.route_confidence_min}
router_high_confidence_threshold: {self.router_high_confidence_threshold}
distill_confidence_min: {self.distill_confidence_min}
entity_confidence_min: {self.entity_confidence_min}

# ChatGPT export ingestion
chatgpt_export:
  gmail_query: '{self.chatgpt_export.gmail_query}'
  max_results: {self.chatgpt_export.max_results}
  state_file: '{self.chatgpt_export.state_file}'
  staging_dir: '{self.chatgpt_export.staging_dir}'
  obsidian_chatgpt_dir: '{self.chatgpt_export.obsidian_chatgpt_dir}'
  obsidian_daily_dir: '{self.chatgpt_export.obsidian_daily_dir}'
  timezone: '{self.chatgpt_export.timezone}'
  summary:
    enabled: {self.chatgpt_export.summary.enabled}
    provider: '{self.chatgpt_export.summary.provider}'
    model: '{self.chatgpt_export.summary.model or ""}'
    temperature: {self.chatgpt_export.summary.temperature}
    timeout_seconds: {self.chatgpt_export.summary.timeout_seconds}
    max_input_chars: {self.chatgpt_export.summary.max_input_chars}
    include_open_question_in_daily: {self.chatgpt_export.summary.include_open_question_in_daily}
    version: {self.chatgpt_export.summary.version}
    backfill_enabled: {self.chatgpt_export.summary.backfill_enabled}
    backfill_batch_size: {self.chatgpt_export.summary.backfill_batch_size}
    backfill_limit: {self.chatgpt_export.summary.backfill_limit}
    backfill_sleep_ms: {self.chatgpt_export.summary.backfill_sleep_ms}

# Launchd configuration for automated scheduling
launchd:
  label: '{self.launchd.label}'
  interval_seconds: {self.launchd.interval_seconds}
"""
