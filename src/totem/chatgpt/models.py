"""Data models for ChatGPT export conversations."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ChatGptMessage(BaseModel):
    """A single message in a ChatGPT conversation."""

    role: str  # "user", "assistant", "system"
    content: str
    timestamp: Optional[datetime] = None


class ChatGptConversation(BaseModel):
    """A ChatGPT conversation."""

    conversation_id: str
    title: str = "Untitled Conversation"
    created_at: datetime
    updated_at: datetime
    messages: list[ChatGptMessage] = Field(default_factory=list)


class ParsedConversations(BaseModel):
    """Result of parsing conversations from export."""

    conversations: list[ChatGptConversation] = Field(default_factory=list)
    total_count: int = 0
    parsed_count: int = 0
    errors: list[str] = Field(default_factory=list)