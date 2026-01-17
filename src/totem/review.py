"""Milestone 6: Totem Review + Correct system.

Single-keystroke review loop where user approves, vetoes, corrects, or defers.
Philosophy: User never files, only judges. No silent writes.
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Literal

from .ledger import LedgerWriter
from .models.review import (
    ArtifactType,
    OverrideArtifact,
    ProposalStatus,
    ProposedArtifact,
    ReviewEvent,
    ReviewEventType,
    ReviewQueueItem,
)
from .paths import VaultPaths


# ============================================================================
# Review Queue Persistence (JSONL-based)
# ============================================================================


class ReviewQueue:
    """Persistent queue of proposed artifacts awaiting review.
    
    Uses JSONL format for append-friendly persistence.
    Each line is a ReviewQueueItem JSON object.
    """

    def __init__(self, queue_path: Path):
        """Initialize review queue.
        
        Args:
            queue_path: Path to the JSONL queue file
        """
        self.queue_path = queue_path
        self._ensure_parent_exists()

    def _ensure_parent_exists(self) -> None:
        """Ensure parent directory exists."""
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def add_proposal(self, proposal: ProposedArtifact) -> ReviewQueueItem:
        """Add a new proposal to the queue.
        
        Args:
            proposal: The proposed artifact to add
            
        Returns:
            The created ReviewQueueItem
        """
        item = ReviewQueueItem(
            proposal=proposal,
            status=ProposalStatus.PENDING,
            status_changed_at=None,
            defer_count=0,
        )
        
        # Append to JSONL
        with open(self.queue_path, "a", encoding="utf-8") as f:
            f.write(item.model_dump_json() + "\n")
        
        return item

    def get_pending_items(self, limit: int | None = None) -> list[ReviewQueueItem]:
        """Get all pending items from the queue.
        
        Args:
            limit: Maximum number of items to return
            
        Returns:
            List of pending ReviewQueueItems
        """
        items = self._load_all_items()
        pending = [item for item in items if item.status == ProposalStatus.PENDING]
        
        if limit:
            return pending[:limit]
        return pending

    def get_deferred_items(self, limit: int | None = None) -> list[ReviewQueueItem]:
        """Get all deferred items from the queue.
        
        Args:
            limit: Maximum number of items to return
            
        Returns:
            List of deferred ReviewQueueItems
        """
        items = self._load_all_items()
        deferred = [item for item in items if item.status == ProposalStatus.DEFERRED]
        
        if limit:
            return deferred[:limit]
        return deferred

    def update_status(
        self,
        proposal_id: str,
        new_status: ProposalStatus,
    ) -> bool:
        """Update the status of a proposal.
        
        Rewrites the queue file with updated status.
        
        Args:
            proposal_id: ID of the proposal to update
            new_status: New status to set
            
        Returns:
            True if proposal was found and updated, False otherwise
        """
        items = self._load_all_items()
        found = False
        
        for item in items:
            if item.proposal.proposal_id == proposal_id:
                item.status = new_status
                item.status_changed_at = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                if new_status == ProposalStatus.DEFERRED:
                    item.defer_count += 1
                found = True
                break
        
        if found:
            self._write_all_items(items)
        
        return found

    def _load_all_items(self) -> list[ReviewQueueItem]:
        """Load all items from the queue file."""
        if not self.queue_path.exists():
            return []
        
        items = []
        with open(self.queue_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    items.append(ReviewQueueItem(**data))
                except (json.JSONDecodeError, ValueError):
                    # Skip malformed lines
                    continue
        
        return items

    def _write_all_items(self, items: list[ReviewQueueItem]) -> None:
        """Write all items to the queue file (rewrite)."""
        self._ensure_parent_exists()
        with open(self.queue_path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(item.model_dump_json() + "\n")


# ============================================================================
# Learning Event Logger
# ============================================================================


class LearningEventLogger:
    """Logger for review learning events.
    
    Writes events to JSONL file for later analysis.
    """

    def __init__(self, events_path: Path):
        """Initialize learning event logger.
        
        Args:
            events_path: Path to the JSONL events file
        """
        self.events_path = events_path
        self._ensure_parent_exists()

    def _ensure_parent_exists(self) -> None:
        """Ensure parent directory exists."""
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(
        self,
        event_type: ReviewEventType,
        proposal: ProposedArtifact,
        payload: dict | None = None,
    ) -> ReviewEvent:
        """Log a review event.
        
        Args:
            event_type: Type of review event
            proposal: The proposal being reviewed
            payload: Additional event-specific data
            
        Returns:
            The created ReviewEvent
        """
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            proposal_id=proposal.proposal_id,
            capture_id=proposal.capture_id,
            run_id=proposal.run_id,
            artifact_type=proposal.artifact_type.value,
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            payload=payload or {},
        )
        
        # Append to JSONL
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        
        return event


# ============================================================================
# Terminal Keystroke Capture (Cross-platform)
# ============================================================================


def get_single_keypress() -> str:
    """Read a single keypress from the terminal.
    
    Cross-platform: uses msvcrt on Windows, termios on Unix.
    
    Returns:
        Single character that was pressed
    """
    try:
        # Try Windows first
        import msvcrt
        return msvcrt.getch().decode("utf-8", errors="replace")
    except ImportError:
        # Unix/Linux/macOS
        import termios
        import tty
        
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch


class KeyInputSource:
    """Abstraction for key input to enable testing.
    
    In production: reads from terminal.
    In tests: reads from injected sequence.
    """

    def __init__(self, key_sequence: list[str] | None = None):
        """Initialize key input source.
        
        Args:
            key_sequence: Optional list of keys for testing.
                          If None, reads from terminal.
        """
        self._sequence = key_sequence
        self._index = 0

    def get_key(self) -> str:
        """Get next key.
        
        Returns:
            Single character from sequence or terminal
        """
        if self._sequence is not None:
            if self._index >= len(self._sequence):
                return "q"  # Auto-quit when sequence exhausted
            key = self._sequence[self._index]
            self._index += 1
            return key
        else:
            return get_single_keypress()

    def get_line(self, prompt: str = "") -> str:
        """Get a line of input.
        
        Args:
            prompt: Prompt to display
            
        Returns:
            Line of input
        """
        if self._sequence is not None:
            # In test mode, consume until newline
            line_parts = []
            while self._index < len(self._sequence):
                char = self._sequence[self._index]
                self._index += 1
                if char == "\n":
                    break
                line_parts.append(char)
            return "".join(line_parts)
        else:
            return input(prompt)


# ============================================================================
# Multiline Input Helper
# ============================================================================


def get_multiline_input(
    key_source: KeyInputSource,
    prompt: str = "Enter content (end with '.' on its own line):\n",
) -> str:
    """Get multiline input from user.
    
    Continues reading lines until a line containing only '.' is entered.
    
    Args:
        key_source: Key input source
        prompt: Prompt to display
        
    Returns:
        Multiline content string
    """
    if key_source._sequence is not None:
        # Test mode: read from sequence
        lines = []
        while True:
            line = key_source.get_line()
            if line == ".":
                break
            lines.append(line)
        return "\n".join(lines)
    else:
        # Interactive mode
        print(prompt)
        lines = []
        while True:
            try:
                line = input()
                if line == ".":
                    break
                lines.append(line)
            except EOFError:
                break
        return "\n".join(lines)


# ============================================================================
# Display Helpers
# ============================================================================


def format_proposal_display(proposal: ProposedArtifact) -> str:
    """Format a proposal for display in the review UI.
    
    Args:
        proposal: Proposal to format
        
    Returns:
        Formatted string for display
    """
    lines = [
        "",
        "=" * 60,
        f"Proposed Type: {proposal.artifact_type.value.upper()}",
    ]
    
    if proposal.title:
        lines.append(f"Title:         {proposal.title}")
    
    lines.append(f"Destination:   {proposal.destination}")
    lines.append(f"Rationale:     {proposal.rationale}")
    lines.append(f"Confidence:    {proposal.confidence:.2f}")
    lines.append("")
    
    # Content preview (first ~280 chars)
    content_preview = proposal.content
    if len(content_preview) > 280:
        content_preview = content_preview[:277] + "..."
    
    lines.append("Content:")
    lines.append("-" * 40)
    lines.append(content_preview)
    lines.append("-" * 40)
    lines.append("")
    lines.append("[A]pprove  [V]eto  [C]orrect  [D]efer  [Q]uit")
    lines.append("")
    
    return "\n".join(lines)


def format_correction_confirmation(
    original: ProposedArtifact,
    corrected_type: ArtifactType,
    corrected_title: str | None,
    corrected_content: str,
    corrected_destination: str | None,
) -> str:
    """Format correction confirmation display.
    
    Args:
        original: Original proposal
        corrected_type: Corrected artifact type
        corrected_title: Corrected title
        corrected_content: Corrected content
        corrected_destination: Corrected destination
        
    Returns:
        Formatted string for confirmation
    """
    lines = [
        "",
        "=" * 60,
        "CORRECTION SUMMARY",
        "=" * 60,
        "",
        f"Original Type: {original.artifact_type.value} -> {corrected_type.value}",
    ]
    
    original_title = original.title or "(none)"
    new_title = corrected_title or "(none)"
    if original_title != new_title:
        lines.append(f"Title:         {original_title} -> {new_title}")
    
    dest = corrected_destination or original.destination
    if dest != original.destination:
        lines.append(f"Destination:   {original.destination} -> {dest}")
    
    lines.append("")
    lines.append("Corrected Content:")
    lines.append("-" * 40)
    
    content_preview = corrected_content
    if len(content_preview) > 280:
        content_preview = content_preview[:277] + "..."
    lines.append(content_preview)
    
    lines.append("-" * 40)
    lines.append("")
    lines.append("[A]pprove corrected  [V]cancel correction")
    lines.append("")
    
    return "\n".join(lines)


# ============================================================================
# Canon Write Integration
# ============================================================================


def write_approved_artifact(
    proposal: ProposedArtifact,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
) -> Path:
    """Write an approved artifact to its canonical location.
    
    Args:
        proposal: Approved proposal
        vault_paths: VaultPaths instance
        ledger_writer: LedgerWriter for logging
        
    Returns:
        Path to the written artifact
    """
    # Determine destination path
    dest_path = vault_paths.root / proposal.destination
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write content based on artifact type
    if proposal.artifact_type == ArtifactType.TASK:
        # Append to todo.md
        _append_task_to_todo(proposal, vault_paths)
        output_path = vault_paths.todo_file
    elif proposal.artifact_type == ArtifactType.NOTE:
        # Write as markdown file
        output_path = _write_note_file(proposal, vault_paths)
    elif proposal.artifact_type == ArtifactType.PRINCIPLE:
        # Append to principles.md
        _append_to_principles(proposal, vault_paths)
        output_path = vault_paths.principles_file
    elif proposal.artifact_type == ArtifactType.MEMORY:
        # Write to daily note
        output_path = _append_to_daily(proposal, vault_paths)
    elif proposal.artifact_type == ArtifactType.DECISION:
        # Write as decision record
        output_path = _write_decision_file(proposal, vault_paths)
    elif proposal.artifact_type == ArtifactType.ENTITY:
        # Add to entities.json
        _add_entity(proposal, vault_paths)
        output_path = vault_paths.entities_file
    else:
        # Generic write to destination
        dest_path.write_text(proposal.content, encoding="utf-8")
        output_path = dest_path
    
    # Log to ledger
    ledger_writer.append_event(
        event_type="REVIEW_APPROVED",
        capture_id=proposal.capture_id,
        payload={
            "proposal_id": proposal.proposal_id,
            "artifact_type": proposal.artifact_type.value,
            "destination": str(output_path.relative_to(vault_paths.root)),
        },
    )
    
    return output_path


def _append_task_to_todo(proposal: ProposedArtifact, vault_paths: VaultPaths) -> None:
    """Append a task to todo.md."""
    todo_path = vault_paths.todo_file
    
    # Format task line
    title_part = f"**{proposal.title}**: " if proposal.title else ""
    task_line = f"- [ ] {title_part}{proposal.content}\n"
    
    # Append
    if todo_path.exists():
        existing = todo_path.read_text(encoding="utf-8")
        todo_path.write_text(existing + task_line, encoding="utf-8")
    else:
        todo_path.write_text(f"# Tasks\n\n{task_line}", encoding="utf-8")


def _write_note_file(proposal: ProposedArtifact, vault_paths: VaultPaths) -> Path:
    """Write a note as a markdown file."""
    # Use destination or generate path
    if proposal.destination:
        note_path = vault_paths.root / proposal.destination
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        note_path = vault_paths.daily / f"note_{proposal.proposal_id[:8]}_{date_str}.md"
    
    note_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Format note content
    lines = []
    if proposal.title:
        lines.append(f"# {proposal.title}")
        lines.append("")
    lines.append(proposal.content)
    
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path


def _append_to_principles(proposal: ProposedArtifact, vault_paths: VaultPaths) -> None:
    """Append a principle to principles.md."""
    principles_path = vault_paths.principles_file
    
    # Format principle
    title_part = f"## {proposal.title}\n\n" if proposal.title else ""
    principle_block = f"\n{title_part}{proposal.content}\n"
    
    # Append
    if principles_path.exists():
        existing = principles_path.read_text(encoding="utf-8")
        principles_path.write_text(existing + principle_block, encoding="utf-8")
    else:
        principles_path.write_text(f"# Personal Principles\n{principle_block}", encoding="utf-8")


def _append_to_daily(proposal: ProposedArtifact, vault_paths: VaultPaths) -> Path:
    """Append memory to daily note."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_path = vault_paths.daily_note_path(date_str)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Format memory block
    title_part = f"### {proposal.title}\n\n" if proposal.title else ""
    memory_block = f"\n{title_part}{proposal.content}\n"
    
    # Append or create
    if daily_path.exists():
        existing = daily_path.read_text(encoding="utf-8")
        daily_path.write_text(existing + memory_block, encoding="utf-8")
    else:
        daily_path.write_text(f"# Daily Notes — {date_str}\n{memory_block}", encoding="utf-8")
    
    return daily_path


def _write_decision_file(proposal: ProposedArtifact, vault_paths: VaultPaths) -> Path:
    """Write a decision as a file."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Use corrections folder for decisions
    decisions_folder = vault_paths.corrections / "decisions"
    decisions_folder.mkdir(parents=True, exist_ok=True)
    
    filename = f"decision_{date_str}_{proposal.proposal_id[:8]}.md"
    decision_path = decisions_folder / filename
    
    # Format decision content
    lines = []
    if proposal.title:
        lines.append(f"# {proposal.title}")
    else:
        lines.append(f"# Decision — {date_str}")
    lines.append("")
    lines.append(proposal.content)
    
    decision_path.write_text("\n".join(lines), encoding="utf-8")
    return decision_path


def _add_entity(proposal: ProposedArtifact, vault_paths: VaultPaths) -> None:
    """Add an entity to entities.json."""
    entities_path = vault_paths.entities_file
    
    # Load existing
    if entities_path.exists():
        try:
            entities = json.loads(entities_path.read_text(encoding="utf-8"))
            if not isinstance(entities, list):
                entities = []
        except json.JSONDecodeError:
            entities = []
    else:
        entities = []
    
    # Parse entity from content (expecting JSON or simple format)
    try:
        entity_data = json.loads(proposal.content)
    except json.JSONDecodeError:
        # Simple format: use content as name
        entity_data = {
            "name": proposal.title or proposal.content[:50],
            "kind": "topic",
            "note": proposal.content if not proposal.title else None,
        }
    
    # Add metadata
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entity_data["first_seen_at"] = now_iso
    entity_data["last_seen_at"] = now_iso
    entity_data["proposal_id"] = proposal.proposal_id
    
    entities.append(entity_data)
    entities_path.write_text(json.dumps(entities, indent=2), encoding="utf-8")


def write_corrected_artifact(
    override: OverrideArtifact,
    original_proposal: ProposedArtifact,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
) -> Path:
    """Write a corrected artifact to canon.
    
    Args:
        override: The override record
        original_proposal: Original proposal that was corrected
        vault_paths: VaultPaths instance
        ledger_writer: LedgerWriter for logging
        
    Returns:
        Path to the written artifact
    """
    # Create a new proposal from the corrected data
    corrected_proposal = ProposedArtifact(
        proposal_id=str(uuid.uuid4()),
        capture_id=original_proposal.capture_id,
        run_id=original_proposal.run_id,
        artifact_type=override.corrected_artifact_type,
        title=override.corrected_title,
        content=override.corrected_content,
        destination=override.corrected_destination or original_proposal.destination,
        rationale=f"Corrected from {original_proposal.proposal_id[:8]}",
        confidence=1.0,  # User-corrected = full confidence
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source_file=original_proposal.source_file,
    )
    
    # Write using same logic as approved
    output_path = write_approved_artifact(
        corrected_proposal,
        vault_paths,
        ledger_writer,
    )
    
    # Save override record
    _save_override_record(override, vault_paths)
    
    # Log correction event
    ledger_writer.append_event(
        event_type="REVIEW_CORRECTED",
        capture_id=original_proposal.capture_id,
        payload={
            "proposal_id": original_proposal.proposal_id,
            "override_id": override.override_id,
            "original_type": original_proposal.artifact_type.value,
            "corrected_type": override.corrected_artifact_type.value,
            "destination": str(output_path.relative_to(vault_paths.root)),
        },
    )
    
    return output_path


def _save_override_record(override: OverrideArtifact, vault_paths: VaultPaths) -> Path:
    """Save override record to corrections folder."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overrides_folder = vault_paths.corrections / "overrides" / date_str
    overrides_folder.mkdir(parents=True, exist_ok=True)
    
    override_path = overrides_folder / f"{override.override_id}.json"
    override_path.write_text(override.model_dump_json(indent=2), encoding="utf-8")
    
    return override_path


# ============================================================================
# Review Session
# ============================================================================


class ReviewSession:
    """Interactive review session for proposed artifacts.
    
    Handles the single-keystroke review loop with Approve/Veto/Correct/Defer actions.
    """

    def __init__(
        self,
        review_queue: ReviewQueue,
        learning_logger: LearningEventLogger,
        vault_paths: VaultPaths,
        ledger_writer: LedgerWriter,
        key_source: KeyInputSource | None = None,
        dry_run: bool = False,
        output_fn: Callable[[str], None] | None = None,
    ):
        """Initialize review session.
        
        Args:
            review_queue: ReviewQueue instance
            learning_logger: LearningEventLogger instance
            vault_paths: VaultPaths instance
            ledger_writer: LedgerWriter instance
            key_source: Optional KeyInputSource for testing
            dry_run: If True, don't write artifacts (UI only)
            output_fn: Optional output function (default: print)
        """
        self.queue = review_queue
        self.logger = learning_logger
        self.vault_paths = vault_paths
        self.ledger_writer = ledger_writer
        self.key_source = key_source or KeyInputSource()
        self.dry_run = dry_run
        self.output = output_fn or print
        
        # Session stats
        self.approved_count = 0
        self.vetoed_count = 0
        self.corrected_count = 0
        self.deferred_count = 0

    def run(self, limit: int | None = None) -> dict:
        """Run the review session.
        
        Args:
            limit: Maximum number of items to process
            
        Returns:
            Summary dict with counts
        """
        # Get pending items
        items = self.queue.get_pending_items(limit=limit)
        
        if not items:
            self.output("No pending proposals to review.")
            return self._get_summary()
        
        self.output(f"Found {len(items)} pending proposal(s) to review.\n")
        
        for i, item in enumerate(items, 1):
            self.output(f"[{i}/{len(items)}]")
            
            action = self._review_item(item)
            
            if action == "quit":
                self.output("\nReview session ended.")
                break
        
        return self._get_summary()

    def _review_item(self, item: ReviewQueueItem) -> str:
        """Review a single item.
        
        Args:
            item: The item to review
            
        Returns:
            Action taken: "approve", "veto", "correct", "defer", or "quit"
        """
        proposal = item.proposal
        
        # Display proposal
        self.output(format_proposal_display(proposal))
        
        while True:
            key = self.key_source.get_key().lower()
            
            if key == "a":
                self._handle_approve(item)
                return "approve"
            elif key == "v":
                self._handle_veto(item)
                return "veto"
            elif key == "c":
                result = self._handle_correct(item)
                return result  # "correct" or "cancel"
            elif key == "d":
                self._handle_defer(item)
                return "defer"
            elif key == "q":
                return "quit"
            else:
                # Invalid key, show hint
                self.output("  Press A/V/C/D/Q")

    def _handle_approve(self, item: ReviewQueueItem) -> None:
        """Handle approve action."""
        proposal = item.proposal
        
        if not self.dry_run:
            # Write artifact to canon
            output_path = write_approved_artifact(
                proposal,
                self.vault_paths,
                self.ledger_writer,
            )
            
            # Update queue status
            self.queue.update_status(proposal.proposal_id, ProposalStatus.APPROVED)
            
            # Log learning event
            self.logger.log_event(
                ReviewEventType.APPROVED,
                proposal,
                {"destination": str(output_path.relative_to(self.vault_paths.root))},
            )
            
            self.output(f"  -> APPROVED: written to {output_path.name}")
        else:
            self.output("  -> [DRY-RUN] Would approve and write artifact")
        
        self.approved_count += 1

    def _handle_veto(self, item: ReviewQueueItem) -> None:
        """Handle veto action."""
        proposal = item.proposal
        
        if not self.dry_run:
            # Update queue status (no canon write)
            self.queue.update_status(proposal.proposal_id, ProposalStatus.VETOED)
            
            # Log learning event
            self.logger.log_event(
                ReviewEventType.VETOED,
                proposal,
                {"reason": "user_vetoed"},
            )
            
            # Log to ledger
            self.ledger_writer.append_event(
                event_type="REVIEW_VETOED",
                capture_id=proposal.capture_id,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "artifact_type": proposal.artifact_type.value,
                },
            )
            
            self.output("  -> VETOED: discarded (learning event logged)")
        else:
            self.output("  -> [DRY-RUN] Would veto and discard")
        
        self.vetoed_count += 1

    def _handle_defer(self, item: ReviewQueueItem) -> None:
        """Handle defer action."""
        proposal = item.proposal
        
        if not self.dry_run:
            # Update queue status
            self.queue.update_status(proposal.proposal_id, ProposalStatus.DEFERRED)
            
            # Log learning event
            self.logger.log_event(
                ReviewEventType.DEFERRED,
                proposal,
                {"defer_count": item.defer_count + 1},
            )
            
            # Log to ledger
            self.ledger_writer.append_event(
                event_type="REVIEW_DEFERRED",
                capture_id=proposal.capture_id,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "artifact_type": proposal.artifact_type.value,
                    "defer_count": item.defer_count + 1,
                },
            )
            
            self.output(f"  -> DEFERRED: will review later (count: {item.defer_count + 1})")
        else:
            self.output("  -> [DRY-RUN] Would defer")
        
        self.deferred_count += 1

    def _handle_correct(self, item: ReviewQueueItem) -> str:
        """Handle correct action (override mode).
        
        Returns:
            "correct" if correction applied, "cancel" if cancelled
        """
        proposal = item.proposal
        
        self.output("\n--- CORRECTION MODE ---\n")
        
        # Prompt for artifact type
        self.output(f"Artifact type [{proposal.artifact_type.value}]: ")
        type_input = self.key_source.get_line("").strip()
        if type_input:
            try:
                corrected_type = ArtifactType(type_input.lower())
            except ValueError:
                self.output(f"  Invalid type, using original: {proposal.artifact_type.value}")
                corrected_type = proposal.artifact_type
        else:
            corrected_type = proposal.artifact_type
        
        # Prompt for title
        default_title = proposal.title or ""
        self.output(f"Title [{default_title}]: ")
        title_input = self.key_source.get_line("").strip()
        corrected_title = title_input if title_input else proposal.title
        
        # Prompt for content
        self.output("\nContent (enter new content, end with '.' on its own line):")
        self.output(f"[Current: {proposal.content[:100]}{'...' if len(proposal.content) > 100 else ''}]")
        corrected_content = get_multiline_input(self.key_source, "")
        if not corrected_content.strip():
            corrected_content = proposal.content
        
        # Prompt for destination
        self.output(f"\nDestination [{proposal.destination}]: ")
        dest_input = self.key_source.get_line("").strip()
        corrected_destination = dest_input if dest_input else None
        
        # Show confirmation
        self.output(format_correction_confirmation(
            proposal,
            corrected_type,
            corrected_title,
            corrected_content,
            corrected_destination,
        ))
        
        # Wait for confirmation
        while True:
            confirm_key = self.key_source.get_key().lower()
            
            if confirm_key == "a":
                # Apply correction
                override = OverrideArtifact(
                    override_id=str(uuid.uuid4()),
                    proposal_id=proposal.proposal_id,
                    original_summary=f"{proposal.artifact_type.value}: {proposal.content[:200]}",
                    corrected_artifact_type=corrected_type,
                    corrected_title=corrected_title,
                    corrected_content=corrected_content,
                    corrected_destination=corrected_destination,
                    created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                
                if not self.dry_run:
                    # Write corrected artifact
                    output_path = write_corrected_artifact(
                        override,
                        proposal,
                        self.vault_paths,
                        self.ledger_writer,
                    )
                    
                    # Update queue status
                    self.queue.update_status(proposal.proposal_id, ProposalStatus.CORRECTED)
                    
                    # Log learning event
                    self.logger.log_event(
                        ReviewEventType.CORRECTED,
                        proposal,
                        {
                            "override_id": override.override_id,
                            "original_type": proposal.artifact_type.value,
                            "corrected_type": corrected_type.value,
                        },
                    )
                    
                    self.output(f"  -> CORRECTED: written to {output_path.name}")
                else:
                    self.output("  -> [DRY-RUN] Would apply correction")
                
                self.corrected_count += 1
                return "correct"
            
            elif confirm_key == "v":
                self.output("  -> Correction cancelled")
                return "cancel"
            
            else:
                self.output("  Press A to approve correction, V to cancel")

    def _get_summary(self) -> dict:
        """Get session summary."""
        return {
            "approved": self.approved_count,
            "vetoed": self.vetoed_count,
            "corrected": self.corrected_count,
            "deferred": self.deferred_count,
            "total": self.approved_count + self.vetoed_count + self.corrected_count + self.deferred_count,
        }


# ============================================================================
# Proposal Generation from Existing Pipeline
# ============================================================================


def generate_proposals_from_distill(
    vault_paths: VaultPaths,
    date_str: str,
) -> list[ProposedArtifact]:
    """Generate proposals from existing distill outputs.
    
    Scans 10_derived/distill/ for items and creates proposals.
    
    Args:
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        List of generated ProposedArtifact
    """
    proposals = []
    distill_folder = vault_paths.distill_date_folder(date_str)
    
    if not distill_folder.exists():
        return proposals
    
    for json_file in distill_folder.glob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            
            # Create a proposal from the distill result
            proposal = ProposedArtifact(
                proposal_id=str(uuid.uuid4()),
                capture_id=data.get("capture_id"),
                run_id=None,
                artifact_type=ArtifactType.NOTE,
                title=f"Distill: {data.get('capture_id', 'unknown')[:8]}",
                content=data.get("summary", ""),
                destination=f"20_memory/daily/{date_str}.md",
                rationale=f"Distilled summary from capture with confidence {data.get('confidence', 0):.2f}",
                confidence=data.get("confidence", 0.5),
                created_at=data.get("distilled_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                source_file=str(json_file.relative_to(vault_paths.root)),
            )
            proposals.append(proposal)
            
            # Also create task proposals from extracted tasks
            for task in data.get("tasks", []):
                task_proposal = ProposedArtifact(
                    proposal_id=str(uuid.uuid4()),
                    capture_id=data.get("capture_id"),
                    run_id=None,
                    artifact_type=ArtifactType.TASK,
                    title=None,
                    content=task.get("text", ""),
                    destination="30_tasks/todo.md",
                    rationale=f"Task extracted from distillation of {data.get('capture_id', 'unknown')[:8]}",
                    confidence=data.get("confidence", 0.5) * 0.9,  # Slightly lower for tasks
                    created_at=data.get("distilled_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                    source_file=str(json_file.relative_to(vault_paths.root)),
                )
                proposals.append(task_proposal)
            
            # Create entity proposals
            for entity in data.get("entities", []):
                entity_proposal = ProposedArtifact(
                    proposal_id=str(uuid.uuid4()),
                    capture_id=data.get("capture_id"),
                    run_id=None,
                    artifact_type=ArtifactType.ENTITY,
                    title=entity.get("name"),
                    content=json.dumps(entity),
                    destination="20_memory/entities.json",
                    rationale=f"Entity '{entity.get('name')}' mentioned in capture",
                    confidence=data.get("confidence", 0.5) * 0.8,  # Lower for entities
                    created_at=data.get("distilled_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                    source_file=str(json_file.relative_to(vault_paths.root)),
                )
                proposals.append(entity_proposal)
                
        except (json.JSONDecodeError, KeyError, ValueError):
            # Skip malformed files
            continue
    
    return proposals


def load_or_create_proposals(
    vault_paths: VaultPaths,
    date_str: str,
) -> list[ProposedArtifact]:
    """Load existing proposals or generate from distill outputs.
    
    Args:
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        List of proposals to review
    """
    queue = ReviewQueue(vault_paths.review_queue_file)
    
    # Check for existing pending items
    pending = queue.get_pending_items()
    if pending:
        return [item.proposal for item in pending]
    
    # Generate new proposals from distill
    proposals = generate_proposals_from_distill(vault_paths, date_str)
    
    # Add to queue
    for proposal in proposals:
        queue.add_proposal(proposal)
    
    return proposals
