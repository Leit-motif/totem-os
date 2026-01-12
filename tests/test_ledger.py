"""Tests for ledger functionality."""

import json
from pathlib import Path

from totem.ledger import LedgerWriter, read_ledger_tail
from totem.models.ledger import LedgerEvent


def test_ledger_append_creates_file(temp_vault):
    """Test that appending to ledger creates the file if it doesn't exist."""
    ledger_path = temp_vault / "90_system" / "ledger.jsonl"
    
    # Ensure file doesn't exist yet
    assert not ledger_path.exists()
    
    # Append an event
    writer = LedgerWriter(ledger_path)
    event = writer.append_event(
        event_type="CAPTURE_INGESTED",
        payload={"test": "data"},
        capture_id="test-capture-id",
    )
    
    # Verify file was created
    assert ledger_path.exists()
    
    # Verify event has required fields
    assert event.event_id
    assert event.run_id
    assert event.ts
    assert event.event_type == "CAPTURE_INGESTED"
    assert event.capture_id == "test-capture-id"
    assert event.payload == {"test": "data"}


def test_ledger_append_multiple_events(vault_paths):
    """Test appending multiple events to ledger."""
    writer = LedgerWriter(vault_paths.ledger_file)
    
    # Append multiple events
    event1 = writer.append_event(
        event_type="CAPTURE_INGESTED",
        payload={"index": 1},
    )
    event2 = writer.append_event(
        event_type="CAPTURE_INGESTED",
        payload={"index": 2},
    )
    event3 = writer.append_event(
        event_type="CAPTURE_INGESTED",
        payload={"index": 3},
    )
    
    # Verify all events have same run_id
    assert event1.run_id == event2.run_id == event3.run_id
    
    # Verify all events have unique event_ids
    assert event1.event_id != event2.event_id
    assert event2.event_id != event3.event_id
    assert event1.event_id != event3.event_id
    
    # Verify file has 3 lines
    lines = vault_paths.ledger_file.read_text().strip().split("\n")
    assert len(lines) == 3
    
    # Verify each line is valid JSON
    for line in lines:
        data = json.loads(line)
        assert "event_id" in data
        assert "run_id" in data
        assert "ts" in data


def test_ledger_tail_reads_last_n(vault_paths):
    """Test reading last N events from ledger."""
    writer = LedgerWriter(vault_paths.ledger_file)
    
    # Append 10 events
    for i in range(10):
        writer.append_event(
            event_type="CAPTURE_INGESTED",
            payload={"index": i},
        )
    
    # Read last 5 events
    events = read_ledger_tail(vault_paths.ledger_file, n=5)
    
    # Verify we got 5 events
    assert len(events) == 5
    
    # Verify they are the last 5 (indices 5-9)
    for i, event in enumerate(events):
        assert event.payload["index"] == i + 5


def test_ledger_tail_handles_malformed(vault_paths, capsys):
    """Test that tail handles malformed lines gracefully."""
    writer = LedgerWriter(vault_paths.ledger_file)
    
    # Append some valid events
    writer.append_event(event_type="CAPTURE_INGESTED", payload={"index": 1})
    writer.append_event(event_type="CAPTURE_INGESTED", payload={"index": 2})
    
    # Append malformed lines directly
    with open(vault_paths.ledger_file, "a") as f:
        f.write("this is not json\n")
        f.write("{\"incomplete\": \n")
    
    # Append more valid events
    writer.append_event(event_type="CAPTURE_INGESTED", payload={"index": 3})
    
    # Read tail
    events = read_ledger_tail(vault_paths.ledger_file, n=10)
    
    # Should have 3 valid events (malformed ones skipped)
    assert len(events) == 3
    assert events[0].payload["index"] == 1
    assert events[1].payload["index"] == 2
    assert events[2].payload["index"] == 3


def test_ledger_tail_empty_file(vault_paths):
    """Test tail on empty ledger file."""
    events = read_ledger_tail(vault_paths.ledger_file, n=10)
    assert events == []


def test_ledger_tail_nonexistent_file(temp_vault):
    """Test tail on nonexistent ledger file."""
    ledger_path = temp_vault / "nonexistent.jsonl"
    events = read_ledger_tail(ledger_path, n=10)
    assert events == []


def test_ledger_event_format(vault_paths):
    """Test that ledger events are properly formatted."""
    writer = LedgerWriter(vault_paths.ledger_file)
    
    event = writer.append_event(
        event_type="CAPTURE_INGESTED",
        payload={"source": "cli_text", "raw_path": "00_inbox/2026-01-11/capture.txt"},
        capture_id="test-capture-123",
    )
    
    # Read the raw JSON from file
    with open(vault_paths.ledger_file, "r") as f:
        line = f.read().strip()
    
    data = json.loads(line)
    
    # Verify all required fields are present
    assert "event_id" in data
    assert "run_id" in data
    assert "ts" in data
    assert "event_type" in data
    assert "capture_id" in data
    assert "payload" in data
    
    # Verify ISO8601 timestamp format with UTC indicator
    assert "T" in data["ts"]
    assert data["ts"].endswith("Z") or "+" in data["ts"] or data["ts"].endswith("+00:00")
    
    # Verify event type
    assert data["event_type"] == "CAPTURE_INGESTED"


def test_ledger_timestamp_is_utc(vault_paths):
    """Test that ledger timestamps are stored as ISO8601 UTC."""
    writer = LedgerWriter(vault_paths.ledger_file)
    
    event = writer.append_event(
        event_type="CAPTURE_INGESTED",
        payload={"test": "data"},
    )
    
    # Read raw line from file
    with open(vault_paths.ledger_file, "r") as f:
        line = f.read().strip()
    
    data = json.loads(line)
    
    # Verify timestamp ends with Z (UTC indicator)
    assert data["ts"].endswith("Z"), f"Timestamp should end with Z: {data['ts']}"
    
    # Verify it's valid ISO8601
    from datetime import datetime
    parsed = datetime.fromisoformat(data["ts"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "Timestamp should include timezone info"
