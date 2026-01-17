"""Pydantic models for intent classification."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    """Intent classification types."""
    
    REFLECT = "reflect"
    KNOWLEDGE_UPDATE = "knowledge_update"
    TASK_GENERATION = "task_generation"
    DECISION_SUPPORT = "decision_support"
    EXECUTION = "execution"
    IGNORE = "ignore"


class IntentDecision(BaseModel):
    """Result of intent classification."""
    
    intent_type: IntentType = Field(description="Classified intent type")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    rationale: str = Field(description="Short rationale for the decision")
    suggested_agents: list[str] = Field(
        default_factory=list, 
        description="Optional list of suggested downstream agents"
    )
    
    model_config = {"frozen": True}
