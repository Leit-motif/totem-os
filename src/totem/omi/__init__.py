"""Omi integration for Totem OS.

Provides API client and transcript sync functionality for Omi conversations.
"""

from .client import OmiClient
from .writer import write_transcripts_to_vault

__all__ = ["OmiClient", "write_transcripts_to_vault"]
