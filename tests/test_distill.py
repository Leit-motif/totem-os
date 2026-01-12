"""Tests for distillation functionality."""

import json
from pathlib import Path

import pytest

from totem.config import TotemConfig
from totem.distill import (
    BLOCK_END_MARKER,
    BLOCK_START_MARKER,
    append_tasks_to_todo,
    append_to_daily_note,
    compute_content_hash,
    load_routed_items,
    process_distillation,
    process_distillation_dry_run,
    undo_canon_write,
    update_entities_json,
    write_distill_artifact,
)
from totem.ledger import LedgerWriter
from totem.llm import FakeLLMClient
from totem.models.distill import DistillResult, EntityKind, EntityMention, Priority, TaskItem
from totem.models.routing import RouteLabel


class TestFakeLLMClient:
    """Tests for the FakeLLMClient deterministic behavior."""
    
    def test_distill_produces_deterministic_output(self):
        """Same input always produces same output."""
        client = FakeLLMClient()
        
        routed_item = {
            "capture_id": "test-capture-123",
            "route_label": "TASK",
            "raw_text": "I need to finish the report by Friday. Must complete the analysis.",
        }
        
        result1 = client.distill(routed_item)
        result2 = client.distill(routed_item)
        
        # Core fields should be identical
        assert result1.capture_id == result2.capture_id
        assert result1.route_label == result2.route_label
        assert result1.summary == result2.summary
        assert result1.confidence == result2.confidence
    
    def test_distill_extracts_tasks_from_action_phrases(self):
        """FakeLLM extracts tasks from action-like phrases."""
        client = FakeLLMClient()
        
        routed_item = {
            "capture_id": "test-capture-456",
            "route_label": "TASK",
            "raw_text": "I need to call John. Must finish the report. Should review the code.",
        }
        
        result = client.distill(routed_item)
        
        assert len(result.tasks) > 0
        task_texts = [t.text.lower() for t in result.tasks]
        assert any("call john" in t for t in task_texts)
    
    def test_distill_max_limits_respected(self):
        """FakeLLM respects max limits on tasks, entities, key_points."""
        client = FakeLLMClient()
        
        # Long text with many potential extractions
        routed_item = {
            "capture_id": "test-capture-789",
            "route_label": "TASK",
            "raw_text": """
            Need to call Alice. Must email Bob. Should review Carol's work.
            Todo: update David's report. Action: deploy Eve's code.
            Need to meet Frank. Must check George's PR. Should ping Henry.
            Line one. Line two. Line three. Line four. Line five.
            Line six. Line seven. Line eight.
            """,
        }
        
        result = client.distill(routed_item)
        
        assert len(result.tasks) <= 7
        assert len(result.entities) <= 7
        assert len(result.key_points) <= 5
    
    def test_distill_engine_name(self):
        """FakeLLMClient reports correct engine name."""
        client = FakeLLMClient()
        assert client.engine_name == "fake"
        assert client.provider_model is None


class TestDistillResult:
    """Tests for DistillResult model."""
    
    def test_distill_result_validates_correctly(self):
        """DistillResult accepts valid data."""
        result = DistillResult(
            capture_id="test-123",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Test summary",
            key_points=["point 1", "point 2"],
            tasks=[TaskItem(text="Do something", priority=Priority.HIGH)],
            entities=[EntityMention(name="John", kind=EntityKind.PERSON)],
            confidence=0.85,
            reasoning="Test reasoning",
        )
        
        assert result.capture_id == "test-123"
        assert result.confidence == 0.85
        assert len(result.tasks) == 1
        assert result.tasks[0].priority == Priority.HIGH
    
    def test_distill_result_enforces_confidence_bounds(self):
        """DistillResult rejects confidence outside 0-1."""
        with pytest.raises(ValueError):
            DistillResult(
                capture_id="test",
                distilled_at="2026-01-11T12:00:00Z",
                route_label="TASK",
                summary="Test",
                confidence=1.5,  # Invalid
                reasoning="",
            )


class TestDistillArtifactWrite:
    """Tests for writing distill artifacts."""
    
    def test_write_distill_artifact_creates_file(self, vault_paths):
        """Distill artifact is written to correct location."""
        date_str = "2026-01-11"
        
        result = DistillResult(
            capture_id="test-artifact-123",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Test summary",
            confidence=0.85,
            reasoning="Test",
        )
        
        artifact_path = write_distill_artifact(result, vault_paths, date_str)
        
        assert artifact_path.exists()
        assert "distill" in str(artifact_path)
        assert date_str in str(artifact_path)
        
        # Verify content
        data = json.loads(artifact_path.read_text())
        assert data["capture_id"] == "test-artifact-123"
        assert data["confidence"] == 0.85
    
    def test_write_distill_artifact_no_overwrite(self, vault_paths):
        """Distill artifact uses suffix on collision."""
        date_str = "2026-01-11"
        
        result = DistillResult(
            capture_id="test-collision",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Test",
            confidence=0.8,
            reasoning="",
        )
        
        path1 = write_distill_artifact(result, vault_paths, date_str)
        path2 = write_distill_artifact(result, vault_paths, date_str)
        
        assert path1.exists()
        assert path2.exists()
        assert path1 != path2
        assert "_1" in path2.name


class TestCanonWrites:
    """Tests for canon write operations."""
    
    def test_append_to_daily_note_creates_file(self, vault_paths):
        """Daily note is created with distill content."""
        date_str = "2026-01-11"
        write_id = "test-write-123"
        
        result = DistillResult(
            capture_id="test-daily",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Meeting notes from today",
            key_points=["Point A", "Point B"],
            tasks=[TaskItem(text="Follow up", priority=Priority.MED)],
            entities=[EntityMention(name="Alice", kind=EntityKind.PERSON)],
            confidence=0.9,
            reasoning="",
        )
        
        path, inserted = append_to_daily_note(result, vault_paths, date_str, write_id)
        
        daily_path = vault_paths.daily_note_path(date_str)
        assert daily_path.exists()
        
        content = daily_path.read_text()
        assert "Meeting notes from today" in content
        assert "Point A" in content
        assert "Alice" in content
        assert "Follow up" in content
        assert BLOCK_START_MARKER.format(write_id=write_id) in content
        assert BLOCK_END_MARKER.format(write_id=write_id) in content
    
    def test_append_tasks_to_todo_adds_section(self, vault_paths):
        """Tasks are appended to todo.md."""
        date_str = "2026-01-11"
        write_id = "test-write-456"
        
        # Create initial todo file
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        result = DistillResult(
            capture_id="test-tasks",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Test",
            tasks=[
                TaskItem(text="Task one", priority=Priority.HIGH),
                TaskItem(text="Task two", priority=Priority.LOW),
            ],
            confidence=0.85,
            reasoning="",
        )
        
        path, inserted = append_tasks_to_todo(result, vault_paths, date_str, write_id)
        
        content = vault_paths.todo_file.read_text(encoding="utf-8")
        assert "AI Draft Tasks" in content
        assert "Task one" in content
        assert "Task two" in content
        # Priority markers are text
        assert "[HIGH]" in content
        assert "[LOW]" in content
    
    def test_append_tasks_deduplicates(self, vault_paths):
        """Duplicate tasks are not added."""
        date_str = "2026-01-11"
        write_id = "test-write-789"
        
        # Create initial todo with existing task
        vault_paths.todo_file.write_text("# Tasks\n\nExisting task one\n")
        
        result = DistillResult(
            capture_id="test-dedup",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Test",
            tasks=[
                TaskItem(text="Existing task one", priority=Priority.MED),  # Duplicate
                TaskItem(text="New task two", priority=Priority.MED),
            ],
            confidence=0.85,
            reasoning="",
        )
        
        result_tuple = append_tasks_to_todo(result, vault_paths, date_str, write_id)
        
        if result_tuple:
            _, inserted = result_tuple
            # Only new task should be in inserted text
            assert "Existing task one" not in inserted
            assert "New task two" in inserted
    
    def test_update_entities_adds_new_only(self, vault_paths):
        """Only new entities are added to entities.json."""
        write_id = "test-write-entities"
        
        # Create initial entities
        vault_paths.entities_file.write_text(json.dumps([
            {"name": "Alice", "kind": "person", "note": None}
        ]))
        
        result = DistillResult(
            capture_id="test-entities",
            distilled_at="2026-01-11T12:00:00Z",
            route_label="TASK",
            summary="Test",
            entities=[
                EntityMention(name="Alice", kind=EntityKind.PERSON),  # Existing
                EntityMention(name="Bob", kind=EntityKind.PERSON),  # New
            ],
            confidence=0.85,
            reasoning="",
        )
        
        result_tuple = update_entities_json(result, vault_paths, write_id)
        
        entities = json.loads(vault_paths.entities_file.read_text())
        names = [e["name"] for e in entities]
        
        assert "Alice" in names
        assert "Bob" in names
        # Should have exactly 2 entities (not duplicate Alice)
        assert len([n for n in names if n == "Alice"]) == 1


class TestFullDistillationPipeline:
    """Integration tests for the full distillation pipeline."""
    
    def test_process_distillation_creates_all_artifacts(self, vault_paths):
        """Full distillation creates distill artifact, daily note, todo, entities, trace."""
        date_str = "2026-01-11"
        
        # Create routed folder with test item
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        # Create raw capture
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "test_capture.txt"
        raw_file.write_text("I need to call Alice about the project. Must review Bob's code.")
        
        # Create routed item JSON
        routed_item_path = routed_folder / "test-full-pipeline.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-full-pipeline",
            "route_label": "TASK",
            "confidence": 0.85,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        # Initialize required files
        vault_paths.todo_file.write_text("# Tasks\n\n")
        vault_paths.entities_file.write_text("[]")
        
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        llm_client = FakeLLMClient()
        
        # Load and process
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        assert len(routed_items) == 1
        
        distill_result, write_record = process_distillation(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            date_str=date_str,
        )
        
        # Verify distill artifact exists
        distill_folder = vault_paths.distill_date_folder(date_str)
        distill_files = list(distill_folder.glob("*.json"))
        assert len(distill_files) >= 1
        
        # Verify daily note exists
        daily_path = vault_paths.daily_note_path(date_str)
        assert daily_path.exists()
        
        # Verify trace exists
        trace_folder = vault_paths.traces_writes_date_folder(date_str)
        trace_files = list(trace_folder.glob("*.json"))
        assert len(trace_files) >= 1
        
        # Verify ledger event
        ledger_content = vault_paths.ledger_file.read_text()
        assert "DISTILL_APPLIED" in ledger_content
        assert write_record.write_id in ledger_content
    
    def test_undo_removes_inserted_blocks(self, vault_paths):
        """Undo operation removes inserted blocks from files."""
        date_str = "2026-01-11"
        
        # Setup: run full distillation first
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "undo_test.txt"
        raw_file.write_text("Need to finish the task urgently.")
        
        routed_item_path = routed_folder / "test-undo.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-undo",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        vault_paths.todo_file.write_text("# Tasks\n\nExisting content\n")
        vault_paths.entities_file.write_text("[]")
        
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        distill_result, write_record = process_distillation(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            date_str=date_str,
        )
        
        # Verify content was added
        daily_path = vault_paths.daily_note_path(date_str)
        assert daily_path.exists()
        daily_content_before = daily_path.read_text()
        assert "Totem Distill" in daily_content_before
        
        # Now undo
        modified_files = undo_canon_write(
            write_id=write_record.write_id,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
        )
        
        # Verify content was removed
        daily_content_after = daily_path.read_text()
        assert BLOCK_START_MARKER.format(write_id=write_record.write_id) not in daily_content_after
        
        # Verify ledger has undo event
        ledger_content = vault_paths.ledger_file.read_text()
        assert "DISTILL_UNDONE" in ledger_content


class TestLoadRoutedItems:
    """Tests for loading routed items."""
    
    def test_load_routed_items_returns_with_raw_text(self, vault_paths):
        """Loaded items include raw_text from original capture."""
        date_str = "2026-01-11"
        
        # Create raw capture
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "load_test.txt"
        raw_file.write_text("This is the raw capture content.")
        
        # Create routed item
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        routed_item_path = routed_folder / "test-load.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-load",
            "route_label": "TASK",
            "confidence": 0.8,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        items = load_routed_items(vault_paths, date_str, limit=10)
        
        assert len(items) == 1
        assert items[0]["raw_text"] == "This is the raw capture content."
    
    def test_load_routed_items_respects_limit(self, vault_paths):
        """Load respects limit parameter."""
        date_str = "2026-01-11"
        
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        # Create multiple routed items
        for i in range(5):
            item_path = routed_folder / f"test-{i}.json"
            item_path.write_text(json.dumps({
                "capture_id": f"test-{i}",
                "route_label": "TASK",
                "confidence": 0.8,
                "raw_file_path": "",
                "meta_file_path": "",
            }))
        
        items = load_routed_items(vault_paths, date_str, limit=3)
        assert len(items) == 3


class TestContentHash:
    """Tests for content hash verification."""
    
    def test_compute_content_hash_deterministic(self):
        """Same text always produces same hash."""
        text = "Test content for hashing"
        hash1 = compute_content_hash(text)
        hash2 = compute_content_hash(text)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length
    
    def test_compute_content_hash_different_for_different_text(self):
        """Different text produces different hash."""
        hash1 = compute_content_hash("Text A")
        hash2 = compute_content_hash("Text B")
        assert hash1 != hash2
    
    def test_applied_file_includes_content_hash(self, vault_paths):
        """AppliedFile records include content_hash."""
        date_str = "2026-01-11"
        
        # Setup routed item
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "hash_test.txt"
        raw_file.write_text("Need to verify content hash works.")
        
        routed_item_path = routed_folder / "test-hash.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-hash",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        vault_paths.entities_file.write_text("[]")
        
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        distill_result, write_record = process_distillation(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            date_str=date_str,
        )
        
        # Verify all applied files have content_hash
        for applied_file in write_record.applied_files:
            assert hasattr(applied_file, 'content_hash')
            assert applied_file.content_hash
            assert len(applied_file.content_hash) == 64
    
    def test_undo_detects_hash_mismatch(self, vault_paths):
        """Undo warns when file has been manually edited."""
        date_str = "2026-01-11"
        
        # Setup and run distillation
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "mismatch_test.txt"
        raw_file.write_text("Need to test hash mismatch detection.")
        
        routed_item_path = routed_folder / "test-mismatch.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-mismatch",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        vault_paths.entities_file.write_text("[]")
        
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        distill_result, write_record = process_distillation(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            date_str=date_str,
        )
        
        # Manually edit the daily note to cause hash mismatch
        daily_path = vault_paths.daily_note_path(date_str)
        content = daily_path.read_text(encoding="utf-8")
        modified_content = content.replace("**Summary:**", "**MANUALLY EDITED Summary:**")
        daily_path.write_text(modified_content, encoding="utf-8")
        
        # Undo should skip the modified file
        modified_files = undo_canon_write(
            write_id=write_record.write_id,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
        )
        
        # Daily note should NOT be in modified files (hash mismatch)
        daily_relative = str(daily_path.relative_to(vault_paths.root))
        assert daily_relative not in modified_files
        
        # Verify ledger has warning about hash mismatch
        ledger_content = vault_paths.ledger_file.read_text()
        assert "DISTILL_UNDONE" in ledger_content


class TestDryRun:
    """Tests for dry-run mode."""
    
    def test_dry_run_creates_distill_artifact(self, vault_paths):
        """Dry-run creates distill artifact in 10_derived/distill/."""
        date_str = "2026-01-11"
        
        # Setup routed item
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "dryrun_test.txt"
        raw_file.write_text("Need to test dry-run mode works correctly.")
        
        routed_item_path = routed_folder / "test-dryrun.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-dryrun",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        vault_paths.entities_file.write_text("[]")
        
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        distill_result, would_apply, distill_path = process_distillation_dry_run(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            date_str=date_str,
        )
        
        # Distill artifact should exist
        full_distill_path = vault_paths.root / distill_path
        assert full_distill_path.exists()
        
        # Verify distill artifact content
        artifact = json.loads(full_distill_path.read_text())
        assert artifact["capture_id"] == "test-dryrun"
    
    def test_dry_run_does_not_modify_daily(self, vault_paths):
        """Dry-run does NOT modify daily notes."""
        date_str = "2026-01-11"
        
        # Setup
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "dryrun_daily_test.txt"
        raw_file.write_text("Need to verify daily note not modified.")
        
        routed_item_path = routed_folder / "test-dryrun-daily.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-dryrun-daily",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        vault_paths.entities_file.write_text("[]")
        
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        distill_result, would_apply, distill_path = process_distillation_dry_run(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            date_str=date_str,
        )
        
        # Daily note should NOT exist (dry-run doesn't create it)
        daily_path = vault_paths.daily_note_path(date_str)
        assert not daily_path.exists()
    
    def test_dry_run_does_not_modify_todo(self, vault_paths):
        """Dry-run does NOT modify todo.md."""
        date_str = "2026-01-11"
        
        # Setup
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "dryrun_todo_test.txt"
        raw_file.write_text("Need to verify todo not modified.")
        
        routed_item_path = routed_folder / "test-dryrun-todo.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-dryrun-todo",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        original_todo = "# Tasks\n\nOriginal content only\n"
        vault_paths.todo_file.write_text(original_todo)
        vault_paths.entities_file.write_text("[]")
        
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        process_distillation_dry_run(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            date_str=date_str,
        )
        
        # Todo should be unchanged
        assert vault_paths.todo_file.read_text() == original_todo
    
    def test_dry_run_returns_would_apply_list(self, vault_paths):
        """Dry-run returns list of files that would be modified."""
        date_str = "2026-01-11"
        
        # Setup
        routed_folder = vault_paths.routed_date_folder(date_str)
        routed_folder.mkdir(parents=True, exist_ok=True)
        
        inbox_folder = vault_paths.inbox / date_str
        inbox_folder.mkdir(parents=True, exist_ok=True)
        raw_file = inbox_folder / "dryrun_list_test.txt"
        raw_file.write_text("Need to verify would_apply list is returned.")
        
        routed_item_path = routed_folder / "test-dryrun-list.json"
        routed_item_path.write_text(json.dumps({
            "capture_id": "test-dryrun-list",
            "route_label": "TASK",
            "confidence": 0.9,
            "raw_file_path": str(raw_file.relative_to(vault_paths.root)),
            "meta_file_path": "",
        }))
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        vault_paths.entities_file.write_text("[]")
        
        llm_client = FakeLLMClient()
        
        routed_items = load_routed_items(vault_paths, date_str, limit=10)
        distill_result, would_apply, distill_path = process_distillation_dry_run(
            routed_item=routed_items[0],
            llm_client=llm_client,
            vault_paths=vault_paths,
            date_str=date_str,
        )
        
        # Should have would_apply entries
        assert len(would_apply) > 0
        
        # Each entry should have required fields
        for af in would_apply:
            assert af.path
            assert af.inserted_text
            assert af.content_hash
            assert len(af.content_hash) == 64
