"""Tests for ChatGPT routing between daemon and tooling vaults."""

import json
from pathlib import Path

import pytest

from totem.chatgpt.local_ingest import ingest_from_zip_with_summary
from totem.config import (
    ChatGptExportConfig,
    ObsidianConfig,
    ObsidianVaultsConfig,
    TotemConfig,
)
from totem.ledger import LedgerWriter
from totem.paths import VaultPaths


def _create_zip(zip_path: Path, payload: list[dict]) -> None:
    import zipfile

    with zipfile.ZipFile(zip_path, "w") as zip_ref:
        zip_ref.writestr("conversations.json", json.dumps(payload))


def _make_config(vault_root: Path, daemon_root: Path, tooling_root: Path) -> TotemConfig:
    chatgpt_config = ChatGptExportConfig(
        staging_dir="state/chatgpt_exports",
        obsidian_chatgpt_dir="40_chatgpt/conversations",
        tooling_chatgpt_dir="ChatGPT",
        obsidian_daily_dir="40_chatgpt/daily",
        timezone="America/Chicago",
    )
    return TotemConfig(
        vault_path=vault_root,
        chatgpt_export=chatgpt_config,
        obsidian=ObsidianConfig(
            vaults=ObsidianVaultsConfig(
                daemon_path=str(daemon_root),
                tooling_path=str(tooling_root),
            ),
        ),
    )


def test_chatgpt_routing_daemon_and_tooling(tmp_path):
    vault_root = tmp_path / "vault"
    daemon_root = tmp_path / "daemon_vault"
    tooling_root = tmp_path / "tooling_vault"

    config = _make_config(vault_root, daemon_root, tooling_root)
    paths = VaultPaths.from_config(config)
    ledger_writer = LedgerWriter(paths.ledger_file)

    payload = [
        {
            "id": "conv_daemon",
            "title": "Morning reflection",
            "create_time": "2026-01-22T03:45:25Z",
            "update_time": "2026-01-22T03:46:00Z",
            "messages": [
                {"role": "user", "content": "Thinking about values and priorities."},
                {"role": "assistant", "content": "Let's explore your goals."},
            ],
        },
        {
            "id": "conv_tooling",
            "title": "Fix Python error",
            "create_time": "2026-01-22T04:00:00Z",
            "update_time": "2026-01-22T04:05:00Z",
            "messages": [
                {"role": "user", "content": "Here's the stack:\n```python\nprint('hi')\n```\n"},
                {"role": "assistant", "content": "Try this:\n```bash\npython -m pytest\n```\n"},
            ],
        },
    ]

    zip_path = tmp_path / "export.zip"
    _create_zip(zip_path, payload)

    ingest_from_zip_with_summary(
        config=config,
        vault_paths=paths,
        ledger_writer=ledger_writer,
        zip_path=zip_path,
    )

    daemon_notes = list(daemon_root.rglob("Morning reflection.md"))
    tooling_notes = list(tooling_root.rglob("Fix Python error.md"))

    assert daemon_notes
    assert tooling_notes


def test_chatgpt_routing_idempotent_state(tmp_path):
    vault_root = tmp_path / "vault"
    daemon_root = tmp_path / "daemon_vault"
    tooling_root = tmp_path / "tooling_vault"

    config = _make_config(vault_root, daemon_root, tooling_root)
    paths = VaultPaths.from_config(config)
    ledger_writer = LedgerWriter(paths.ledger_file)

    payload = [
        {
            "id": "conv_tooling",
            "title": "Debug error",
            "create_time": "2026-01-22T04:00:00Z",
            "update_time": "2026-01-22T04:05:00Z",
            "messages": [
                {"role": "user", "content": "Traceback (most recent call last):"},
                {"role": "assistant", "content": "Let's fix it."},
            ],
        },
    ]

    zip_path = tmp_path / "export.zip"
    _create_zip(zip_path, payload)

    ingest_from_zip_with_summary(
        config=config,
        vault_paths=paths,
        ledger_writer=ledger_writer,
        zip_path=zip_path,
    )
    ingest_from_zip_with_summary(
        config=config,
        vault_paths=paths,
        ledger_writer=ledger_writer,
        zip_path=zip_path,
    )

    daemon_notes = list(daemon_root.rglob("Debug error.md"))
    tooling_notes = list(tooling_root.rglob("Debug error.md"))

    assert not daemon_notes
    assert tooling_notes

    state_path = paths.root / config.chatgpt_export.state_file
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["conversations"]["conv_tooling"]["destination_vault"] == "tooling"


@pytest.mark.parametrize("routing_mode,expected_root", [
    ("force-tooling", "tooling_vault"),
    ("force-daemon", "daemon_vault"),
])
def test_chatgpt_routing_forced_modes(tmp_path, routing_mode, expected_root):
    vault_root = tmp_path / "vault"
    daemon_root = tmp_path / "daemon_vault"
    tooling_root = tmp_path / "tooling_vault"

    config = _make_config(vault_root, daemon_root, tooling_root)
    paths = VaultPaths.from_config(config)
    ledger_writer = LedgerWriter(paths.ledger_file)

    payload = [
        {
            "id": "conv_any",
            "title": "Random chat",
            "create_time": "2026-01-22T03:45:25Z",
            "update_time": "2026-01-22T03:46:00Z",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        }
    ]

    zip_path = tmp_path / "export.zip"
    _create_zip(zip_path, payload)

    ingest_from_zip_with_summary(
        config=config,
        vault_paths=paths,
        ledger_writer=ledger_writer,
        zip_path=zip_path,
        routing_mode=routing_mode,
        reclassify=True,
    )

    expected_root_path = daemon_root if expected_root == "daemon_vault" else tooling_root
    assert list(expected_root_path.rglob("Random chat.md"))
