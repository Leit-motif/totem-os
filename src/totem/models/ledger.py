"""Pydantic models for ledger events."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LedgerEvent(BaseModel):
    """Append-only ledger event record.
    
    Written as JSONL to <vault>/90_system/ledger.jsonl.
    Never mutate or delete; only append.
    """

    event_id: str = Field(description="Unique event identifier (uuid4)")
    run_id: str = Field(description="Run/session identifier (uuid4)")
    ts: datetime = Field(description="Event timestamp (ISO8601 UTC)")
    event_type: Literal[
        "CAPTURE_INGESTED",
        "CAPTURE_META_GENERATED",
        "DERIVED_TRANSCRIPT_CREATED",
        "CAPTURE_ROUTED",
        "FLAGGED_FOR_REVIEW",
        "DISTILL_RUN_STARTED",
        "DISTILL_RESULT_WRITTEN",
        "DISTILL_APPLIED",
        "DISTILL_SIMULATED",
        "DISTILL_UNDONE",
        "TASKS_UPDATED",
        "MEMORY_PROMOTED",
        "CORRECTION_APPLIED",
    ] = Field(description="Event type")
    capture_id: str | None = Field(default=None, description="Related capture ID if applicable")
    payload: dict = Field(default_factory=dict, description="Event-specific data")

    model_config = {"frozen": True}
