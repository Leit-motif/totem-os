"""Pydantic models for Omi transcript data."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class OmiTranscriptSegment(BaseModel):
    """A single transcript segment from an Omi conversation.
    
    Represents one speaker's utterance in the conversation.
    """
    
    segment_id: str = Field(..., description="Unique segment identifier")
    speaker_id: str = Field(..., description="Speaker identifier (e.g., 'SPEAKER_00')")
    text: str = Field(..., description="Transcript text for this segment")
    timestamp: datetime | None = Field(None, description="Optional timestamp for segment")


class OmiConversation(BaseModel):
    """An Omi conversation with transcript segments.
    
    Represents a complete conversation recording with all transcript segments
    and optional metadata from Omi API.
    """
    
    id: str = Field(..., description="Unique conversation identifier")
    started_at: datetime = Field(..., description="Conversation start timestamp")
    finished_at: datetime = Field(..., description="Conversation end timestamp")
    transcript: list[OmiTranscriptSegment] = Field(
        default_factory=list,
        description="List of transcript segments in order"
    )
    
    # Optional metadata fields from Omi API
    overview: str | None = Field(None, description="Short summary of the conversation")
    action_items: list[str] = Field(
        default_factory=list,
        description="List of action items/tasks from the conversation"
    )
    category: str | None = Field(None, description="Conversation category")
    emoji: str | None = Field(None, description="Associated emoji")
    location: str | None = Field(None, description="Geolocation/location string")


class OmiSyncResult(BaseModel):
    """Result of an Omi transcript sync operation.
    
    Contains statistics about what was synced and written.
    """
    
    date: str = Field(..., description="Date synced (YYYY-MM-DD)")
    conversations_count: int = Field(..., description="Number of conversations processed")
    segments_written: int = Field(..., description="Number of new segments written")
    segments_skipped: int = Field(..., description="Number of duplicate segments skipped")
    file_path: Path = Field(..., description="Path to written markdown file")


class DailyNoteResult(BaseModel):
    """Result of a daily note Omi block write operation.
    
    Contains information about what was written to the daily note.
    """
    
    date: str = Field(..., description="Date of daily note (YYYY-MM-DD)")
    daily_note_path: Path = Field(..., description="Path to daily note file")
    transcript_wikilink: str = Field(..., description="Wikilink to transcript file")
    conversations_count: int = Field(..., description="Number of conversations aggregated")
    action_items_count: int = Field(..., description="Total number of action items")
    marker_status: str = Field("new", description="Status of markers: new, existing, recovered")
    block_replaced: bool = Field(False, description="Whether an existing block was replaced")
