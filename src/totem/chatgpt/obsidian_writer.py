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
        conversation.created_at.isoformat() if conversation.created_at else "",
        conversation.updated_at.isoformat() if conversation.updated_at else "",
    ]

    # Add all message content in order
    for msg in conversation.messages:
        content_parts.extend([
            msg.role,
            msg.content,
            msg.created_at.isoformat() if msg.created_at else "",
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
        f"created_at: {conversation.created_at.isoformat() if conversation.created_at else ''}",
        f"updated_at: {conversation.updated_at.isoformat() if conversation.updated_at else ''}",
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

    body_lines.extend(["## Transcript", ""])

    if not conversation.messages:
        body_lines.append(
            "No message content was found in this export for this conversation."
        )
        body_lines.append("")
    else:
        for msg in conversation.messages:
            role_display = msg.role.title()
            body_lines.extend([f"### {role_display}", ""])

            if msg.created_at:
                body_lines.extend([f"*{msg.created_at.isoformat()}*", ""])

            content = msg.content.strip()
            if content:
                body_lines.extend([content, ""])
            else:
                body_lines.extend(["*(empty message)*", ""])

    return "\n".join(frontmatter_lines + body_lines)


def write_conversation_note(
    conversation: ChatGptConversation,
    obsidian_dir: Path,
    gmail_msg_id: str,
    timezone: str = "America/Chicago",
    run_date_str: str = ""
) -> Path:
    """Write conversation as Obsidian note.

    Args:
        conversation: The conversation to write
        obsidian_dir: Base Obsidian ChatGPT directory
        gmail_msg_id: Gmail message ID for tracking
        timezone: Timezone for date-based organization
        run_date_str: Fallback date string (YYYY-MM-DD)

    Returns:
        Path to the written file
    """
    # Determine local date for organization
    if conversation.created_at:
        local_date = conversation.created_at.astimezone()
        date_str = local_date.strftime("%Y-%m-%d")
    else:
        date_str = run_date_str or datetime.now().strftime("%Y-%m-%d")

    year, month, day = date_str.split("-")

    # Create directory structure
    note_dir = obsidian_dir / year / month / day
    note_dir.mkdir(parents=True, exist_ok=True)

    # Create filename from title with de-dupe
    safe_title = "".join(
        c for c in conversation.title if c.isalnum() or c in " -_"
    ).strip()
    if not safe_title:
        safe_title = "Placeholder Title"

    note_path = _resolve_note_path(note_dir, safe_title, conversation.conversation_id)

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


def _resolve_note_path(note_dir: Path, base_title: str, conversation_id: str) -> Path:
    """Find a stable filename for a conversation, de-duping by title."""
    suffix = 0

    while True:
        title = base_title if suffix == 0 else f"{base_title}-{suffix + 1}"
        candidate = note_dir / f"{title}.md"

        if not candidate.exists():
            return candidate

        existing_id = _read_conversation_id(candidate)
        if existing_id == conversation_id:
            return candidate

        suffix += 1


def _read_conversation_id(note_path: Path) -> Optional[str]:
    """Read conversation_id from frontmatter if present."""
    try:
        existing_content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    in_frontmatter = False
    for line in existing_content.split("\n"):
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter and line.startswith("conversation_id:"):
            return line.split(":", 1)[1].strip()

    return None
