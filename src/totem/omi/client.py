"""Omi API client for fetching conversation transcripts."""

import os
from datetime import datetime
from typing import Any

import requests

from ..models.omi import OmiConversation, OmiTranscriptSegment


class OmiClient:
    """Client for Omi Developer API.
    
    Fetches conversation transcripts from the Omi API.
    """
    
    BASE_URL = "https://api.omi.me/v1/dev"
    
    def __init__(self, api_key: str | None = None):
        """Initialize Omi API client.
        
        Args:
            api_key: Omi API key. If None, reads from OMI_API_KEY env var.
            
        Raises:
            ValueError: If no API key is provided or found in environment
        """
        self.api_key = api_key or os.getenv("OMI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Omi API key not found. Set OMI_API_KEY environment variable "
                "or pass api_key parameter."
            )
    
    def fetch_conversations(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[OmiConversation]:
        """Fetch conversations for a date range.
        
        Args:
            start_date: Start of date range (inclusive)
            end_date: End of date range (inclusive)
            
        Returns:
            List of OmiConversation objects with transcript segments
            
        Raises:
            requests.RequestException: If API request fails
        """
        conversations: list[OmiConversation] = []
        
        # Format dates as ISO 8601 strings
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()
        
        # Build request parameters
        params = {
            "start_date": start_str,
            "end_date": end_str,
            "include_transcript": "true",
            "limit": 100,
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        
        endpoint = f"{self.BASE_URL}/user/conversations"
        
        # Fetch first page
        response = requests.get(endpoint, params=params, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Parse conversations from response (handles list directly)
        if isinstance(data, list):
            for conv_data in data:
                conversations.append(self._parse_conversation(conv_data))
        elif isinstance(data, dict) and "conversations" in data:
             # Legacy/Wrapper handling just in case
            for conv_data in data["conversations"]:
                conversations.append(self._parse_conversation(conv_data))
        
        if "offset" in params:
            # Simple offset-based pagination if supported, otherwise just break
            # The current API seems to be list-based without explicit cursor in body
            # We will implement cursor if/when we see it in the response or headers
            pass

        return conversations
    
    def _parse_conversation(self, data: dict[str, Any]) -> OmiConversation:
        """Parse conversation data from API response.
        
        Args:
            data: Raw conversation data from API
            
        Returns:
            OmiConversation object
        """
        # Parse transcript segments
        segments: list[OmiTranscriptSegment] = []
        
        # Field is 'transcript_segments' in actual API, but fall back to 'transcript' just in case
        transcript_data = data.get("transcript_segments") or data.get("transcript")
        
        if transcript_data:
            for i, seg_data in enumerate(transcript_data):
                # Generate segment ID if not provided
                seg_id = seg_data.get("id", f"{data['id']}_seg_{i}")
                
                # Speaker handling: prefers speaker_id (int) or speaker (legacy)
                speaker_val = seg_data.get("speaker_id")
                if speaker_val is None:
                    speaker_val = seg_data.get("speaker", "SPEAKER_00")
                
                segment = OmiTranscriptSegment(
                    segment_id=seg_id,
                    speaker_id=str(speaker_val),
                    text=seg_data.get("text", ""),
                    timestamp=self._parse_timestamp(seg_data.get("created_at")), 
                )
                segments.append(segment)
        
        # Parse optional metadata fields
        # Try both top-level and structured_data locations
        structured = data.get("structured", {})
        
        overview = data.get("overview") or structured.get("overview")
        
        # Action items can be in various formats
        action_items_raw = data.get("action_items") or structured.get("action_items") or []
        action_items = []
        if isinstance(action_items_raw, list):
            # Could be list of strings or list of dicts with 'description' field
            for item in action_items_raw:
                if isinstance(item, str):
                    action_items.append(item)
                elif isinstance(item, dict) and "description" in item:
                    action_items.append(item["description"])
                elif isinstance(item, dict) and "text" in item:
                    action_items.append(item["text"])
        
        category = data.get("category") or structured.get("category")
        emoji = data.get("emoji") or structured.get("emoji")
        location = data.get("location") or structured.get("location")
        
        # Parse conversation
        return OmiConversation(
            id=data["id"],
            started_at=self._parse_timestamp(data.get("started_at") or data.get("created_at")),
            finished_at=self._parse_timestamp(data.get("finished_at") or data.get("created_at")),
            transcript=segments,
            overview=overview,
            action_items=action_items,
            category=category,
            emoji=emoji,
            location=location,
        )
    
    def _parse_timestamp(self, ts: str | None) -> datetime:
        """Parse timestamp string to datetime.
        
        Args:
            ts: ISO 8601 timestamp string
            
        Returns:
            datetime object
        """
        if not ts:
            return datetime.now()
        
        # Handle various ISO 8601 formats
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.now()
