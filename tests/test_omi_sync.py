"""Tests for Omi transcript sync integration."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from totem.ledger import LedgerWriter
from totem.models.omi import OmiConversation, OmiTranscriptSegment
from totem.omi.writer import write_transcripts_to_vault
from totem.omi.trace import write_sync_trace
from totem.paths import VaultPaths


@pytest.fixture
def sample_conversations():
    """Create sample conversations for testing."""
    return [
        OmiConversation(
            id="conv_1",
            started_at=datetime(2026, 1, 19, 10, 0, 0),
            finished_at=datetime(2026, 1, 19, 10, 15, 0),
            transcript=[
                OmiTranscriptSegment(
                    segment_id="seg_1",
                    speaker_id="SPEAKER_00",
                    text="Hello, this is the first segment.",
                ),
                OmiTranscriptSegment(
                    segment_id="seg_2",
                    speaker_id="SPEAKER_01",
                    text="And this is the second segment.",
                ),
            ],
        ),
        OmiConversation(
            id="conv_2",
            started_at=datetime(2026, 1, 19, 14, 0, 0),
            finished_at=datetime(2026, 1, 19, 14, 30, 0),
            transcript=[
                OmiTranscriptSegment(
                    segment_id="seg_3",
                    speaker_id="SPEAKER_00",
                    text="Third segment from second conversation.",
                ),
            ],
        ),
    ]


def test_write_transcripts_creates_file(tmp_path, vault_paths, sample_conversations):
    """Test that write_transcripts_to_vault creates markdown file."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    result = write_transcripts_to_vault(
        conversations=sample_conversations,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    # Verify file was created
    expected_path = obsidian_vault / "Omi Transcripts" / "2026" / "01" / "2026-01-19.md"
    assert expected_path.exists()
    
    # Verify result
    assert result.date == "2026-01-19"
    assert result.conversations_count == 2
    assert result.segments_written == 3
    assert result.segments_skipped == 0
    assert result.file_path == expected_path


def test_write_transcripts_markdown_format(tmp_path, vault_paths, sample_conversations):
    """Test that markdown is formatted correctly."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    result = write_transcripts_to_vault(
        conversations=sample_conversations,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    content = result.file_path.read_text(encoding="utf-8")
    
    # Verify header
    assert "# Omi Transcripts — 2026-01-19" in content
    
    # Verify conversation headers
    assert "## Conversation conv_1 (10:00:00–10:15:00)" in content
    assert "## Conversation conv_2 (14:00:00–14:30:00)" in content
    
    # Verify segments
    assert "- [speaker SPEAKER_00] Hello, this is the first segment." in content
    assert "- [speaker SPEAKER_01] And this is the second segment." in content
    
    # Verify HTML comments
    assert "<!-- conv_id: conv_1 -->" in content
    assert "<!-- seg_id: seg_1 -->" in content
    assert "<!-- seg_id: seg_2 -->" in content
    assert "<!-- seg_id: seg_3 -->" in content


def test_write_transcripts_idempotency(tmp_path, vault_paths, sample_conversations):
    """Test that re-running sync doesn't create duplicates."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # First write
    result1 = write_transcripts_to_vault(
        conversations=sample_conversations,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    assert result1.segments_written == 3
    assert result1.segments_skipped == 0
    
    # Second write (same conversations)
    result2 = write_transcripts_to_vault(
        conversations=sample_conversations,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    # Verify no duplicates
    assert result2.segments_written == 0
    assert result2.segments_skipped == 3
    
    # Verify file content hasn't duplicated
    content = result2.file_path.read_text(encoding="utf-8")
    assert content.count("<!-- seg_id: seg_1 -->") == 1
    assert content.count("<!-- seg_id: seg_2 -->") == 1
    assert content.count("<!-- seg_id: seg_3 -->") == 1


def test_write_transcripts_partial_update(tmp_path, vault_paths):
    """Test that new segments are added while existing ones are skipped."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # First conversation
    conv1 = [
        OmiConversation(
            id="conv_1",
            started_at=datetime(2026, 1, 19, 10, 0, 0),
            finished_at=datetime(2026, 1, 19, 10, 15, 0),
            transcript=[
                OmiTranscriptSegment(
                    segment_id="seg_1",
                    speaker_id="SPEAKER_00",
                    text="First segment.",
                ),
            ],
        ),
    ]
    
    # Write first conversation
    result1 = write_transcripts_to_vault(
        conversations=conv1,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    assert result1.segments_written == 1
    
    # Second conversation with new segment
    conv2 = [
        OmiConversation(
            id="conv_1",
            started_at=datetime(2026, 1, 19, 10, 0, 0),
            finished_at=datetime(2026, 1, 19, 10, 15, 0),
            transcript=[
                OmiTranscriptSegment(
                    segment_id="seg_1",  # Existing
                    speaker_id="SPEAKER_00",
                    text="First segment.",
                ),
                OmiTranscriptSegment(
                    segment_id="seg_2",  # New
                    speaker_id="SPEAKER_01",
                    text="Second segment.",
                ),
            ],
        ),
    ]
    
    # Write with new segment
    result2 = write_transcripts_to_vault(
        conversations=conv2,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    # Verify only new segment was written
    assert result2.segments_written == 1
    assert result2.segments_skipped == 1
    
    # Verify both segments are in file
    content = result2.file_path.read_text(encoding="utf-8")
    assert "<!-- seg_id: seg_1 -->" in content
    assert "<!-- seg_id: seg_2 -->" in content


def test_write_transcripts_empty_conversation_list(tmp_path, vault_paths):
    """Test handling empty conversation list."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    result = write_transcripts_to_vault(
        conversations=[],
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    # File should still be created with header
    assert result.file_path.exists()
    assert result.conversations_count == 0
    assert result.segments_written == 0


def test_write_transcripts_logs_ledger_event(tmp_path, vault_paths, sample_conversations):
    """Test that ledger event is written."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    write_transcripts_to_vault(
        conversations=sample_conversations,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    # Read ledger
    ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
    assert len(ledger_lines) == 1
    
    event = json.loads(ledger_lines[0])
    assert event["event_type"] == "OMI_TRANSCRIPT_WRITTEN"
    assert event["payload"]["date"] == "2026-01-19"
    assert event["payload"]["segments_written"] == 3
    assert event["payload"]["segments_skipped"] == 0


def test_write_sync_trace(vault_paths, tmp_path):
    """Test that sync trace is written correctly."""
    from totem.models.omi import OmiSyncResult
    
    sync_result = OmiSyncResult(
        date="2026-01-19",
        conversations_count=2,
        segments_written=5,
        segments_skipped=1,
        file_path=Path("/path/to/file.md"),
    )
    
    start_time = datetime(2026, 1, 19, 10, 0, 0)
    end_time = datetime(2026, 1, 19, 10, 0, 5)
    
    trace_path = write_sync_trace(
        sync_result=sync_result,
        run_id="test_run_123",
        vault_paths=vault_paths,
        date_str="2026-01-19",
        start_time=start_time,
        end_time=end_time,
        api_endpoint="https://api.omi.me/v1/dev/user/conversations",
        api_params={"start_date": "2026-01-19T00:00:00", "end_date": "2026-01-19T23:59:59"},
        conversation_ids=["conv_1", "conv_2"],
    )
    
    # Verify trace file exists with new naming convention
    assert trace_path.exists()
    assert trace_path.name == "omi_sync_test_run_123.json"
    
    # Verify trace content
    trace_data = json.loads(trace_path.read_text())
    assert trace_data["run_id"] == "test_run_123"
    assert trace_data["date"] == "2026-01-19"
    assert trace_data["conversation_ids"] == ["conv_1", "conv_2"]
    assert trace_data["segments_count"] == 6  # 5 written + 1 skipped
    assert trace_data["api_request"]["endpoint"] == "https://api.omi.me/v1/dev/user/conversations"
    assert trace_data["sync_result"]["segments_written"] == 5
    assert trace_data["sync_result"]["segments_skipped"] == 1
    assert trace_data["duration_ms"] == 5000


def test_conversations_sorted_by_started_at(tmp_path, vault_paths):
    """Test that conversations are sorted by started_at timestamp."""
    obsidian_vault = tmp_path / "obsidian"
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Create conversations in reverse chronological order
    conversations = [
        OmiConversation(
            id="conv_2",
            started_at=datetime(2026, 1, 19, 14, 0, 0),
            finished_at=datetime(2026, 1, 19, 14, 15, 0),
            transcript=[],
        ),
        OmiConversation(
            id="conv_1",
            started_at=datetime(2026, 1, 19, 10, 0, 0),
            finished_at=datetime(2026, 1, 19, 10, 15, 0),
            transcript=[],
        ),
    ]
    
    result = write_transcripts_to_vault(
        conversations=conversations,
        date_str="2026-01-19",
        vault_root=obsidian_vault,
        ledger_writer=ledger_writer,
    )
    
    content = result.file_path.read_text(encoding="utf-8")
    
    # Verify conv_1 appears before conv_2 in file
    conv1_pos = content.find("Conversation conv_1")
    conv2_pos = content.find("Conversation conv_2")
    assert conv1_pos < conv2_pos
