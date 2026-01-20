"""Markdown writer for Omi transcripts with idempotency."""

import re
from datetime import datetime
from pathlib import Path

from ..ledger import LedgerWriter
from ..models.omi import OmiConversation, OmiSyncResult


def write_transcripts_to_vault(
    conversations: list[OmiConversation],
    date_str: str,
    vault_root: Path,
    ledger_writer: LedgerWriter,
) -> OmiSyncResult:
    """Write Omi transcripts to Obsidian vault with idempotency.
    
    Creates markdown file at: {vault_root}/Omi Transcripts/YYYY/MM/YYYY-MM-DD.md
    
    Idempotency: Reads existing file and skips segments already present
    (identified by HTML comment with seg_id).
    
    Args:
        conversations: List of conversations to write
        date_str: Date string (YYYY-MM-DD)
        vault_root: Root path of Obsidian vault
        ledger_writer: Ledger writer for events
        
    Returns:
        OmiSyncResult with statistics
    """
    # Parse date for directory structure
    year, month, day = date_str.split("-")
    
    # Create directory structure
    transcript_dir = vault_root / "Omi Transcripts" / year / month
    transcript_dir.mkdir(parents=True, exist_ok=True)
    
    # File path
    file_path = transcript_dir / f"{date_str}.md"
    
    # Read existing file to find existing segment IDs
    existing_seg_ids = set()
    if file_path.exists():
        existing_content = file_path.read_text(encoding="utf-8")
        existing_seg_ids = _extract_segment_ids(existing_content)
    
    # Sort conversations by started_at
    sorted_conversations = sorted(conversations, key=lambda c: c.started_at)
    
    # Build markdown content
    lines = []
    
    # Add header if file doesn't exist
    if not file_path.exists():
        lines.append(f"# Omi Transcripts — {date_str}\n")
    
    # Process each conversation
    segments_written = 0
    segments_skipped = 0
    
    for conv in sorted_conversations:
        # Format timestamps
        start_time = conv.started_at.strftime("%H:%M:%S")
        end_time = conv.finished_at.strftime("%H:%M:%S")
        
        # Add conversation header
        lines.append(f"\n## Conversation {conv.id} ({start_time}–{end_time})\n")
        
        # Add transcript segments
        for segment in conv.transcript:
            # Check if segment already exists
            if segment.segment_id in existing_seg_ids:
                segments_skipped += 1
                continue
            
            # Add segment
            lines.append(f"- [speaker {segment.speaker_id}] {segment.text}")
            lines.append(f"<!-- conv_id: {conv.id} -->")
            lines.append(f"<!-- seg_id: {segment.segment_id} -->\n")
            
            segments_written += 1
    
    # Write to file (append mode if exists, write mode if new)
    if file_path.exists():
        with open(file_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    else:
        file_path.write_text("\n".join(lines), encoding="utf-8")
    
    # Create result
    result = OmiSyncResult(
        date=date_str,
        conversations_count=len(conversations),
        segments_written=segments_written,
        segments_skipped=segments_skipped,
        file_path=file_path,
    )
    
    # Write ledger event
    ledger_writer.append_event(
        event_type="OMI_TRANSCRIPT_WRITTEN",
        payload={
            "date": date_str,
            "file_path": str(file_path),
            "segments_written": segments_written,
            "segments_skipped": segments_skipped,
            "conversations_count": len(conversations),
        },
    )
    
    return result


def _extract_segment_ids(content: str) -> set[str]:
    """Extract segment IDs from existing markdown content.
    
    Parses HTML comments like: <!-- seg_id: abc123 -->
    
    Args:
        content: Markdown file content
        
    Returns:
        Set of segment IDs found in content
    """
    pattern = r"<!-- seg_id: (.+?) -->"
    matches = re.findall(pattern, content)
    return set(matches)
