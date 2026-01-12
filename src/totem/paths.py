"""Path management and vault structure for Totem OS."""

from pathlib import Path
from typing import Optional

from .config import TotemConfig


class VaultPaths:
    """Manages paths within the Totem vault structure."""

    def __init__(self, vault_root: Path):
        """Initialize vault paths from root directory.
        
        Args:
            vault_root: Root directory of the Totem vault
        """
        self.root = vault_root
        
        # Top-level directories
        self.inbox = vault_root / "00_inbox"
        self.derived = vault_root / "10_derived"
        self.memory = vault_root / "20_memory"
        self.tasks = vault_root / "30_tasks"
        self.system = vault_root / "90_system"
        
        # Derived subdirectories
        self.transcripts = self.derived / "transcripts"
        self.routed = self.derived / "routed"
        self.distill = self.derived / "distill"
        self.review_queue = self.derived / "review_queue"
        self.corrections = self.derived / "corrections"
        
        # Memory subdirectories
        self.daily = self.memory / "daily"
        
        # System subdirectories
        self.traces = self.system / "traces"
        self.traces_writes = self.traces / "writes"
        self.traces_routing = self.traces / "routing"
        
        # System files
        self.config_file = self.system / "config.yaml"
        self.ledger_file = self.system / "ledger.jsonl"
        
        # Memory files
        self.entities_file = self.memory / "entities.json"
        self.principles_file = self.memory / "principles.md"
        
        # Task files
        self.todo_file = self.tasks / "todo.md"

    @classmethod
    def from_config(cls, config: TotemConfig) -> "VaultPaths":
        """Create VaultPaths from a TotemConfig."""
        return cls(config.vault_path)

    def get_all_directories(self) -> list[Path]:
        """Get list of all directories that should exist in the vault."""
        return [
            self.inbox,
            self.derived,
            self.transcripts,
            self.routed,
            self.distill,
            self.review_queue,
            self.corrections,
            self.memory,
            self.daily,
            self.tasks,
            self.system,
            self.traces,
            self.traces_writes,
            self.traces_routing,
        ]

    def inbox_date_folder(self, date_str: str) -> Path:
        """Get path to inbox folder for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the inbox date folder
        """
        return self.inbox / date_str
    
    def routed_date_folder(self, date_str: str) -> Path:
        """Get path to routed folder for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the routed date folder
        """
        return self.routed / date_str
    
    def review_queue_date_folder(self, date_str: str) -> Path:
        """Get path to review queue folder for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the review queue date folder
        """
        return self.review_queue / date_str
    
    def distill_date_folder(self, date_str: str) -> Path:
        """Get path to distill folder for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the distill date folder
        """
        return self.distill / date_str
    
    def traces_writes_date_folder(self, date_str: str) -> Path:
        """Get path to traces/writes folder for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the traces/writes date folder
        """
        return self.traces_writes / date_str

    def traces_routing_date_folder(self, date_str: str) -> Path:
        """Get path to traces/routing folder for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the traces/routing date folder
        """
        return self.traces_routing / date_str

    def daily_note_path(self, date_str: str) -> Path:
        """Get path to daily note for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Path to the daily note markdown file
        """
        return self.daily / f"{date_str}.md"
