"""Pydantic models for routing and review queue."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class RouteLabel(str, Enum):
    """Route classification labels for captures."""
    
    TASK = "TASK"
    IDEA = "IDEA"
    JOURNAL = "JOURNAL"
    PEOPLE = "PEOPLE"
    ADMIN = "ADMIN"
    UNKNOWN = "UNKNOWN"


class RouteResult(BaseModel):
    """Result of routing a capture through keyword heuristics.
    
    This is the output of the RuleRouter before bouncer logic.
    """
    
    capture_id: str = Field(description="Capture identifier")
    route_label: RouteLabel = Field(description="Assigned route category")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    next_actions: list[str] = Field(
        default_factory=list,
        description="Extracted action items (max 3)"
    )
    reasoning: str = Field(
        default="",
        description="Human-readable explanation of routing decision"
    )
    
    model_config = {"frozen": False}


class RoutedItem(BaseModel):
    """Final output for a routed capture (confidence >= threshold).
    
    Written to 10_derived/routed/YYYY-MM-DD/<capture_id>.json
    """
    
    capture_id: str = Field(description="Capture identifier")
    routed_at: datetime = Field(description="Timestamp when routing was performed")
    route_label: RouteLabel = Field(description="Assigned route category")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score")
    next_actions: list[str] = Field(default_factory=list, description="Extracted actions")
    reasoning: str = Field(default="", description="Routing decision explanation")
    
    # Original capture references
    raw_file_path: str = Field(description="Relative path to raw capture file")
    meta_file_path: str = Field(description="Relative path to meta.json file")
    
    model_config = {"frozen": False}


class ReviewItem(BaseModel):
    """Final output for a capture flagged for review (confidence < threshold).
    
    Written to 10_derived/review_queue/YYYY-MM-DD/<capture_id>.json
    """
    
    capture_id: str = Field(description="Capture identifier")
    flagged_at: datetime = Field(description="Timestamp when flagged for review")
    route_label: RouteLabel = Field(description="Tentative route category")
    confidence: float = Field(ge=0.0, le=1.0, description="Low confidence score")
    next_actions: list[str] = Field(default_factory=list, description="Extracted actions")
    reasoning: str = Field(default="", description="Routing decision explanation")
    
    # Why it needs review
    review_reason: str = Field(
        description="Explanation of why this item was flagged for review"
    )
    
    # Original capture references
    raw_file_path: str = Field(description="Relative path to raw capture file")
    meta_file_path: str = Field(description="Relative path to meta.json file")
    
    model_config = {"frozen": False}
