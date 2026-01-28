"""State persistence for ChatGPT ingestion."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STATE_SCHEMA_VERSION = 1


@dataclass
class ChatGptConversationState:
    destination_vault: str
    output_note_relpath: str


@dataclass
class ChatGptIngestState:
    schema_version: int = STATE_SCHEMA_VERSION
    conversations: dict[str, ChatGptConversationState] = field(default_factory=dict)
    updated_at: Optional[str] = None


def load_ingest_state(state_path: Path) -> ChatGptIngestState:
    if not state_path.exists():
        return ChatGptIngestState(updated_at=_now_utc())
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ChatGptIngestState(updated_at=_now_utc())

    conversations = {}
    for conv_id, entry in (raw.get("conversations") or {}).items():
        dest = entry.get("destination_vault")
        relpath = entry.get("output_note_relpath")
        if not dest or not relpath:
            continue
        conversations[conv_id] = ChatGptConversationState(
            destination_vault=dest,
            output_note_relpath=relpath,
        )

    return ChatGptIngestState(
        schema_version=raw.get("schema_version", STATE_SCHEMA_VERSION),
        conversations=conversations,
        updated_at=raw.get("updated_at") or _now_utc(),
    )


def save_ingest_state(state_path: Path, state: ChatGptIngestState) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": state.schema_version,
        "updated_at": _now_utc(),
        "conversations": {
            conv_id: {
                "destination_vault": entry.destination_vault,
                "output_note_relpath": entry.output_note_relpath,
            }
            for conv_id, entry in state.conversations.items()
        },
    }
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(state_path)


def get_conversation_state(
    state: ChatGptIngestState, conversation_id: str
) -> Optional[ChatGptConversationState]:
    return state.conversations.get(conversation_id)


def set_conversation_state(
    state: ChatGptIngestState,
    conversation_id: str,
    destination_vault: str,
    output_note_relpath: str,
) -> None:
    state.conversations[conversation_id] = ChatGptConversationState(
        destination_vault=destination_vault,
        output_note_relpath=output_note_relpath,
    )
    state.updated_at = _now_utc()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
