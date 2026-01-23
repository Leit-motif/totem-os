"""Parser for ChatGPT export conversation data."""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ChatGptConversation, ChatMessage, ParsedConversations

logger = logging.getLogger(__name__)


def parse_conversations_json(json_path: Path) -> ParsedConversations:
    """Parse conversations from ChatGPT export JSON file.

    Args:
        json_path: Path to the conversations JSON file

    Returns:
        Parsed conversations result
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return ParsedConversations(
            total_count=0,
            parsed_count=0,
            errors=[f"Failed to read/parse JSON: {e}"]
        )

    logger.debug(f"Loaded JSON from {json_path}, type: {type(data)}")
    if isinstance(data, dict):
        logger.debug(f"JSON keys: {list(data.keys())[:10]}")
        if len(data) <= 5:
            logger.debug(f"JSON content: {data}")

    # Handle different export formats
    conversations_data = _extract_conversations_data(data)
    if not conversations_data:
        logger.warning(f"Could not find conversations data in JSON file: {json_path}")
        logger.warning(f"JSON type: {type(data)}")
        if isinstance(data, dict):
            logger.warning(f"Available keys: {list(data.keys())}")
        elif isinstance(data, list):
            logger.warning(f"Array with {len(data)} items")
            if data and isinstance(data[0], dict):
                logger.warning(f"First item keys: {list(data[0].keys()) if data[0] else 'empty'}")

        return ParsedConversations(
            total_count=0,
            parsed_count=0,
            errors=["Could not find conversations data in JSON"]
        )

    conversations = []
    errors = []

    for i, conv_data in enumerate(conversations_data):
        try:
            conversation = _parse_single_conversation(conv_data)
            if conversation:
                conversations.append(conversation)
        except Exception as e:
            errors.append(f"Failed to parse conversation {i}: {e}")
            logger.debug(f"Conversation parse error: {e}", exc_info=True)

    return ParsedConversations(
        conversations=conversations,
        total_count=len(conversations_data),
        parsed_count=len(conversations),
        errors=errors
    )


def _extract_conversations_data(data: Any) -> Optional[List[Dict[str, Any]]]:
    """Extract conversations array from various export formats."""
    # If data is already a list, assume it's conversations
    if isinstance(data, list):
        logger.debug(f"JSON is a list with {len(data)} items")
        return data

    # Check for common top-level keys
    if isinstance(data, dict):
        for key in ['conversations', 'data', 'chats', 'messages', 'conversations_list', 'chat_list']:
            if key in data and isinstance(data[key], list):
                logger.debug(f"Found conversations in key '{key}' with {len(data[key])} items")
                return data[key]

        # Check nested structures
        if 'conversations' in data and isinstance(data['conversations'], dict):
            # Some formats have conversations as a dict with IDs as keys
            conv_list = list(data['conversations'].values())
            logger.debug(f"Found conversations as dict values with {len(conv_list)} items")
            return conv_list

        # Check if the entire dict represents a single conversation
        if any(key in data for key in ['id', 'conversation_id', 'title', 'messages']):
            logger.debug("JSON appears to be a single conversation, wrapping in list")
            return [data]

    logger.debug(f"Could not extract conversations from data type: {type(data)}")
    return None


def _parse_single_conversation(conv_data: Dict[str, Any]) -> Optional[ChatGptConversation]:
    """Parse a single conversation from raw data."""
    if not isinstance(conv_data, dict):
        return None

    # Extract conversation ID
    conv_id = _extract_conversation_id(conv_data)
    if not conv_id:
        return None

    # Extract title
    title = _extract_title(conv_data)

    # Extract timestamps
    created_at = _extract_created_at(conv_data)
    updated_at = _extract_updated_at(conv_data)

    if not created_at:
        # Fallback to current time if no timestamp
        created_at = datetime.now()
    if not updated_at:
        updated_at = created_at

    # Extract messages
    messages = _extract_messages(conv_data)

    return ChatGptConversation(
        conversation_id=conv_id,
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        messages=messages
    )


def _extract_conversation_id(conv_data: Dict[str, Any]) -> Optional[str]:
    """Extract conversation ID from various formats."""
    # Try different possible keys
    for key in ['id', 'conversation_id', 'chat_id', 'uuid']:
        if key in conv_data and conv_data[key]:
            return str(conv_data[key])

    # Generate ID from title and timestamp if available
    title = conv_data.get('title', '')
    created = conv_data.get('create_time', conv_data.get('created_at', ''))
    if title or created:
        id_source = f"{title}_{created}"
        return hashlib.md5(id_source.encode()).hexdigest()[:16]

    return None


def _extract_title(conv_data: Dict[str, Any]) -> str:
    """Extract conversation title."""
    for key in ['title', 'name', 'subject']:
        if key in conv_data and conv_data[key]:
            title = str(conv_data[key]).strip()
            if title:
                return title

    return "Untitled Conversation"


def _extract_created_at(conv_data: Dict[str, Any]) -> Optional[datetime]:
    """Extract creation timestamp."""
    return _parse_timestamp(conv_data, ['create_time', 'created_at', 'created', 'timestamp'])


def _extract_updated_at(conv_data: Dict[str, Any]) -> Optional[datetime]:
    """Extract update timestamp."""
    # Try update_time first, then fallback to create_time, then earliest message time
    updated = _parse_timestamp(conv_data, ['update_time', 'updated_at', 'modified_at'])

    if updated:
        return updated

    # Fallback to creation time
    created = _extract_created_at(conv_data)
    if created:
        return created

    # Fallback to earliest message timestamp
    messages = _extract_messages(conv_data)
    if messages:
        message_times = [msg.created_at for msg in messages if msg.created_at]
        if message_times:
            return min(message_times)

    return None


def _parse_timestamp(data: Dict[str, Any], keys: List[str]) -> Optional[datetime]:
    """Parse timestamp from various formats."""
    for key in keys:
        if key in data and data[key]:
            value = data[key]

            # Handle numeric timestamps (Unix epoch)
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(value)
                except (ValueError, OSError):
                    continue

            # Handle string timestamps
            if isinstance(value, str):
                # Try ISO format first
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    pass

                # Try common date formats
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue

    return None


def _extract_messages(conv_data: Dict[str, Any]) -> List[ChatMessage]:
    """Extract messages from conversation data."""
    messages: List[ChatMessage] = []

    # Mapping-based export format
    mapping = conv_data.get("mapping")
    if isinstance(mapping, dict):
        messages = _extract_messages_from_mapping(mapping)
        if messages:
            return _sort_messages(messages)

    # Message-list export format
    for msg_key in ["messages", "message", "turns", "history"]:
        if msg_key in conv_data and isinstance(conv_data[msg_key], list):
            messages = _extract_messages_from_list(conv_data[msg_key])
            return _sort_messages(messages)

    return messages


def _extract_messages_from_mapping(mapping: Dict[str, Any]) -> List[ChatMessage]:
    """Extract messages from mapping-based export format."""
    messages: List[ChatMessage] = []

    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        msg_data = node.get("message")
        if not isinstance(msg_data, dict):
            continue
        message = _parse_single_message(msg_data)
        if message:
            messages.append(message)

    return messages


def _extract_messages_from_list(raw_messages: List[Any]) -> List[ChatMessage]:
    """Extract messages from list-based export format."""
    messages: List[ChatMessage] = []

    for msg_data in raw_messages:
        if not isinstance(msg_data, dict):
            continue

        try:
            message = _parse_single_message(msg_data)
            if message:
                messages.append(message)
        except Exception as e:
            logger.debug(f"Failed to parse message: {e}")
            continue

    return messages


def _sort_messages(messages: List[ChatMessage]) -> List[ChatMessage]:
    """Sort messages by timestamp when available."""
    if not messages:
        return messages

    if not any(msg.created_at for msg in messages):
        return messages

    def sort_key(msg: ChatMessage) -> tuple:
        if msg.created_at:
            try:
                return (0, msg.created_at.timestamp())
            except (ValueError, OSError):
                return (0, 0.0)
        return (1, 0.0)

    return sorted(messages, key=sort_key)


def _parse_single_message(msg_data: Dict[str, Any]) -> Optional[ChatMessage]:
    """Parse a single message."""
    # Extract role
    role = _extract_message_role(msg_data)
    if not role or role not in ["user", "assistant"]:
        return None

    # Extract content
    content = _extract_message_content(msg_data)
    if not content or not content.strip():
        return None

    # Extract timestamp
    timestamp = _parse_timestamp(
        msg_data,
        ['create_time', 'update_time', 'timestamp', 'time', 'created_at']
    )

    return ChatMessage(
        role=role,
        content=content.strip(),
        created_at=timestamp,
    )


def _extract_message_role(msg_data: Dict[str, Any]) -> Optional[str]:
    """Extract message role (user/assistant/system)."""
    for key in ['role', 'author', 'sender', 'type']:
        if key in msg_data and msg_data[key]:
            value = msg_data[key]
            if key == "author" and isinstance(value, dict):
                value = value.get("role", "")
            role = str(value).lower().strip()
            # Normalize role names
            if role in ['user', 'human']:
                return 'user'
            if role in ['assistant', 'ai', 'bot', 'gpt']:
                return 'assistant'
            if role in ['system', 'meta', 'tool', 'function']:
                return 'system'

    # Infer from content structure
    if 'content' in msg_data and 'response' not in msg_data:
        return 'user'
    elif 'response' in msg_data:
        return 'assistant'

    return None


def _extract_message_content(msg_data: Dict[str, Any]) -> Optional[str]:
    """Extract message content text."""
    for key in ['content', 'text', 'message', 'body', 'response']:
        if key in msg_data and msg_data[key]:
            content = msg_data[key]

            # Handle nested content structures
            if isinstance(content, dict):
                # ChatGPT export format: content.parts
                if "parts" in content and isinstance(content["parts"], list):
                    parts = [
                        str(part)
                        for part in content["parts"]
                        if isinstance(part, str) and part.strip()
                    ]
                    if parts:
                        return "\n".join(parts)

                if "text" in content and content["text"]:
                    return str(content["text"])

                # Try common nested keys
                for nested_key in ['text', 'value', 'content']:
                    if nested_key in content and content[nested_key]:
                        return str(content[nested_key])
            else:
                return str(content)

    return None
