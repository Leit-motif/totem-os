"""Tests for capture functionality."""

import json
from pathlib import Path

from totem.capture import ingest_file_capture, ingest_text_capture
from totem.ledger import LedgerWriter
from totem.models.capture import CaptureMeta


def test_capture_text_creates_raw_meta_and_event(vault_paths):
    """Test that text capture creates raw file, meta file, and ledger event."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Ingest text capture
    raw_path, meta_path, capture_id = ingest_text_capture(
        vault_inbox=vault_paths.inbox,
        text="This is a test capture",
        ledger_writer=ledger_writer,
        date_str="2026-01-11",
    )
    
    # Verify raw file was created
    assert raw_path.exists()
    assert raw_path.read_text() == "This is a test capture"
    
    # Verify meta file was created
    assert meta_path.exists()
    meta_data = json.loads(meta_path.read_text())
    assert meta_data["id"] == capture_id
    assert meta_data["source"] == "cli_text"
    assert meta_data["type"] == "text"
    assert len(meta_data["files"]) == 1
    
    # Verify ledger event was appended
    ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
    assert len(ledger_lines) == 1
    
    event_data = json.loads(ledger_lines[0])
    assert event_data["event_type"] == "CAPTURE_INGESTED"
    assert event_data["capture_id"] == capture_id
    assert "raw_path" in event_data["payload"]
    assert "meta_path" in event_data["payload"]


def test_capture_text_writes_to_date_subfolder(vault_paths):
    """Test that captures are written to date-specific subfolders."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    raw_path, meta_path, capture_id = ingest_text_capture(
        vault_inbox=vault_paths.inbox,
        text="Test",
        ledger_writer=ledger_writer,
        date_str="2026-01-11",
    )
    
    # Verify files are in date subfolder
    assert "2026-01-11" in str(raw_path)
    assert raw_path.parent.name == "2026-01-11"


def test_capture_file_copies_and_logs(vault_paths, tmp_path):
    """Test that file capture copies file and logs to ledger."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Create a source file
    source_file = tmp_path / "test_document.txt"
    source_file.write_text("Source file content")
    
    # Ingest file capture
    raw_path, meta_path, capture_id = ingest_file_capture(
        vault_inbox=vault_paths.inbox,
        source_file_path=source_file,
        ledger_writer=ledger_writer,
        date_str="2026-01-11",
    )
    
    # Verify file was copied
    assert raw_path.exists()
    assert raw_path.read_text() == "Source file content"
    assert "test_document" in raw_path.name
    
    # Verify meta file was created
    assert meta_path.exists()
    meta_data = json.loads(meta_path.read_text())
    assert meta_data["source"] == "cli_file"
    assert meta_data["type"] == "text"
    assert meta_data["origin"]["original_path"] == str(source_file.absolute())
    
    # Verify ledger event
    ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
    event_data = json.loads(ledger_lines[0])
    assert event_data["event_type"] == "CAPTURE_INGESTED"
    assert event_data["payload"]["source"] == "cli_file"


def test_capture_collision_creates_distinct_files(vault_paths):
    """Test that filename collisions are handled with suffixes."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Create first capture
    date_folder = vault_paths.inbox / "2026-01-11"
    date_folder.mkdir(parents=True, exist_ok=True)
    
    # Manually create a file that will cause collision
    existing_file = date_folder / "capture_20260111_120000.txt"
    existing_file.write_text("existing")
    
    # Mock the timestamp generation by directly creating the collision scenario
    # We'll use the ingest function but expect it to handle collision
    from unittest.mock import patch
    from datetime import datetime, timezone
    
    fixed_time = datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc)
    
    with patch("totem.capture.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_time
        mock_datetime.strftime = datetime.strftime
        
        # Ingest capture (should create _1 suffix)
        raw_path, meta_path, capture_id = ingest_text_capture(
            vault_inbox=vault_paths.inbox,
            text="New capture",
            ledger_writer=ledger_writer,
            date_str="2026-01-11",
        )
    
    # Verify new file has suffix
    assert raw_path.exists()
    assert "_1" in raw_path.name
    assert raw_path.read_text() == "New capture"
    
    # Verify original file unchanged
    assert existing_file.read_text() == "existing"


def test_capture_meta_json_structure(vault_paths):
    """Test that CaptureMeta JSON structure is correct."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    raw_path, meta_path, capture_id = ingest_text_capture(
        vault_inbox=vault_paths.inbox,
        text="Test",
        ledger_writer=ledger_writer,
        date_str="2026-01-11",
    )
    
    # Parse meta file
    meta_data = json.loads(meta_path.read_text())
    
    # Validate against Pydantic model
    meta = CaptureMeta(**meta_data)
    
    # Verify required fields
    assert meta.id
    assert meta.created_at
    assert meta.source in ["cli_text", "cli_file", "manual", "chatgpt_export", "other"]
    assert meta.type in ["text", "markdown", "audio", "image", "pdf", "json", "other"]
    assert isinstance(meta.files, list)
    assert len(meta.files) > 0


def test_capture_file_nonexistent_source(vault_paths, tmp_path):
    """Test that capturing nonexistent file raises error."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    nonexistent_file = tmp_path / "does_not_exist.txt"
    
    try:
        ingest_file_capture(
            vault_inbox=vault_paths.inbox,
            source_file_path=nonexistent_file,
            ledger_writer=ledger_writer,
            date_str="2026-01-11",
        )
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass  # Expected


def test_capture_infers_content_type(vault_paths, tmp_path):
    """Test that content type is inferred from file extension."""
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Test various file types
    test_files = [
        ("test.md", "markdown"),
        ("test.pdf", "pdf"),
        ("test.mp3", "audio"),
        ("test.jpg", "image"),
        ("test.json", "json"),
        ("test.xyz", "other"),
    ]
    
    for filename, expected_type in test_files:
        source_file = tmp_path / filename
        source_file.write_text("content")
        
        raw_path, meta_path, capture_id = ingest_file_capture(
            vault_inbox=vault_paths.inbox,
            source_file_path=source_file,
            ledger_writer=ledger_writer,
            date_str="2026-01-11",
        )
        
        meta_data = json.loads(meta_path.read_text())
        assert meta_data["type"] == expected_type, f"Failed for {filename}"
