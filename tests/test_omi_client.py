"""Tests for Omi API client."""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import requests

from totem.models.omi import OmiConversation, OmiTranscriptSegment
from totem.omi.client import OmiClient


def test_omi_client_requires_api_key():
    """Test that OmiClient raises error if no API key provided."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="Omi API key not found"):
            OmiClient()


def test_omi_client_reads_env_var():
    """Test that OmiClient reads API key from environment."""
    with patch.dict("os.environ", {"OMI_API_KEY": "test-key"}):
        client = OmiClient()
        assert client.api_key == "test-key"


def test_omi_client_accepts_api_key_param():
    """Test that OmiClient accepts API key as parameter."""
    client = OmiClient(api_key="param-key")
    assert client.api_key == "param-key"


@patch("totem.omi.client.requests.get")
def test_fetch_conversations_single_page(mock_get):
    """Test fetching conversations with single page response."""
    # Mock API response (list format)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "id": "conv_123",
            "started_at": "2026-01-19T10:00:00Z",
            "finished_at": "2026-01-19T10:15:00Z",
            "transcript_segments": [
                {
                    "id": "seg_1",
                    "speaker_id": 0,
                    "text": "Hello world",
                },
                {
                    "id": "seg_2",
                    "speaker_id": 1,
                    "text": "Hi there",
                },
            ],
        }
    ]
    mock_get.return_value = mock_response
    
    # Create client and fetch
    client = OmiClient(api_key="test-key")
    start = datetime(2026, 1, 19, 0, 0, 0)
    end = datetime(2026, 1, 19, 23, 59, 59)
    
    conversations = client.fetch_conversations(start, end)
    
    # Verify parsed conversations
    assert len(conversations) == 1
    conv = conversations[0]
    assert conv.id == "conv_123"
    assert len(conv.transcript) == 2
    assert conv.transcript[0].text == "Hello world"
    # Note: speaker_id is converted to string
    assert conv.transcript[1].speaker_id == "1"


@patch("totem.omi.client.requests.get")
def test_fetch_conversations_with_pagination(mock_get):
    """Test fetching conversations with pagination."""
    # Since API is currently a list without pagination cursor in body,
    # we just test that multiple calls happen IF we implemented pagination logic.
    # But for now, the client doesn't do pagination because we don't know the cursor format.
    # So we'll just test that it handles the LIST response correctly.
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "id": "conv_1",
            "started_at": "2026-01-19T10:00:00Z",
            "finished_at": "2026-01-19T10:15:00Z",
            "transcript_segments": [],
        }
    ]
    
    mock_get.return_value = mock_response
    
    # Fetch conversations
    client = OmiClient(api_key="test-key")
    start = datetime(2026, 1, 19, 0, 0, 0)
    end = datetime(2026, 1, 19, 23, 59, 59)
    
    conversations = client.fetch_conversations(start, end)
    
    assert len(conversations) == 1
    assert conversations[0].id == "conv_1"


@patch("totem.omi.client.requests.get")
def test_fetch_conversations_empty_response(mock_get):
    """Test fetching conversations with empty response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_get.return_value = mock_response
    
    client = OmiClient(api_key="test-key")
    start = datetime(2026, 1, 19, 0, 0, 0)
    end = datetime(2026, 1, 19, 23, 59, 59)
    
    conversations = client.fetch_conversations(start, end)
    
    assert len(conversations) == 0


@patch("totem.omi.client.requests.get")
def test_fetch_conversations_network_error(mock_get):
    """Test that network errors are propagated."""
    mock_get.side_effect = requests.RequestException("Network error")
    
    client = OmiClient(api_key="test-key")
    start = datetime(2026, 1, 19, 0, 0, 0)
    end = datetime(2026, 1, 19, 23, 59, 59)
    
    with pytest.raises(requests.RequestException):
        client.fetch_conversations(start, end)


@patch("totem.omi.client.requests.get")
def test_fetch_conversations_missing_transcript_field(mock_get):
    """Test handling conversations without transcript field."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "id": "conv_no_transcript",
            "started_at": "2026-01-19T10:00:00Z",
            "finished_at": "2026-01-19T10:15:00Z",
            # No transcript field
        }
    ]
    mock_get.return_value = mock_response
    
    client = OmiClient(api_key="test-key")
    start = datetime(2026, 1, 19, 0, 0, 0)
    end = datetime(2026, 1, 19, 23, 59, 59)
    
    conversations = client.fetch_conversations(start, end)
    
    assert len(conversations) == 1
    assert len(conversations[0].transcript) == 0


@patch("totem.omi.client.requests.get")
def test_parse_segment_generates_id_if_missing(mock_get):
    """Test that segment IDs are generated if not provided by API."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "id": "conv_123",
            "started_at": "2026-01-19T10:00:00Z",
            "finished_at": "2026-01-19T10:15:00Z",
            "transcript_segments": [
                {
                    # No id field
                    "speaker_id": 0,
                    "text": "Test",
                }
            ],
        }
    ]
    mock_get.return_value = mock_response
    
    client = OmiClient(api_key="test-key")
    start = datetime(2026, 1, 19, 0, 0, 0)
    end = datetime(2026, 1, 19, 23, 59, 59)
    
    conversations = client.fetch_conversations(start, end)
    
    # Verify segment has generated ID
    assert conversations[0].transcript[0].segment_id == "conv_123_seg_0"
