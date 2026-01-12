"""Pydantic models for capture metadata."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CaptureMeta(BaseModel):
    """Metadata for a captured item.
    
    Written as <capture_filename>.meta.json in the same folder as the raw file.
    """

    id: str = Field(description="Unique capture identifier (uuid4)")
    created_at: datetime = Field(description="Capture creation timestamp (ISO8601 UTC)")
    source: Literal["cli_text", "cli_file", "manual", "chatgpt_export", "other"] = Field(
        description="Source of the capture"
    )
    type: Literal["text", "markdown", "audio", "image", "pdf", "json", "other"] = Field(
        description="Content type"
    )
    files: list[str] = Field(description="List of associated filenames (relative to meta file)")
    context: dict | None = Field(default=None, description="Optional additional metadata")
    origin: dict | None = Field(default=None, description="Optional origin information")

    model_config = {"frozen": False}
