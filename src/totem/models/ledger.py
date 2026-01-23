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
        # Milestone 6: Review events
        "REVIEW_APPROVED",
        "REVIEW_VETOED",
        "REVIEW_DEFERRED",
        "REVIEW_CORRECTED",
        # Milestone 7: Intent Arbiter
        "INTENT_DECISION",
        # Milestone 7.5: Omi sync events
        "OMI_SYNC_FETCHED",
        "OMI_TRANSCRIPT_WRITTEN",
        "OMI_DAILY_NOTE_WRITTEN",
        "OMI_DAILY_NOTE_BLOCK_MALFORMED",
        # Milestone 8: ChatGPT export ingestion
        "CHATGPT_EXPORT_INGEST_STARTED",
        "CHATGPT_EXPORT_EMAILS_FOUND",
        "CHATGPT_EXPORT_EMAIL_SELECTED",
        "CHATGPT_EXPORT_EMAIL_SKIPPED",
        "CHATGPT_EXPORT_DOWNLOAD_STARTED",
        "CHATGPT_EXPORT_DOWNLOADED",
        "CHATGPT_EXPORT_UNZIPPED",
        "CHATGPT_EXPORT_PARSED",
        "CHATGPT_CONVERSATIONS_WRITTEN",
        "CHATGPT_DAILY_NOTE_WRITTEN",
        "CHATGPT_EXPORT_INGEST_COMPLETED",
        "CHATGPT_EXPORT_INGEST_FAILED",
        "CHATGPT_EXPORT_LOCAL_ZIP_SELECTED",
        "CHATGPT_EXPORT_LOCAL_ZIP_INGEST_STARTED",
        "CHATGPT_EXPORT_LOCAL_ZIP_INGESTED",
        "CHATGPT_EXPORT_LOCAL_ZIP_INGEST_FAILED",
        "CHATGPT_EXPORT_LOCAL_ZIP_NOT_FOUND",
    ] = Field(description="Event type")
    capture_id: str | None = Field(default=None, description="Related capture ID if applicable")
    payload: dict = Field(default_factory=dict, description="Event-specific data")

    model_config = {"frozen": True}
