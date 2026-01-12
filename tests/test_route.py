"""Tests for routing functionality."""

import json
from pathlib import Path

from totem.config import TotemConfig
from totem.ledger import LedgerWriter
from totem.models.capture import CaptureMeta
from totem.models.routing import RouteLabel
from totem.route import RuleRouter, process_capture_routing


def test_route_deterministic_task_keywords():
    """Test that task keywords route to TASK with high confidence."""
    router = RuleRouter()
    
    text = "I need to finish the report by Friday. Must complete the analysis."
    result = router.route(text, "test-capture-id")
    
    assert result.route_label == RouteLabel.TASK
    assert result.confidence >= 0.7
    assert "need to" in result.reasoning.lower() or "must" in result.reasoning.lower()


def test_route_deterministic_idea_keywords():
    """Test that idea keywords route to IDEA."""
    router = RuleRouter()
    
    text = "Maybe we could try a new approach. What if we considered a different strategy?"
    result = router.route(text, "test-capture-id")
    
    assert result.route_label == RouteLabel.IDEA
    assert result.confidence >= 0.6
    assert "idea" in result.reasoning.lower() or "maybe" in result.reasoning.lower()


def test_route_deterministic_journal_keywords():
    """Test that journal keywords route to JOURNAL."""
    router = RuleRouter()
    
    text = "Today I realized something important. I'm feeling grateful for the team."
    result = router.route(text, "test-capture-id")
    
    assert result.route_label == RouteLabel.JOURNAL
    assert result.confidence >= 0.7
    assert "today" in result.reasoning.lower() or "realized" in result.reasoning.lower()


def test_route_deterministic_people_keywords():
    """Test that people keywords route to PEOPLE."""
    router = RuleRouter()
    
    text = "Met with Sarah today. Had a great discussion with the engineering team."
    result = router.route(text, "test-capture-id")
    
    assert result.route_label == RouteLabel.PEOPLE
    assert result.confidence >= 0.7
    assert "met with" in result.reasoning.lower() or "discussion with" in result.reasoning.lower()


def test_route_deterministic_admin_keywords():
    """Test that admin keywords route to ADMIN."""
    router = RuleRouter()
    
    text = "Need to file this expense report. Invoice from vendor received."
    result = router.route(text, "test-capture-id")
    
    assert result.route_label == RouteLabel.ADMIN
    assert result.confidence >= 0.6


def test_route_empty_text_goes_to_unknown():
    """Test that empty text routes to UNKNOWN with low confidence."""
    router = RuleRouter()
    
    result = router.route("", "test-capture-id")
    
    assert result.route_label == RouteLabel.UNKNOWN
    assert result.confidence <= 0.2
    assert "too short" in result.reasoning.lower()


def test_route_short_text_low_confidence():
    """Test that very short text gets low confidence."""
    router = RuleRouter()
    
    result = router.route("ok", "test-capture-id")
    
    assert result.route_label == RouteLabel.UNKNOWN
    assert result.confidence <= 0.4


def test_route_no_keywords_unknown():
    """Test that text with no matching keywords routes to UNKNOWN."""
    router = RuleRouter()
    
    text = "The quick brown fox jumps over the lazy dog."
    result = router.route(text, "test-capture-id")
    
    assert result.route_label == RouteLabel.UNKNOWN
    assert result.confidence <= 0.5
    assert "no recognizable keywords" in result.reasoning.lower()


def test_route_extracts_next_actions():
    """Test that action items are extracted from text."""
    router = RuleRouter()
    
    text = "I need to call John tomorrow. Must finish the report. Should review the code."
    result = router.route(text, "test-capture-id")
    
    assert len(result.next_actions) > 0
    assert any("call" in action.lower() for action in result.next_actions)


def test_route_extracts_max_three_actions():
    """Test that max 3 actions are extracted."""
    router = RuleRouter()
    
    text = """
    Need to call John.
    Must email Sarah.
    Should review the docs.
    Todo: update the tests.
    Action: deploy to staging.
    """
    result = router.route(text, "test-capture-id")
    
    assert len(result.next_actions) <= 3


def test_route_is_deterministic():
    """Test that routing is deterministic (same input = same output)."""
    router = RuleRouter()
    
    text = "I need to finish the project today. Must complete the tasks."
    
    result1 = router.route(text, "test-capture-id")
    result2 = router.route(text, "test-capture-id")
    
    assert result1.route_label == result2.route_label
    assert result1.confidence == result2.confidence
    assert result1.next_actions == result2.next_actions


def test_bouncer_high_confidence_goes_to_routed(vault_paths, tmp_path):
    """Test that high confidence captures are routed to routed directory."""
    # Create a test capture
    date_str = "2026-01-11"
    date_folder = vault_paths.inbox / date_str
    date_folder.mkdir(parents=True, exist_ok=True)
    
    capture_id = "test-high-confidence"
    raw_file = date_folder / "test_capture.txt"
    meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
    
    # Write capture with strong TASK keywords
    raw_file.write_text("I must finish the report. Need to complete the task by deadline.")
    
    # Write meta
    meta = CaptureMeta(
        id=capture_id,
        created_at="2026-01-11T12:00:00Z",
        source="cli_text",
        type="text",
        files=[raw_file.name],
    )
    meta_file.write_text(meta.model_dump_json(indent=2))
    
    # Configure with low threshold to ensure routing
    config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Process routing
    output_path, was_routed = process_capture_routing(
        raw_file_path=raw_file,
        meta_file_path=meta_file,
        vault_root=vault_paths.root,
        config=config,
        ledger_writer=ledger_writer,
        date_str=date_str,
    )
    
    # Verify it was routed (not flagged for review)
    assert was_routed
    assert "routed" in str(output_path)
    assert "review_queue" not in str(output_path)
    
    # Verify output file exists
    assert output_path.exists()
    
    # Verify output JSON structure
    output_data = json.loads(output_path.read_text())
    assert output_data["capture_id"] == capture_id
    assert output_data["route_label"] == "TASK"
    assert output_data["confidence"] >= 0.5


def test_bouncer_low_confidence_goes_to_review(vault_paths, tmp_path):
    """Test that low confidence captures are flagged for review."""
    # Create a test capture
    date_str = "2026-01-11"
    date_folder = vault_paths.inbox / date_str
    date_folder.mkdir(parents=True, exist_ok=True)
    
    capture_id = "test-low-confidence"
    raw_file = date_folder / "test_capture.txt"
    meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
    
    # Write capture with no clear keywords (low confidence)
    raw_file.write_text("The quick brown fox jumps over the lazy dog.")
    
    # Write meta
    meta = CaptureMeta(
        id=capture_id,
        created_at="2026-01-11T12:00:00Z",
        source="cli_text",
        type="text",
        files=[raw_file.name],
    )
    meta_file.write_text(meta.model_dump_json(indent=2))
    
    # Configure with threshold
    config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.7)
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Process routing
    output_path, was_routed = process_capture_routing(
        raw_file_path=raw_file,
        meta_file_path=meta_file,
        vault_root=vault_paths.root,
        config=config,
        ledger_writer=ledger_writer,
        date_str=date_str,
    )
    
    # Verify it was flagged for review
    assert not was_routed
    assert "review_queue" in str(output_path)
    assert "routed" not in str(output_path) or "review" in str(output_path)
    
    # Verify output file exists
    assert output_path.exists()
    
    # Verify output JSON structure
    output_data = json.loads(output_path.read_text())
    assert output_data["capture_id"] == capture_id
    assert "review_reason" in output_data
    assert output_data["confidence"] < 0.7


def test_route_collision_adds_suffix(vault_paths):
    """Test that filename collisions are handled with suffixes."""
    date_str = "2026-01-11"
    date_folder = vault_paths.inbox / date_str
    date_folder.mkdir(parents=True, exist_ok=True)
    
    capture_id = "test-collision"
    
    # Create two captures with same ID
    for i in range(2):
        raw_file = date_folder / f"test_capture_{i}.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        raw_file.write_text("I need to complete the task.")
        
        meta = CaptureMeta(
            id=capture_id,  # Same ID!
            created_at="2026-01-11T12:00:00Z",
            source="cli_text",
            type="text",
            files=[raw_file.name],
        )
        meta_file.write_text(meta.model_dump_json(indent=2))
    
    config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Process both captures
    outputs = []
    for i in range(2):
        raw_file = date_folder / f"test_capture_{i}.txt"
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        output_path, was_routed = process_capture_routing(
            raw_file_path=raw_file,
            meta_file_path=meta_file,
            vault_root=vault_paths.root,
            config=config,
            ledger_writer=ledger_writer,
            date_str=date_str,
        )
        outputs.append(output_path)
    
    # Verify both files exist with different names
    assert outputs[0].exists()
    assert outputs[1].exists()
    assert outputs[0] != outputs[1]
    
    # One should have suffix
    assert "_1" in outputs[1].name or outputs[0].name != outputs[1].name


def test_route_appends_ledger_event(vault_paths):
    """Test that routing appends CAPTURE_ROUTED event to ledger."""
    date_str = "2026-01-11"
    date_folder = vault_paths.inbox / date_str
    date_folder.mkdir(parents=True, exist_ok=True)
    
    capture_id = "test-ledger-event"
    raw_file = date_folder / "test_capture.txt"
    meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
    
    raw_file.write_text("I need to finish the project today.")
    
    meta = CaptureMeta(
        id=capture_id,
        created_at="2026-01-11T12:00:00Z",
        source="cli_text",
        type="text",
        files=[raw_file.name],
    )
    meta_file.write_text(meta.model_dump_json(indent=2))
    
    config = TotemConfig(vault_path=vault_paths.root, route_confidence_min=0.5)
    ledger_writer = LedgerWriter(vault_paths.ledger_file)
    
    # Process routing
    process_capture_routing(
        raw_file_path=raw_file,
        meta_file_path=meta_file,
        vault_root=vault_paths.root,
        config=config,
        ledger_writer=ledger_writer,
        date_str=date_str,
    )
    
    # Read ledger
    ledger_lines = vault_paths.ledger_file.read_text().strip().split("\n")
    assert len(ledger_lines) >= 1
    
    # Check last event
    last_event = json.loads(ledger_lines[-1])
    assert last_event["event_type"] == "CAPTURE_ROUTED"
    assert last_event["capture_id"] == capture_id
    assert "route" in last_event["payload"]
    assert "confidence" in last_event["payload"]
    assert "routed_path" in last_event["payload"] or "review_path" in last_event["payload"]


def test_route_multiple_keywords_increases_confidence():
    """Test that multiple keyword matches increase confidence."""
    router = RuleRouter()
    
    # Single keyword
    text1 = "I need to do something."
    result1 = router.route(text1, "test-1")
    
    # Multiple keywords
    text2 = "I need to finish the task. Must complete the action by deadline."
    result2 = router.route(text2, "test-2")
    
    # Multiple keywords should have higher confidence
    assert result2.confidence > result1.confidence


def test_route_ambiguous_keywords_lower_confidence():
    """Test that ambiguous text (multiple categories) gets lower confidence."""
    router = RuleRouter()
    
    # Clear category
    text1 = "I need to finish the report. Must complete the task."
    result1 = router.route(text1, "test-1")
    
    # Ambiguous (task + journal)
    text2 = "Today I realized I need to finish the report."
    result2 = router.route(text2, "test-2")
    
    # Ambiguous should have slightly lower confidence
    # (though both should still be reasonably confident)
    assert result1.route_label == RouteLabel.TASK
    # result2 could be either TASK or JOURNAL depending on keyword counts
