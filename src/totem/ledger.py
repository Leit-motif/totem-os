"""Append-only ledger writer for Totem OS."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from rich.console import Console

from .models.ledger import LedgerEvent

console = Console()


class LedgerWriter:
    """Append-only ledger writer.
    
    Writes events to <vault>/90_system/ledger.jsonl.
    Never truncates or rewrites; only appends.
    """

    def __init__(self, ledger_path: Path, run_id: str | None = None):
        """Initialize ledger writer.
        
        Args:
            ledger_path: Path to ledger.jsonl file
            run_id: Optional run ID; if None, generates a new uuid4
        """
        self.ledger_path = ledger_path
        self.run_id = run_id or str(uuid.uuid4())

    def append_event(
        self,
        event_type: Literal[
            "CAPTURE_INGESTED",
            "CAPTURE_META_GENERATED",
            "DERIVED_TRANSCRIPT_CREATED",
            "CAPTURE_ROUTED",
            "FLAGGED_FOR_REVIEW",
            "DISTILL_RUN_STARTED",
            "DISTILL_RESULT_WRITTEN",
            "DISTILL_APPLIED",
            "DISTILL_SIMULATED",
            "DISTILL_UNDONE",
            "TASKS_UPDATED",
            "MEMORY_PROMOTED",
            "CORRECTION_APPLIED",
            # Milestone 6: Review events
            "REVIEW_APPROVED",
            "REVIEW_VETOED",
            "REVIEW_DEFERRED",
            "REVIEW_CORRECTED",
            # Milestone 7: Intent Arbiter
            "INTENT_DECISION",
            # Milestone 7.5: Omi sync events
            "OMI_SYNC_FETCHED",
            "OMI_TRANSCRIPT_WRITTEN",
        ],
        payload: dict,
        capture_id: str | None = None,
    ) -> LedgerEvent:
        """Append an event to the ledger.
        
        Args:
            event_type: Type of event
            payload: Event-specific data
            capture_id: Optional capture ID reference
            
        Returns:
            The created LedgerEvent
        """
        # Ensure parent directory exists
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

        # Create event
        event = LedgerEvent(
            event_id=str(uuid.uuid4()),
            run_id=self.run_id,
            ts=datetime.now(timezone.utc),
            event_type=event_type,
            capture_id=capture_id,
            payload=payload,
        )

        # Append to ledger (JSONL format: one JSON object per line)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            # Use model_dump with mode='json' to ensure datetime serialization
            json_str = json.dumps(event.model_dump(mode="json"))
            f.write(json_str + "\n")

        return event


def read_ledger_tail(ledger_path: Path, n: int = 20) -> list[LedgerEvent]:
    """Read the last N events from the ledger.
    
    Robust parsing: skips malformed lines with a warning.
    
    Args:
        ledger_path: Path to ledger.jsonl file
        n: Number of events to read from the end
        
    Returns:
        List of LedgerEvent objects (last N events)
    """
    if not ledger_path.exists():
        return []

    events: list[LedgerEvent] = []
    malformed_count = 0

    # Read all lines (for simplicity; could optimize for very large files)
    with open(ledger_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Process last N lines
    for line in lines[-n:] if len(lines) > n else lines:
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
            event = LedgerEvent(**data)
            events.append(event)
        except (json.JSONDecodeError, ValueError) as e:
            malformed_count += 1
            console.print(f"[yellow]Warning: Skipping malformed line: {e}[/yellow]")

    if malformed_count > 0:
        console.print(f"[yellow]Skipped {malformed_count} malformed line(s)[/yellow]")

    return events
