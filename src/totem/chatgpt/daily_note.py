"""Daily note writer for ChatGPT conversation summaries."""

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..ledger import LedgerWriter
from .models import ChatGptConversation
from .metadata import read_metadata_fields

from pydantic import BaseModel


class DailyNoteResult(BaseModel):
    """Result of updating daily note with ChatGPT conversations."""

    date: str
    daily_note_path: Path
    conversations_count: int
    marker_status: str  # "new", "existing", "recovered"
    block_replaced: bool


def write_daily_note_chatgpt_block(
    conversations: List[ChatGptConversation],
    date_str: str,
    vault_root: Path,
    ledger_writer: LedgerWriter,
    conversation_note_paths: Optional[Dict[str, Path]] = None,
    include_open_question_in_daily: bool = True,
) -> DailyNoteResult:
    """Write or update ChatGPT block in daily note with idempotency.

    Follows the same pattern as OMI daily note blocks.

    Args:
        conversations: List of conversations for this date
        date_str: Date string (YYYY-MM-DD)
        vault_root: Root path of Obsidian vault
        ledger_writer: Ledger writer for events

    Returns:
        DailyNoteResult with statistics
    """
    # Group conversations by local date
    conversations_by_date: Dict[str, List[ChatGptConversation]] = defaultdict(list)

    for conv in conversations:
        # Use created_at for date determination, fallback to provided date_str
        if conv.created_at:
            local_date = conv.created_at.astimezone()
            conv_date_str = local_date.strftime("%Y-%m-%d")
        else:
            conv_date_str = date_str
        conversations_by_date[conv_date_str].append(conv)

    # Process each date
    total_processed = 0
    marker_status = "new"
    block_replaced = False

    for conv_date_str, day_convos in conversations_by_date.items():
        result = _write_single_date_block(
            day_convos,
            conv_date_str,
            vault_root,
            ledger_writer,
            conversation_note_paths,
            include_open_question_in_daily,
        )
        total_processed += result.conversations_count
        if result.block_replaced:
            block_replaced = True
        marker_status = result.marker_status

    # Return result for the primary date
    year, month, _day = date_str.split("-")
    daily_note_path = (
        vault_root / "5.0 Journal" / "5.1 Daily" / year / month / f"{date_str}.md"
    )

    return DailyNoteResult(
        date=date_str,
        daily_note_path=daily_note_path,
        conversations_count=total_processed,
        marker_status=marker_status,
        block_replaced=block_replaced,
    )


def _write_single_date_block(
    conversations: List[ChatGptConversation],
    date_str: str,
    vault_root: Path,
    ledger_writer: LedgerWriter,
    conversation_note_paths: Optional[Dict[str, Path]] = None,
    include_open_question_in_daily: bool = True,
) -> DailyNoteResult:
    """Write ChatGPT block for a single date."""
    # Sort conversations by creation time
    def sort_key(conv: ChatGptConversation) -> datetime:
        if conv.created_at:
            return conv.created_at
        return datetime.strptime(date_str, "%Y-%m-%d")

    sorted_conversations = sorted(conversations, key=sort_key)

    # Create daily note path
    year, month, _day = date_str.split("-")
    daily_note_dir = vault_root / "5.0 Journal" / "5.1 Daily" / year / month
    daily_note_dir.mkdir(parents=True, exist_ok=True)
    daily_note_path = daily_note_dir / f"{date_str}.md"

    # Build ChatGPT block content
    block_lines = ["<!-- TOTEM:CHATGPT:START -->", "## Transcripts", ""]

    for conv in sorted_conversations:
        # Create path-qualified link to conversation note
        link_path = _build_conversation_link_path(
            conv,
            date_str,
            conversation_note_paths,
            vault_root,
        )

        block_lines.append(f"- [[{link_path}]]")

        note_path = None
        if conversation_note_paths and conv.conversation_id in conversation_note_paths:
            note_path = conversation_note_paths[conv.conversation_id]

        if note_path and note_path.exists():
            metadata = read_metadata_fields(note_path)
            signpost = metadata.get("totem_signpost")
            if signpost:
                signpost_text = str(signpost).replace("\n", " ").strip()
                if metadata.get("totem_summary_confidence") == "partial":
                    signpost_text = f"{signpost_text} ⏳"
                block_lines.append(f"  - {signpost_text}")

            open_questions = metadata.get("totem_open_questions") or []
            if include_open_question_in_daily and open_questions:
                question = str(open_questions[0]).replace("\n", " ").strip()
                block_lines.append(f"  - Q: {question}")

    block_lines.extend(["", "<!-- TOTEM:CHATGPT:END -->"])
    chatgpt_block = "\n".join(block_lines)

    # Read existing content or create new
    if daily_note_path.exists():
        existing_content = daily_note_path.read_text(encoding="utf-8")
    else:
        # Create new daily note with minimal header
        existing_content = f"# {date_str}\n\n"

    # Marker analysis and robustness (following OMI pattern)
    start_marker = "<!-- TOTEM:CHATGPT:START -->"
    end_marker = "<!-- TOTEM:CHATGPT:END -->"

    start_count = existing_content.count(start_marker)
    end_count = existing_content.count(end_marker)

    marker_status = "new"
    block_replaced = False
    malformed = False

    if start_count == 0 and end_count == 0:
        # Case A: New block
        new_content = existing_content.rstrip() + "\n\n" + chatgpt_block + "\n"
        marker_status = "new"

    elif start_count == 1 and end_count == 1:
        # Case B: Canonical replacement (Normal update)
        # Check order
        start_pos = existing_content.find(start_marker)
        end_pos = existing_content.find(end_marker)

        if start_pos < end_pos:
            pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
            new_content = re.sub(pattern, chatgpt_block, existing_content, flags=re.DOTALL)
            marker_status = "existing"
            block_replaced = True
        else:
            # Malformed: end before start
            malformed = True
    else:
        # Case C: Multiple or mismatched markers (Recovery)
        malformed = True

    if malformed:
        # Recovery strategy: Remove ALL marker debris and append new block
        marker_status = "recovered"
        block_replaced = True

        # Remove complete blocks
        pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
        temp_content = re.sub(pattern, "", existing_content, flags=re.DOTALL)

        # Remove any orphaned markers
        temp_content = temp_content.replace(start_marker, "").replace(end_marker, "")

        # Clean up excessive newlines
        temp_content = re.sub(r"\n{3,}", "\n\n", temp_content)

        # Append canonical block
        new_content = temp_content.rstrip() + "\n\n" + chatgpt_block + "\n"

    # Write to file
    daily_note_path.write_text(new_content, encoding="utf-8")

    # Log event
    ledger_writer.append_event(
        event_type="CHATGPT_DAILY_NOTE_WRITTEN",
        payload={
            "date": date_str,
            "daily_note_path": str(daily_note_path),
            "conversations_count": len(conversations),
            "marker_status": marker_status,
            "block_replaced": block_replaced,
        },
    )

    return DailyNoteResult(
        date=date_str,
        daily_note_path=daily_note_path,
        conversations_count=len(conversations),
        marker_status=marker_status,
        block_replaced=block_replaced,
    )


def _build_conversation_link_path(
    conversation: ChatGptConversation,
    date_str: str,
    conversation_note_paths: Optional[Dict[str, Path]],
    vault_root: Path,
) -> str:
    """Build vault-relative path for a conversation note."""
    if conversation_note_paths and conversation.conversation_id in conversation_note_paths:
        note_path = conversation_note_paths[conversation.conversation_id]
        try:
            relative = note_path.relative_to(vault_root)
            return relative.stem
        except ValueError:
            pass
        return note_path.stem

    if conversation.created_at:
        local_date = conversation.created_at.astimezone()
        date_str = local_date.strftime("%Y-%m-%d")

    year, month, day = date_str.split("-")
    safe_title = "".join(
        c for c in conversation.title if c.isalnum() or c in " -_"
    ).strip()
    if not safe_title:
        safe_title = "Placeholder Title"
    return safe_title
