"""Tests for Milestone 6: Review and Correction system.

Tests cover:
- Approve path: writes canon + logs event
- Veto path: does NOT write canon + logs event
- Correct path: writes corrected artifact + logs override event
- Defer path: keeps item in queue
"""

import json
from pathlib import Path

import pytest

from totem.ledger import LedgerWriter
from totem.models.review import (
    ArtifactType,
    OverrideArtifact,
    ProposalStatus,
    ProposedArtifact,
    ReviewEventType,
    ReviewQueueItem,
)
from totem.review import (
    KeyInputSource,
    LearningEventLogger,
    ReviewQueue,
    ReviewSession,
    format_proposal_display,
    write_approved_artifact,
    write_corrected_artifact,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_proposal():
    """Create a sample proposal for testing."""
    return ProposedArtifact(
        proposal_id="test-proposal-123",
        capture_id="test-capture-456",
        run_id="test-run-789",
        artifact_type=ArtifactType.TASK,
        title="Test Task",
        content="This is a test task content that needs to be reviewed.",
        destination="30_tasks/todo.md",
        rationale="Extracted from test capture with high confidence.",
        confidence=0.85,
        created_at="2026-01-14T12:00:00Z",
        source_file="10_derived/distill/2026-01-14/test.json",
    )


@pytest.fixture
def sample_note_proposal():
    """Create a sample note proposal for testing."""
    return ProposedArtifact(
        proposal_id="test-note-proposal",
        capture_id="test-capture-note",
        run_id=None,
        artifact_type=ArtifactType.NOTE,
        title="Important Meeting Notes",
        content="Discussion about project timeline and milestones.",
        destination="20_memory/daily/2026-01-14.md",
        rationale="Summary of captured meeting notes.",
        confidence=0.75,
        created_at="2026-01-14T14:00:00Z",
    )


# ============================================================================
# ProposedArtifact Model Tests
# ============================================================================


class TestProposedArtifactModel:
    """Tests for ProposedArtifact model."""
    
    def test_creates_valid_proposal(self, sample_proposal):
        """ProposedArtifact accepts valid data."""
        assert sample_proposal.proposal_id == "test-proposal-123"
        assert sample_proposal.artifact_type == ArtifactType.TASK
        assert sample_proposal.confidence == 0.85
    
    def test_enforces_confidence_bounds(self):
        """ProposedArtifact rejects confidence outside 0-1."""
        with pytest.raises(ValueError):
            ProposedArtifact(
                proposal_id="test",
                artifact_type=ArtifactType.TASK,
                content="test",
                destination="test",
                rationale="test",
                confidence=1.5,  # Invalid
                created_at="2026-01-14T12:00:00Z",
            )
    
    def test_optional_fields_are_optional(self):
        """Optional fields can be None."""
        proposal = ProposedArtifact(
            proposal_id="minimal-test",
            artifact_type=ArtifactType.NOTE,
            content="minimal content",
            destination="test/path",
            rationale="minimal rationale",
            confidence=0.5,
            created_at="2026-01-14T12:00:00Z",
        )
        
        assert proposal.capture_id is None
        assert proposal.run_id is None
        assert proposal.title is None
        assert proposal.source_file is None


# ============================================================================
# ReviewQueue Tests
# ============================================================================


class TestReviewQueue:
    """Tests for ReviewQueue persistence."""
    
    def test_add_proposal_creates_file(self, vault_paths, sample_proposal):
        """Adding a proposal creates the queue file."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        item = queue.add_proposal(sample_proposal)
        
        assert vault_paths.review_queue_file.exists()
        assert item.status == ProposalStatus.PENDING
        assert item.proposal.proposal_id == sample_proposal.proposal_id
    
    def test_get_pending_items_returns_pending_only(self, vault_paths, sample_proposal):
        """get_pending_items only returns PENDING items."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        
        # Add proposal
        queue.add_proposal(sample_proposal)
        
        # Verify pending
        pending = queue.get_pending_items()
        assert len(pending) == 1
        assert pending[0].status == ProposalStatus.PENDING
    
    def test_update_status_changes_status(self, vault_paths, sample_proposal):
        """update_status correctly changes item status."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        # Update to approved
        result = queue.update_status(sample_proposal.proposal_id, ProposalStatus.APPROVED)
        
        assert result is True
        
        # Verify no more pending
        pending = queue.get_pending_items()
        assert len(pending) == 0
        
        # Reload and check
        all_items = queue._load_all_items()
        assert len(all_items) == 1
        assert all_items[0].status == ProposalStatus.APPROVED
    
    def test_defer_increments_count(self, vault_paths, sample_proposal):
        """Deferring increments defer_count."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        # Defer twice
        queue.update_status(sample_proposal.proposal_id, ProposalStatus.DEFERRED)
        # Reset to pending and defer again
        queue.update_status(sample_proposal.proposal_id, ProposalStatus.PENDING)
        queue.update_status(sample_proposal.proposal_id, ProposalStatus.DEFERRED)
        
        all_items = queue._load_all_items()
        assert all_items[0].defer_count == 2
    
    def test_get_deferred_items(self, vault_paths, sample_proposal):
        """get_deferred_items returns only deferred items."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        # Initially no deferred
        deferred = queue.get_deferred_items()
        assert len(deferred) == 0
        
        # Defer
        queue.update_status(sample_proposal.proposal_id, ProposalStatus.DEFERRED)
        
        # Now one deferred
        deferred = queue.get_deferred_items()
        assert len(deferred) == 1
    
    def test_limit_parameter(self, vault_paths):
        """Limit parameter restricts returned items."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        
        # Add multiple proposals
        for i in range(5):
            proposal = ProposedArtifact(
                proposal_id=f"proposal-{i}",
                artifact_type=ArtifactType.TASK,
                content=f"Task {i}",
                destination="test",
                rationale="test",
                confidence=0.5,
                created_at="2026-01-14T12:00:00Z",
            )
            queue.add_proposal(proposal)
        
        # Get with limit
        pending = queue.get_pending_items(limit=2)
        assert len(pending) == 2


# ============================================================================
# LearningEventLogger Tests
# ============================================================================


class TestLearningEventLogger:
    """Tests for LearningEventLogger."""
    
    def test_log_event_creates_file(self, vault_paths, sample_proposal):
        """Logging an event creates the events file."""
        logger = LearningEventLogger(vault_paths.review_events_file)
        
        event = logger.log_event(
            ReviewEventType.APPROVED,
            sample_proposal,
            {"destination": "test/path"},
        )
        
        assert vault_paths.review_events_file.exists()
        assert event.event_type == ReviewEventType.APPROVED
        assert event.proposal_id == sample_proposal.proposal_id
    
    def test_log_event_appends_to_file(self, vault_paths, sample_proposal):
        """Multiple events are appended to the file."""
        logger = LearningEventLogger(vault_paths.review_events_file)
        
        logger.log_event(ReviewEventType.APPROVED, sample_proposal)
        logger.log_event(ReviewEventType.VETOED, sample_proposal)
        logger.log_event(ReviewEventType.DEFERRED, sample_proposal)
        
        # Read file and count lines
        lines = vault_paths.review_events_file.read_text().strip().split("\n")
        assert len(lines) == 3
    
    def test_log_event_includes_payload(self, vault_paths, sample_proposal):
        """Event includes custom payload."""
        logger = LearningEventLogger(vault_paths.review_events_file)
        
        payload = {"custom_field": "custom_value", "count": 42}
        event = logger.log_event(ReviewEventType.CORRECTED, sample_proposal, payload)
        
        assert event.payload["custom_field"] == "custom_value"
        assert event.payload["count"] == 42


# ============================================================================
# KeyInputSource Tests
# ============================================================================


class TestKeyInputSource:
    """Tests for KeyInputSource abstraction."""
    
    def test_injected_sequence_returns_keys_in_order(self):
        """Injected sequence returns keys in order."""
        source = KeyInputSource(key_sequence=["a", "b", "c"])
        
        assert source.get_key() == "a"
        assert source.get_key() == "b"
        assert source.get_key() == "c"
    
    def test_exhausted_sequence_returns_quit(self):
        """Exhausted sequence returns 'q' to quit."""
        source = KeyInputSource(key_sequence=["a"])
        
        source.get_key()  # Consume 'a'
        assert source.get_key() == "q"  # Auto-quit
    
    def test_get_line_from_sequence(self):
        """get_line reads until newline from sequence."""
        source = KeyInputSource(key_sequence=["h", "e", "l", "l", "o", "\n", "x"])
        
        line = source.get_line()
        assert line == "hello"
        
        # Next key should be 'x'
        assert source.get_key() == "x"


# ============================================================================
# Display Format Tests
# ============================================================================


class TestDisplayFormat:
    """Tests for display formatting."""
    
    def test_format_proposal_display_includes_all_fields(self, sample_proposal):
        """Display includes all required fields."""
        display = format_proposal_display(sample_proposal)
        
        assert "TASK" in display
        assert "Test Task" in display
        assert "30_tasks/todo.md" in display
        assert "0.85" in display
        assert "[A]pprove" in display
        assert "[V]eto" in display
        assert "[C]orrect" in display
        assert "[D]efer" in display
        assert "[Q]uit" in display
    
    def test_format_truncates_long_content(self):
        """Long content is truncated with ellipsis."""
        proposal = ProposedArtifact(
            proposal_id="long-content-test",
            artifact_type=ArtifactType.NOTE,
            content="x" * 500,  # Long content
            destination="test",
            rationale="test",
            confidence=0.5,
            created_at="2026-01-14T12:00:00Z",
        )
        
        display = format_proposal_display(proposal)
        assert "..." in display
        # Should be around 280 chars + ellipsis
        assert "x" * 400 not in display


# ============================================================================
# Canon Write Tests
# ============================================================================


class TestCanonWrite:
    """Tests for canon write operations."""
    
    def test_approve_task_writes_to_todo(self, vault_paths, sample_proposal):
        """Approving a task writes to todo.md."""
        # Initialize todo file
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        output_path = write_approved_artifact(
            sample_proposal,
            vault_paths,
            ledger_writer,
        )
        
        assert output_path == vault_paths.todo_file
        
        content = vault_paths.todo_file.read_text()
        assert "test task content" in content
        assert "- [ ]" in content
    
    def test_approve_note_writes_file(self, vault_paths, sample_note_proposal):
        """Approving a note writes a markdown file."""
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        output_path = write_approved_artifact(
            sample_note_proposal,
            vault_paths,
            ledger_writer,
        )
        
        assert output_path.exists()
        content = output_path.read_text()
        assert "Important Meeting Notes" in content
        assert "Discussion about project timeline" in content
    
    def test_approve_logs_ledger_event(self, vault_paths, sample_proposal):
        """Approving logs a REVIEW_APPROVED event."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        write_approved_artifact(sample_proposal, vault_paths, ledger_writer)
        
        ledger_content = vault_paths.ledger_file.read_text()
        assert "REVIEW_APPROVED" in ledger_content
        assert sample_proposal.proposal_id in ledger_content


# ============================================================================
# Veto Tests
# ============================================================================


class TestVetoPath:
    """Tests for veto behavior."""
    
    def test_veto_does_not_write_artifact(self, vault_paths, sample_proposal):
        """Vetoing does NOT write to canon."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Create session with injected 'v' key
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["v", "q"]),
            output_fn=lambda x: None,  # Suppress output
        )
        
        # Run session
        summary = session.run(limit=1)
        
        # Verify veto happened
        assert summary["vetoed"] == 1
        
        # Verify todo.md was NOT created/modified
        assert not vault_paths.todo_file.exists()
    
    def test_veto_logs_event(self, vault_paths, sample_proposal):
        """Vetoing logs a learning event."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["v", "q"]),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Verify learning event logged
        assert vault_paths.review_events_file.exists()
        events_content = vault_paths.review_events_file.read_text()
        assert "review_vetoed" in events_content
    
    def test_veto_updates_queue_status(self, vault_paths, sample_proposal):
        """Vetoing updates queue status to VETOED."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["v", "q"]),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Verify queue status
        all_items = queue._load_all_items()
        assert len(all_items) == 1
        assert all_items[0].status == ProposalStatus.VETOED


# ============================================================================
# Approve Tests
# ============================================================================


class TestApprovePath:
    """Tests for approve behavior."""
    
    def test_approve_writes_artifact(self, vault_paths, sample_proposal):
        """Approving writes artifact to canon."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["a", "q"]),
            output_fn=lambda x: None,
        )
        
        summary = session.run(limit=1)
        
        assert summary["approved"] == 1
        
        # Verify artifact written
        content = vault_paths.todo_file.read_text()
        assert "test task content" in content
    
    def test_approve_logs_both_events(self, vault_paths, sample_proposal):
        """Approving logs to both learning events and ledger."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["a", "q"]),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Verify learning event
        events_content = vault_paths.review_events_file.read_text()
        assert "review_approved" in events_content
        
        # Verify ledger event
        ledger_content = vault_paths.ledger_file.read_text()
        assert "REVIEW_APPROVED" in ledger_content
    
    def test_approve_updates_queue_status(self, vault_paths, sample_proposal):
        """Approving updates queue status to APPROVED."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["a", "q"]),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        all_items = queue._load_all_items()
        assert all_items[0].status == ProposalStatus.APPROVED


# ============================================================================
# Defer Tests
# ============================================================================


class TestDeferPath:
    """Tests for defer behavior."""
    
    def test_defer_keeps_in_queue(self, vault_paths, sample_proposal):
        """Deferring keeps item in queue with DEFERRED status."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["d", "q"]),
            output_fn=lambda x: None,
        )
        
        summary = session.run(limit=1)
        
        assert summary["deferred"] == 1
        
        # Verify status
        all_items = queue._load_all_items()
        assert all_items[0].status == ProposalStatus.DEFERRED
        assert all_items[0].defer_count == 1
    
    def test_defer_does_not_write_artifact(self, vault_paths, sample_proposal):
        """Deferring does NOT write artifact."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["d", "q"]),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Verify no artifact written
        assert not vault_paths.todo_file.exists()
    
    def test_defer_logs_event(self, vault_paths, sample_proposal):
        """Deferring logs a learning event with defer_count."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["d", "q"]),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        events_content = vault_paths.review_events_file.read_text()
        assert "review_deferred" in events_content
        assert "defer_count" in events_content


# ============================================================================
# Correct Tests
# ============================================================================


class TestCorrectPath:
    """Tests for correct/override behavior."""
    
    def test_correct_writes_corrected_artifact(self, vault_paths, sample_proposal):
        """Correcting writes the corrected artifact."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Sequence: c (correct), empty type, empty title, corrected content, end content, empty dest, a (approve correction)
        key_sequence = [
            "c",           # Enter correct mode
            "\n",          # Accept default type
            "\n",          # Accept default title
            "C", "o", "r", "r", "e", "c", "t", "e", "d", "\n",  # New content
            ".", "\n",     # End content input
            "\n",          # Accept default destination
            "a",           # Approve correction
            "q",           # Quit
        ]
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        summary = session.run(limit=1)
        
        assert summary["corrected"] == 1
        
        # Verify corrected content written
        content = vault_paths.todo_file.read_text()
        assert "Corrected" in content
    
    def test_correct_logs_override_event(self, vault_paths, sample_proposal):
        """Correcting logs override event with original proposal linkage."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        key_sequence = [
            "c", "\n", "\n", "F", "i", "x", "e", "d", "\n", ".", "\n", "\n", "a", "q"
        ]
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Verify learning event with override details
        events_content = vault_paths.review_events_file.read_text()
        assert "review_corrected" in events_content
        assert "override_id" in events_content
        
        # Verify ledger event
        ledger_content = vault_paths.ledger_file.read_text()
        assert "REVIEW_CORRECTED" in ledger_content
    
    def test_correct_saves_override_record(self, vault_paths, sample_proposal):
        """Correcting saves an override record file."""
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        key_sequence = [
            "c", "\n", "\n", "O", "v", "e", "r", "r", "i", "d", "e", "\n", ".", "\n", "\n", "a", "q"
        ]
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Verify override record exists
        overrides_folder = vault_paths.corrections / "overrides"
        assert overrides_folder.exists()
        override_files = list(overrides_folder.rglob("*.json"))
        assert len(override_files) >= 1
    
    def test_correct_cancel_does_not_write(self, vault_paths, sample_proposal):
        """Cancelling correction does not write artifact."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Sequence: c (correct), empty inputs, v (cancel)
        key_sequence = [
            "c", "\n", "\n", "C", "a", "n", "c", "e", "l", "\n", ".", "\n", "\n", "v", "q"
        ]
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        summary = session.run(limit=1)
        
        # Correction was cancelled, so count should be 0
        assert summary["corrected"] == 0
        
        # No artifact written
        assert not vault_paths.todo_file.exists()


# ============================================================================
# Dry-Run Tests
# ============================================================================


class TestDryRunMode:
    """Tests for dry-run mode."""
    
    def test_dry_run_does_not_write_on_approve(self, vault_paths, sample_proposal):
        """Dry-run approve does NOT write artifacts."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["a", "q"]),
            dry_run=True,
            output_fn=lambda x: None,
        )
        
        summary = session.run(limit=1)
        
        assert summary["approved"] == 1
        
        # Verify NO artifact written
        assert not vault_paths.todo_file.exists()
    
    def test_dry_run_does_not_update_queue(self, vault_paths, sample_proposal):
        """Dry-run does NOT update queue status."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        queue.add_proposal(sample_proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=["a", "q"]),
            dry_run=True,
            output_fn=lambda x: None,
        )
        
        session.run(limit=1)
        
        # Status should still be PENDING
        all_items = queue._load_all_items()
        assert all_items[0].status == ProposalStatus.PENDING


# ============================================================================
# Session Integration Tests
# ============================================================================


class TestReviewSession:
    """Integration tests for ReviewSession."""
    
    def test_session_processes_multiple_items(self, vault_paths):
        """Session can process multiple items in sequence."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        
        # Add multiple proposals
        for i in range(3):
            proposal = ProposedArtifact(
                proposal_id=f"multi-{i}",
                artifact_type=ArtifactType.TASK,
                content=f"Task {i}",
                destination="30_tasks/todo.md",
                rationale="test",
                confidence=0.5,
                created_at="2026-01-14T12:00:00Z",
            )
            queue.add_proposal(proposal)
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Approve, veto, defer
        key_sequence = ["a", "v", "d", "q"]
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        summary = session.run()
        
        assert summary["approved"] == 1
        assert summary["vetoed"] == 1
        assert summary["deferred"] == 1
        assert summary["total"] == 3
    
    def test_session_quit_stops_processing(self, vault_paths):
        """Pressing Q stops the session."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        
        # Add multiple proposals
        for i in range(5):
            proposal = ProposedArtifact(
                proposal_id=f"quit-test-{i}",
                artifact_type=ArtifactType.TASK,
                content=f"Task {i}",
                destination="test",
                rationale="test",
                confidence=0.5,
                created_at="2026-01-14T12:00:00Z",
            )
            queue.add_proposal(proposal)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Quit after first item
        key_sequence = ["a", "q"]
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        summary = session.run()
        
        # Only one processed before quit
        assert summary["total"] == 1
    
    def test_session_with_limit(self, vault_paths):
        """Limit parameter restricts items processed."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        
        # Add multiple proposals
        for i in range(5):
            proposal = ProposedArtifact(
                proposal_id=f"limit-test-{i}",
                artifact_type=ArtifactType.TASK,
                content=f"Task {i}",
                destination="test",
                rationale="test",
                confidence=0.5,
                created_at="2026-01-14T12:00:00Z",
            )
            queue.add_proposal(proposal)
        
        vault_paths.todo_file.write_text("# Tasks\n\n")
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        # Approve all
        key_sequence = ["a"] * 5
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            key_source=KeyInputSource(key_sequence=key_sequence),
            output_fn=lambda x: None,
        )
        
        summary = session.run(limit=2)
        
        # Only 2 processed due to limit
        assert summary["total"] == 2
    
    def test_empty_queue_returns_immediately(self, vault_paths):
        """Empty queue returns immediately without error."""
        queue = ReviewQueue(vault_paths.review_queue_file)
        
        logger = LearningEventLogger(vault_paths.review_events_file)
        ledger_writer = LedgerWriter(vault_paths.ledger_file)
        
        output_messages = []
        
        session = ReviewSession(
            review_queue=queue,
            learning_logger=logger,
            vault_paths=vault_paths,
            ledger_writer=ledger_writer,
            output_fn=output_messages.append,
        )
        
        summary = session.run()
        
        assert summary["total"] == 0
        assert any("No pending" in msg for msg in output_messages)
