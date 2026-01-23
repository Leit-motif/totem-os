"""Obsidian note writer for ChatGPT conversations."""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import ChatGptConversation

logger = logging.getLogger(__name__)


def compute_content_hash(conversation: ChatGptConversation) -> str:
    """Compute SHA256 hash of conversation content for idempotency.

    Args:
        conversation: The conversation to hash

    Returns:
        SHA256 hash as hex string
    """
    # Create normalized content string
    content_parts = [
        conversation.conversation_id,
        conversation.title,
        str(conversation.created_at.timestamp()),
        str(conversation.updated_at.timestamp())
    ]

    # Add all message content in order
    for msg in conversation.messages:
        content_parts.extend([
            msg.role,
            msg.content,
            str(msg.timestamp.timestamp()) if msg.timestamp else ""
        ])

    content_str = "|".join(content_parts)
    return hashlib.sha256(content_str.encode('utf-8')).hexdigest()


def format_conversation_markdown(conversation: ChatGptConversation, gmail_msg_id: str) -> str:
    """Format conversation as Obsidian markdown with frontmatter.

    Args:
        conversation: The conversation to format
        gmail_msg_id: Gmail message ID for tracking

    Returns:
        Complete markdown content
    """
    content_hash = compute_content_hash(conversation)

    # Escape title for YAML frontmatter
    escaped_title = conversation.title.replace('"', '\\"')

    # Frontmatter
    frontmatter_lines = [
        "---",
        f"source: chatgpt_export",
        f"conversation_id: {conversation.conversation_id}",
        f"title: \"{escaped_title}\"",
        f"created_at: {conversation.created_at.isoformat()}",
        f"updated_at: {conversation.updated_at.isoformat()}",
        f"ingested_from: gmail:{gmail_msg_id}",
        f"content_hash: {content_hash}",
        "---",
        "",
    ]

    # Title
    body_lines = [
        f"# {conversation.title}",
        "",
    ]

    # Messages
    for msg in conversation.messages:
        # Format timestamp if available
        timestamp_str = ""
        if msg.timestamp:
            timestamp_str = f" ({msg.timestamp.strftime('%H:%M')})"

        # Role header
        role_display = msg.role.title()
        body_lines.extend([
            f"## {role_display}{timestamp_str}",
            "",
        ])

        # Content
        if msg.content.strip():
            # Simple markdown formatting - preserve line breaks
            content = msg.content.replace('\n', '\n\n')
            body_lines.extend([content, ""])
        else:
            body_lines.extend(["*(empty message)*", ""])

    return "\n".join(frontmatter_lines + body_lines)


def write_conversation_note(
    conversation: ChatGptConversation,
    obsidian_dir: Path,
    gmail_msg_id: str,
    timezone: str = "America/Chicago"
) -> Path:
    """Write conversation as Obsidian note.

    Args:
        conversation: The conversation to write
        obsidian_dir: Base Obsidian ChatGPT directory
        gmail_msg_id: Gmail message ID for tracking
        timezone: Timezone for date-based organization

    Returns:
        Path to the written file
    """
    # Determine local date for organization
    local_date = conversation.created_at.astimezone()
    date_str = local_date.strftime("%Y-%m-%d")

    # Create directory structure
    note_dir = obsidian_dir / date_str
    note_dir.mkdir(parents=True, exist_ok=True)

    # Create filename
    safe_title = "".join(c for c in conversation.title if c.isalnum() or c in " -_").strip()
    if not safe_title:
        safe_title = "Untitled"

    filename = f"chatgpt__{conversation.conversation_id}.md"
    note_path = note_dir / filename

    # Check if file exists and has same content hash
    content_hash = compute_content_hash(conversation)

    if note_path.exists():
        try:
            existing_content = note_path.read_text(encoding='utf-8')

            # Extract existing hash from frontmatter
            existing_hash = None
            in_frontmatter = False
            for line in existing_content.split('\n'):
                if line.strip() == '---':
                    in_frontmatter = not in_frontmatter
                    continue
                if in_frontmatter and line.startswith('content_hash:'):
                    existing_hash = line.split(':', 1)[1].strip()
                    break

            if existing_hash == content_hash:
                logger.info(f"Conversation {conversation.conversation_id} unchanged, skipping write")
                return note_path

        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Could not read existing file {note_path}: {e}")

    # Generate and write new content
    markdown_content = format_conversation_markdown(conversation, gmail_msg_id)

    note_path.write_text(markdown_content, encoding='utf-8')
    logger.info(f"Wrote conversation note: {note_path}")

    return note_path