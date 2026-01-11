"""Configuration management for Totem OS."""

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class TotemConfig(BaseModel):
    """Configuration for Totem OS vault and operations."""

    vault_path: Path = Field(
        default_factory=lambda: Path(os.environ.get("TOTEM_VAULT_PATH", "./totem_vault"))
    )
    route_confidence_min: float = Field(default=0.70)
    distill_confidence_min: float = Field(default=0.75)
    entity_confidence_min: float = Field(default=0.70)

    model_config = {"frozen": False}

    @classmethod
    def from_env(cls) -> "TotemConfig":
        """Load configuration from environment variables or defaults."""
        vault_path_str = os.environ.get("TOTEM_VAULT_PATH", "./totem_vault")
        vault_path = Path(vault_path_str)

        return cls(
            vault_path=vault_path,
            route_confidence_min=float(os.environ.get("TOTEM_ROUTE_CONFIDENCE_MIN", "0.70")),
            distill_confidence_min=float(os.environ.get("TOTEM_DISTILL_CONFIDENCE_MIN", "0.75")),
            entity_confidence_min=float(os.environ.get("TOTEM_ENTITY_CONFIDENCE_MIN", "0.70")),
        )

    def to_yaml_str(self) -> str:
        """Generate YAML configuration string."""
        return f"""# Totem OS Configuration

vault_path: {self.vault_path}

# Confidence thresholds for routing and distillation
route_confidence_min: {self.route_confidence_min}
distill_confidence_min: {self.distill_confidence_min}
entity_confidence_min: {self.entity_confidence_min}
"""
