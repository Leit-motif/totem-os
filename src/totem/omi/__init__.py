"""Omi integration for Totem OS.

Provides API client and transcript sync functionality for Omi conversations.
"""

from .client import OmiClient
from .daily_note import write_daily_note_omi_block
from .writer import write_transcripts_to_vault

__all__ = ["OmiClient", "write_transcripts_to_vault", "write_daily_note_omi_block"]
