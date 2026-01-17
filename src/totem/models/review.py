"""Pydantic models for Milestone 6: Review and Correction system.

Philosophy: Totem presents proposed artifacts for user judgment.
User actions: Approve, Veto, Correct, Defer.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ArtifactType(str, Enum):
    """Types of artifacts that can be proposed."""
    
    TASK = "task"
    NOTE = "note"
    PRINCIPLE = "principle"
    MEMORY = "memory"
    DECISION = "decision"
    ENTITY = "entity"


class ProposalStatus(str, Enum):
    """Status of a proposed artifact in the review queue."""
    
    PENDING = "pending"
    APPROVED = "approved"
    VETOED = "vetoed"
    CORRECTED = "corrected"
    DEFERRED = "deferred"


class ProposedArtifact(BaseModel):
    """A proposed artifact awaiting user review.
    
    Represents Totem's suggestion that needs human judgment.
    The user never categorizes/tags/files - only approves, vetoes, corrects, or defers.
    """
    
    proposal_id: str = Field(description="Unique proposal identifier (uuid4)")
    capture_id: str | None = Field(
        default=None,
        description="Source capture ID if applicable"
    )
    run_id: str | None = Field(
        default=None,
        description="Run/session ID that generated this proposal"
    )
    artifact_type: ArtifactType = Field(description="Type of artifact being proposed")
    title: str | None = Field(
        default=None,
        description="Optional title for the artifact"
    )
    content: str = Field(description="Main content (string or serialized payload)")
    destination: str = Field(
        description="Canonical path or logical bucket for this artifact"
    )
    rationale: str = Field(
        description="1-2 sentences explaining why Totem proposes this",
        max_length=500
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Totem's confidence in this proposal (0.0-1.0)"
    )
    created_at: str = Field(description="ISO8601 UTC timestamp when proposal was created")
    
    # Additional context
    source_file: str | None = Field(
        default=None,
        description="Original source file path if applicable"
    )
    
    model_config = {"frozen": False}


class ReviewQueueItem(BaseModel):
    """A proposal in the review queue with its status.
    
    Wraps ProposedArtifact with review workflow state.
    """
    
    proposal: ProposedArtifact = Field(description="The proposed artifact")
    status: ProposalStatus = Field(
        default=ProposalStatus.PENDING,
        description="Current review status"
    )
    status_changed_at: str | None = Field(
        default=None,
        description="Timestamp when status last changed"
    )
    defer_count: int = Field(
        default=0,
        description="Number of times this item has been deferred"
    )
    
    model_config = {"frozen": False}


class OverrideArtifact(BaseModel):
    """Record of a user correction/override.
    
    When the user corrects a proposal, this captures both the
    original proposal and the corrected version for learning.
    """
    
    override_id: str = Field(description="Unique override identifier (uuid4)")
    proposal_id: str = Field(description="Original proposal that was corrected")
    original_summary: str = Field(
        description="Compact summary of original proposal for reference",
        max_length=500
    )
    corrected_artifact_type: ArtifactType = Field(
        description="Type of the corrected artifact"
    )
    corrected_title: str | None = Field(
        default=None,
        description="Title after correction"
    )
    corrected_content: str = Field(description="Content after correction")
    corrected_destination: str | None = Field(
        default=None,
        description="Destination after correction (if changed)"
    )
    created_at: str = Field(description="ISO8601 UTC timestamp")
    
    model_config = {"frozen": False}


class ReviewEventType(str, Enum):
    """Types of review events for learning."""
    
    APPROVED = "review_approved"
    VETOED = "review_vetoed"
    DEFERRED = "review_deferred"
    CORRECTED = "review_corrected"


class ReviewEvent(BaseModel):
    """Learning event from a review action.
    
    Emitted to help Totem learn from user decisions.
    """
    
    event_id: str = Field(description="Unique event identifier (uuid4)")
    event_type: ReviewEventType = Field(description="Type of review event")
    proposal_id: str = Field(description="Proposal that was reviewed")
    capture_id: str | None = Field(
        default=None,
        description="Source capture ID if applicable"
    )
    run_id: str | None = Field(
        default=None,
        description="Run ID if applicable"
    )
    artifact_type: str = Field(description="Type of the proposed artifact")
    ts: str = Field(description="ISO8601 UTC timestamp")
    
    # Event-specific payload
    payload: dict = Field(
        default_factory=dict,
        description="Event-specific data (e.g., override details for corrections)"
    )
    
    model_config = {"frozen": True}
