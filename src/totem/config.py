"""Configuration management for Totem OS."""

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


def resolve_vault_root(cli_vault_path: Optional[str] = None) -> Path:
    """Resolve vault root path with the following precedence:

    1. CLI --vault option (if provided)
    2. TOTEM_VAULT environment variable
    3. Auto-discovery by walking up from cwd looking for vault indicators

    Returns:
        Absolute path to vault root directory

    Raises:
        FileNotFoundError: If vault cannot be found via auto-discovery
    """
    # 1. CLI option takes highest precedence
    if cli_vault_path:
        vault_path = Path(cli_vault_path).resolve()
        if not vault_path.exists():
            raise FileNotFoundError(f"Specified vault path does not exist: {vault_path}")
        return vault_path

    # 2. Environment variable
    env_vault = os.environ.get("TOTEM_VAULT")
    if env_vault:
        vault_path = Path(env_vault).resolve()
        if not vault_path.exists():
            raise FileNotFoundError(f"TOTEM_VAULT path does not exist: {vault_path}")
        return vault_path

    # 3. Auto-discovery by walking up from cwd
    current_dir = Path.cwd()

    while True:
        # Check if current directory contains 90_system/config.yaml (is vault root)
        system_dir = current_dir / "90_system"
        config_file = system_dir / "config.yaml"
        if config_file.exists():
            return current_dir

        # Check if current directory contains totem_vault/90_system/config.yaml
        totem_vault_dir = current_dir / "totem_vault"
        totem_system_dir = totem_vault_dir / "90_system"
        totem_config_file = totem_system_dir / "config.yaml"
        if totem_config_file.exists():
            return totem_vault_dir

        # Move up one directory
        parent_dir = current_dir.parent

        # Stop if we reach filesystem root
        if parent_dir == current_dir:
            break

        current_dir = parent_dir

    # If we get here, no vault was found
    raise FileNotFoundError(
        f"Vault not found. Searched upward from {Path.cwd()} for vault indicators. "
        "Run 'totem init' to initialize a vault, or specify --vault path."
    )


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
    def from_env(cls, cli_vault_path: Optional[str] = None) -> "TotemConfig":
        """Load configuration from environment variables or defaults.

        Args:
            cli_vault_path: Vault path from CLI --vault option (highest precedence)
        """
        # Resolve vault path using the new resolver
        vault_path = resolve_vault_root(cli_vault_path)

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

# Launchd configuration for automated scheduling
launchd:
  label: '{self.launchd.label}'
  interval_seconds: {self.launchd.interval_seconds}
"""
