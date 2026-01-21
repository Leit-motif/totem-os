"""Daily note writer for Omi conversation summaries."""

import re
from pathlib import Path

from ..ledger import LedgerWriter
from ..models.omi import DailyNoteResult, OmiConversation


def _sanitize_action_item(item: str) -> str:
    """Sanitize action item text.
    
    Removes leading bullets, checkboxes, and extra whitespace.
    
    Args:
        item: Raw action item text
        
    Returns:
        Cleaned item text
    """
    # Remove leading bullets, numbers, checkboxes
    # e.g., "- [ ] task", "* task", "1. task", "[ ] task"
    clean = re.sub(r"^(\s*[-*+]|\s*\d+\.|\s*\[[ xX]?\])+\s*", "", item)
    return clean.strip()


def write_daily_note_omi_block(
    conversations: list[OmiConversation],
    date_str: str,
    vault_root: Path,
    ledger_writer: LedgerWriter,
) -> DailyNoteResult:
    """Write or update Omi block in daily note with idempotency.
    
    Creates daily note at: {vault_root}/5.0 Journal/5.1 Daily/YYYY/MM/YYYY-MM-DD.md
    
    Hardening:
    - Sorts conversations by start time
    - Sanitizes action items
    - Detects and cleans up malformed/duplicate blocks (Totem fully owns the block)
    
    Args:
        conversations: List of conversations to aggregate
        date_str: Date string (YYYY-MM-DD)
        vault_root: Root path of Obsidian vault
        ledger_writer: Ledger writer for events
        
    Returns:
        DailyNoteResult with statistics
    """
    # 1. Sort conversations chronologically
    sorted_conversations = sorted(conversations, key=lambda c: c.started_at)
    
    # Parse date for directory structure
    year, month, day = date_str.split("-")
    
    # Create directory structure for daily note
    daily_note_dir = vault_root / "5.0 Journal" / "5.1 Daily" / year / month
    daily_note_dir.mkdir(parents=True, exist_ok=True)
    
    # Daily note file path
    daily_note_path = daily_note_dir / f"{date_str}.md"
    
    # Wikilink to transcript file
    transcript_wikilink = f"[[Omi Transcripts/{year}/{month}/{date_str}]]"
    
    # Aggregate conversation data
    all_overviews = []
    all_action_items = []
    categories = []
    locations = []
    
    for conv in sorted_conversations:
        if conv.overview:
            all_overviews.append(conv.overview)
        if conv.action_items:
            # Sanitize items
            clean_items = [_sanitize_action_item(item) for item in conv.action_items]
            all_action_items.extend(clean_items)
        if conv.category:
            categories.append(conv.category)
        if conv.location:
            locations.append(conv.location)
    
    # Dedup action items (while preserving order)
    unique_action_items = list(dict.fromkeys(all_action_items))
    
    # Build Omi block content
    block_lines = ["<!-- TOTEM:OMI:START -->", "## Omi", ""]
    
    # Add action items section if present (First)
    if unique_action_items:
        block_lines.append("### Omi Action Items (auto)")
        for item in unique_action_items:
            block_lines.append(f"- [ ] {item}")
        block_lines.append("")
    
    # Add overview section if present (Second)
    if all_overviews:
        block_lines.append("### Omi Summary (auto)")
        if len(all_overviews) == 1:
            block_lines.append(all_overviews[0])
        else:
            # Multiple conversations: use bullets
            for overview in all_overviews:
                block_lines.append(f"- {overview}")
        block_lines.append("")
    
    # Add metadata section if present (Third)
    if categories or locations:
        block_lines.append("### Omi Metadata (auto)")
        if categories:
            # Show unique categories
            unique_categories = list(dict.fromkeys(categories))  # Preserve order
            if len(unique_categories) == 1:
                block_lines.append(f"- Category: {unique_categories[0]}")
            else:
                block_lines.append(f"- Categories: {', '.join(unique_categories)}")
        if locations:
            # Show unique locations
            unique_locations = list(dict.fromkeys(locations))
            if len(unique_locations) == 1:
                block_lines.append(f"- Location: {unique_locations[0]}")
            else:
                block_lines.append(f"- Locations: {', '.join(unique_locations)}")
        block_lines.append("")
    
    # Add transcript link at the bottom
    block_lines.append(f"- {transcript_wikilink}")
    block_lines.append("<!-- TOTEM:OMI:END -->")
    omi_block = "\n".join(block_lines)
    
    # Read existing content or create new
    if daily_note_path.exists():
        existing_content = daily_note_path.read_text(encoding="utf-8")
    else:
        # Create new daily note with minimal header
        existing_content = f"# {date_str}\n\n"
    
    # 2. Marker Analysis & Robustness
    start_marker = "<!-- TOTEM:OMI:START -->"
    end_marker = "<!-- TOTEM:OMI:END -->"
    
    start_count = existing_content.count(start_marker)
    end_count = existing_content.count(end_marker)
    
    marker_status = "new"
    block_replaced = False
    malformed = False
    
    if start_count == 0 and end_count == 0:
        # Case A: New block
        new_content = existing_content.rstrip() + "\n\n" + omi_block + "\n"
        marker_status = "new"
        
    elif start_count == 1 and end_count == 1:
        # Case B: Canonical replacement (Normal update)
        # Check order
        start_pos = existing_content.find(start_marker)
        end_pos = existing_content.find(end_marker)
        
        if start_pos < end_pos:
            pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
            new_content = re.sub(pattern, omi_block, existing_content, flags=re.DOTALL)
            marker_status = "existing"
            block_replaced = True
        else:
            # Malformed: end before start? Treat as garbage
            malformed = True
    else:
        # Case C: Multiple or mismatched markers (Recovery)
        malformed = True
        
    if malformed:
        # Recovery strategy: Remove ALL marker debris and append new block
        # Make a regex that matches ANY markers and generic content between them aggresively?
        # Safer: Just remove the marker lines themselves and anything looking like our block?
        # Or simplistic: remove exact marker strings, then append. 
        # Better: Regex substitute all valid-LOOKING blocks first.
        
        marker_status = "recovered"
        block_replaced = True
        
        # Log warning event
        ledger_writer.append_event(
            event_type="OMI_DAILY_NOTE_BLOCK_MALFORMED",
            payload={
                "date": date_str,
                "daily_note_path": str(daily_note_path),
                "start_markers": start_count,
                "end_markers": end_count,
            }
        )
        
        # 1. Remove complete blocks
        pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
        temp_content = re.sub(pattern, "", existing_content, flags=re.DOTALL)
        
        # 2. Remove any orphaned markers remaining
        temp_content = temp_content.replace(start_marker, "").replace(end_marker, "")
        
        # 3. Clean up excessive newlines we might have left
        temp_content = re.sub(r"\n{3,}", "\n\n", temp_content)
        
        # 4. Append canonical block
        new_content = temp_content.rstrip() + "\n\n" + omi_block + "\n"

    # Write to file
    daily_note_path.write_text(new_content, encoding="utf-8")
    
    # Create result
    result = DailyNoteResult(
        date=date_str,
        daily_note_path=daily_note_path,
        transcript_wikilink=transcript_wikilink,
        conversations_count=len(conversations),
        action_items_count=len(unique_action_items),
        marker_status=marker_status,
        block_replaced=block_replaced,
    )
    
    # Write ledger event
    ledger_writer.append_event(
        event_type="OMI_DAILY_NOTE_WRITTEN",
        payload={
            "date": date_str,
            "daily_note_path": str(daily_note_path),
            "transcript_wikilink": transcript_wikilink,
            "conversations_count": len(conversations),
            "action_items_count": len(unique_action_items),
            "marker_status": marker_status,
            "block_replaced": block_replaced,
        },
    )
    
    return result
