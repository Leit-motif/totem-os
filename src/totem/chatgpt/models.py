"""Data models for ChatGPT export conversations."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single message in a ChatGPT conversation."""

    role: str  # "user", "assistant"
    content: str
    created_at: Optional[datetime] = None


class ChatGptMessage(ChatMessage):
    """Backward-compatible alias for ChatGPT messages."""
    pass


class ChatGptConversation(BaseModel):
    """A ChatGPT conversation."""

    conversation_id: str
    title: str = "Untitled Conversation"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    messages: list[ChatMessage] = Field(default_factory=list)


class ParsedConversations(BaseModel):
    """Result of parsing conversations from export."""

    conversations: list[ChatGptConversation] = Field(default_factory=list)
    total_count: int = 0
    parsed_count: int = 0
    errors: list[str] = Field(default_factory=list)

