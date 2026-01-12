"""Pydantic models for distillation and canon writes."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Priority(str, Enum):
    """Task priority levels."""
    
    LOW = "low"
    MED = "med"
    HIGH = "high"


class EntityKind(str, Enum):
    """Entity classification types."""
    
    PERSON = "person"
    PROJECT = "project"
    TOOL = "tool"
    TOPIC = "topic"


class TaskItem(BaseModel):
    """Actionable task extracted from distillation.
    
    Represents a single actionable item with optional priority and due date.
    """
    
    text: str = Field(description="Task description", max_length=500)
    priority: Priority = Field(default=Priority.MED, description="Task priority")
    due_date: str | None = Field(
        default=None,
        description="Optional due date in YYYY-MM-DD format"
    )
    
    model_config = {"frozen": False}


class EntityMention(BaseModel):
    """Entity mentioned in a capture during distillation.
    
    Represents a person, project, tool, or topic identified in the content.
    """
    
    name: str = Field(description="Entity name", max_length=100)
    kind: EntityKind = Field(description="Entity type classification")
    note: str | None = Field(
        default=None,
        description="Optional context note about this entity mention",
        max_length=200
    )
    
    model_config = {"frozen": False}


class DistillResult(BaseModel):
    """Result of LLM distillation for a routed capture.
    
    Written to 10_derived/distill/YYYY-MM-DD/<capture_id>.json
    Contains extracted summary, key points, tasks, and entities.
    """
    
    capture_id: str = Field(description="Capture identifier")
    distilled_at: str = Field(description="Timestamp when distilled (ISO8601 UTC with Z suffix)")
    route_label: str = Field(description="Route label from routing step")
    summary: str = Field(
        description="Brief summary of the capture content",
        max_length=500
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="Key points extracted (max 5)",
        max_length=5
    )
    tasks: list[TaskItem] = Field(
        default_factory=list,
        description="Actionable tasks extracted (max 7)"
    )
    entities: list[EntityMention] = Field(
        default_factory=list,
        description="Entities mentioned (max 7)"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score for distillation quality (0.0-1.0)"
    )
    reasoning: str = Field(
        default="",
        description="Short explanation of signals used for distillation",
        max_length=200
    )
    
    model_config = {"frozen": False}


class AppliedFile(BaseModel):
    """Record of a single file modification during canon write.
    
    Stores the exact text that was inserted and the mode of insertion.
    This enables reliable undo by removing the exact inserted block.
    The content_hash allows detection of manual edits before undo.
    """
    
    path: str = Field(description="Relative path to the modified file")
    inserted_text: str = Field(description="Exact text block that was inserted")
    content_hash: str = Field(description="SHA256 hash of inserted_text for integrity check")
    mode: Literal["append"] = Field(
        default="append",
        description="Write mode (append-only for now)"
    )
    
    model_config = {"frozen": False}


class CanonWriteRecord(BaseModel):
    """Audit trail record for canon writes.
    
    Written to 90_system/traces/writes/YYYY-MM-DD/<write_id>.json
    Enables reversible undo of distillation outputs.
    """
    
    write_id: str = Field(description="Unique write identifier (uuid4)")
    ts: str = Field(description="Timestamp when write occurred (ISO8601 UTC with Z suffix)")
    capture_id: str = Field(description="Source capture identifier")
    applied_files: list[AppliedFile] = Field(
        default_factory=list,
        description="List of files modified with inserted text"
    )
    distill_path: str = Field(description="Relative path to distill artifact JSON")
    can_undo: bool = Field(
        default=True,
        description="Whether this write can be safely undone"
    )
    
    model_config = {"frozen": False}
