"""Tests for Daily Note Omi Block integration."""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from totem.ledger import LedgerWriter
from totem.models.omi import OmiConversation, OmiTranscriptSegment
from totem.omi.daily_note import write_daily_note_omi_block


@pytest.fixture
def sample_conversations_with_metadata():
    """Create sample conversations with metadata."""
    return [
        OmiConversation(
            id="conv_1",
            started_at=datetime(2026, 1, 19, 10, 0, 0),
            finished_at=datetime(2026, 1, 19, 10, 15, 0),
            transcript=[
                OmiTranscriptSegment(
                    segment_id="seg_1",
                    speaker_id="SPEAKER_00",
                    text="Hello",
                ),
            ],
            overview="Initial planning meeting.",
            action_items=["Review PR", "Deploy to staging"],
            category="Work",
            location="San Francisco",
        ),
        OmiConversation(
            id="conv_2",
            started_at=datetime(2026, 1, 19, 14, 0, 0),
            finished_at=datetime(2026, 1, 19, 14, 30, 0),
            transcript=[
                OmiTranscriptSegment(
                    segment_id="seg_2",
                    speaker_id="SPEAKER_00",
                    text="Status update",
                ),
            ],
            overview="Afternoon sync.",
            action_items=["Email client"],
            category="Work",  # Duplicate category to test dedup
            location="Home",
        ),
    ]


def test_write_daily_note_creates_file_if_missing(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test that daily note is created if it doesn't exist."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    result = write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    # Check path construction
    expected_path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    assert result.daily_note_path == expected_path
    assert expected_path.exists()
    
    # Check content
    content = expected_path.read_text(encoding="utf-8")
    assert "# 2026-01-19" in content
    assert "<!-- TOTEM:OMI:START -->" in content
    assert "## Omi" in content
    assert "[[Omi Transcripts/2026/01/2026-01-19]]" in content


def test_write_daily_note_content_format(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test the structure and content of the inserted Omi block."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    content = path.read_text(encoding="utf-8")
    
    # Verify aggregation and Order (Actions -> Summary -> Metadata -> Link)
    
    action_pos = content.find("### Omi Action Items (auto)")
    summary_pos = content.find("### Omi Summary (auto)")
    metadata_pos = content.find("### Omi Metadata (auto)")
    link_pos = content.find("[[Omi Transcripts/2026/01/2026-01-19]]")
    
    assert action_pos != -1
    assert summary_pos != -1
    assert metadata_pos != -1
    assert link_pos != -1
    
    # Assert Order
    assert action_pos < summary_pos
    assert summary_pos < metadata_pos
    assert metadata_pos < link_pos
    
    # Action items section
    assert "- [ ] Review PR" in content
    assert "- [ ] Deploy to staging" in content
    assert "- [ ] Email client" in content

    # Summary section
    assert "- Initial planning meeting." in content
    assert "- Afternoon sync." in content
    
    # Metadata section
    assert "- Category: Work" in content
    assert "- Locations: San Francisco, Home" in content


def test_write_daily_note_idempotency(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test that re-running updates correctly without duplication."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # First write
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    # Modify existing content to simulate user edits outside block
    path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    original_content = path.read_text(encoding="utf-8")
    path.write_text("User header\n\n" + original_content + "\nUser footer", encoding="utf-8")
    
    # Second write (update)
    # Change metadata to verify update
    sample_conversations_with_metadata[0].action_items.append("New Item")
    
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    content = path.read_text(encoding="utf-8")
    
    # Verify markers still exist exactly once
    assert content.count("<!-- TOTEM:OMI:START -->") == 1
    assert content.count("<!-- TOTEM:OMI:END -->") == 1
    
    # Verify update happened
    assert "- [ ] New Item" in content
    
    # Verify user content preserved
    assert "User header" in content
    assert "User footer" in content


def test_write_daily_note_handles_missing_files(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test behavior when creating intermediate directories."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Ensure parents don't exist
    daily_dir = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01"
    assert not daily_dir.exists()
    
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    assert daily_dir.exists()


def test_daily_note_missing_metadata(tmp_path, vault_paths):
    """Test that sections are omitted if metadata missing."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Conversation with NO metadata
    conv = OmiConversation(
        id="conv_minimal",
        started_at=datetime(2026, 1, 19, 10, 0, 0),
        finished_at=datetime(2026, 1, 19, 10, 15, 0),
        transcript=[],
        # All optional fields None/empty
    )
    
    write_daily_note_omi_block(
        conversations=[conv],
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    content = path.read_text(encoding="utf-8")
    
    assert "## Omi" in content
    assert "[[Omi Transcripts/2026/01/2026-01-19]]" in content
    
    # Should NOT have these headers
    assert "### Omi Summary" not in content
    assert "### Omi Action Items" not in content
    assert "### Omi Metadata" not in content


def test_write_daily_note_sorting(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test that conversations are aggregated in chronological order."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Reverse order
    reversed_convs = list(reversed(sample_conversations_with_metadata))
    
    write_daily_note_omi_block(
        conversations=reversed_convs,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    content = path.read_text(encoding="utf-8")
    
    # Verify chronological order
    pos_morning = content.find("Initial planning meeting")
    pos_afternoon = content.find("Afternoon sync")
    assert pos_morning < pos_afternoon


def test_write_daily_note_sanitization(tmp_path, vault_paths):
    """Test that action items are sanitized (bullets removed)."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    conv = OmiConversation(
        id="conv_messy",
        started_at=datetime(2026, 1, 19, 10, 0, 0),
        finished_at=datetime(2026, 1, 19, 10, 15, 0),
        transcript=[],
        action_items=[
            "- [ ] Item 1",
            "* Item 2",
            "3. Item 3",
            "Normal item",
        ]
    )
    
    write_daily_note_omi_block(
        conversations=[conv],
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    content = path.read_text(encoding="utf-8")
    
    # Check that we don't have double checkboxes or weird bullets
    # Expected: "- [ ] Item 1", "- [ ] Item 2", "- [ ] Item 3", "- [ ] Normal item"
    assert "- [ ] Item 1" in content
    assert "- [ ] - [ ] Item 1" not in content
    assert "- [ ] Item 2" in content
    assert "- [ ] Item 3" in content
    assert "- [ ] Normal item" in content


def test_user_edit_overwrite(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test that user edits INSIDE the block are overwritten (Totem owns the block)."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # First write
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    path = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01" / "2026-01-19.md"
    content = path.read_text(encoding="utf-8")
    
    # User modifies inside block
    modified = content.replace("Initial planning meeting", "USER CHANGED THIS")
    path.write_text(modified, encoding="utf-8")
    
    # Second write
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    new_content = path.read_text(encoding="utf-8")
    
    # Verify user change is gone
    assert "USER CHANGED THIS" not in new_content
    assert "Initial planning meeting" in new_content


def test_malformed_marker_recovery(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test recovery from duplicate/malformed blocks."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Create file with duplicate blocks
    daily_dir = obsidian_vault / "5.0 Journal" / "5.1 Daily" / "2026" / "01"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / "2026-01-19.md"
    
    malformed_content = """# Header
    
<!-- TOTEM:OMI:START -->
Old block 1
<!-- TOTEM:OMI:END -->

Some user text

<!-- TOTEM:OMI:START -->
Old block 2
<!-- TOTEM:OMI:END -->
"""
    path.write_text(malformed_content, encoding="utf-8")
    
    # Write
    result = write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    content = path.read_text(encoding="utf-8")
    
    # Verify recovery
    assert result.marker_status == "recovered"
    assert content.count("<!-- TOTEM:OMI:START -->") == 1
    assert content.count("<!-- TOTEM:OMI:END -->") == 1
    assert "Old block 1" not in content
    assert "Old block 2" not in content
    assert "Some user text" in content  # User text preserved
    
    # Verify ledger warning
    ledger_content = vault_paths.ledger_file.read_text()
    assert "OMI_DAILY_NOTE_BLOCK_MALFORMED" in ledger_content


def test_ledger_event_emitted(tmp_path, vault_paths, sample_conversations_with_metadata):
    """Test that correct ledger event is emitted with diagnostics."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    write_daily_note_omi_block(
        conversations=sample_conversations_with_metadata,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
        include_action_items=True,
    )
    
    # Read ledger
    ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
    assert len(ledger_lines) == 1
    
    event = json.loads(ledger_lines[0])
    assert event["event_type"] == "OMI_DAILY_NOTE_WRITTEN"
    payload = event["payload"]
    assert payload["date"] == "2026-01-19"
    assert "transcript_wikilink" in payload
    assert payload["conversations_count"] == 2
    assert payload["action_items_count"] == 3
    assert payload["marker_status"] == "new"
    assert payload["block_replaced"] is False
