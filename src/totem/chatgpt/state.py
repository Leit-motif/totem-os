"""State management for ChatGPT export ingestion."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IngestState(BaseModel):
    """State tracking for ChatGPT export ingestion."""

    processed_message_ids: List[str] = Field(default_factory=list)
    last_success_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None

    @classmethod
    def load(cls, state_file: Path) -> "IngestState":
        """Load state from JSON file."""
        if not state_file.exists():
            logger.info(f"State file {state_file} does not exist, creating new state")
            return cls()

        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Parse timestamps
            if 'last_success_at' in data and data['last_success_at']:
                data['last_success_at'] = datetime.fromisoformat(data['last_success_at'])
            if 'last_attempt_at' in data and data['last_attempt_at']:
                data['last_attempt_at'] = datetime.fromisoformat(data['last_attempt_at'])

            return cls(**data)

        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to load state file {state_file}: {e}, using empty state")
            return cls()

    def save(self, state_file: Path) -> None:
        """Save state to JSON file atomically."""
        # Create directory if needed
        state_file.parent.mkdir(parents=True, exist_ok=True)

        # Prepare data for JSON serialization
        data = self.model_dump()
        if self.last_success_at:
            data['last_success_at'] = self.last_success_at.isoformat()
        if self.last_attempt_at:
            data['last_attempt_at'] = self.last_attempt_at.isoformat()

        # Write atomically using temporary file
        temp_file = state_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            temp_file.replace(state_file)
            logger.debug(f"Saved state to {state_file}")

        except OSError as e:
            logger.error(f"Failed to save state to {state_file}: {e}")
            # Clean up temp file if it exists
            temp_file.unlink(missing_ok=True)
            raise

    def is_message_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        return message_id in self.processed_message_ids

    def mark_message_processed(self, message_id: str) -> None:
        """Mark a message as processed."""
        if message_id not in self.processed_message_ids:
            self.processed_message_ids.append(message_id)
            logger.debug(f"Marked message {message_id} as processed")

    def record_success(self) -> None:
        """Record a successful ingestion run."""
        now = datetime.now(timezone.utc)
        self.last_success_at = now
        self.last_attempt_at = now
        self.last_error = None
        logger.info("Recorded successful ingestion")

    def record_attempt(self, error: Optional[str] = None) -> None:
        """Record an ingestion attempt."""
        self.last_attempt_at = datetime.now(timezone.utc)
        self.last_error = error
        if error:
            logger.warning(f"Recorded failed attempt: {error}")
        else:
            logger.debug("Recorded attempt")

    def get_unprocessed_messages(self, all_message_ids: List[str]) -> List[str]:
        """Get message IDs that haven't been processed yet."""
        return [msg_id for msg_id in all_message_ids if not self.is_message_processed(msg_id)]

    def cleanup_old_processed_ids(self, keep_recent: int = 1000) -> None:
        """Keep only the most recent processed message IDs."""
        if len(self.processed_message_ids) > keep_recent:
            # Keep the most recent ones (assuming they were added in order)
            self.processed_message_ids = self.processed_message_ids[-keep_recent:]
            logger.debug(f"Cleaned up old processed IDs, keeping {keep_recent} most recent")