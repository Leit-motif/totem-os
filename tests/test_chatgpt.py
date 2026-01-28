"""Tests for ChatGPT export ingestion functionality."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from totem.chatgpt import (
    conversation_parser,
    daily_note,
    metadata,
    models,
    obsidian_writer,
)
from totem.chatgpt.local_ingest import (
    ingest_from_downloads,
    ingest_from_zip,
)
from totem.config import resolve_vault_root
from totem.ledger import LedgerWriter
from totem.paths import VaultPaths
from totem.models.ledger import LedgerEvent
from totem.config import (
    ChatGptExportConfig,
    ChatGptSummaryConfig,
    ObsidianConfig,
    ObsidianVaultsConfig,
    TotemConfig,
)


class TestConversationParser:
    """Test conversation parsing."""

    def test_parse_conversations_json(self):
        """Test parsing conversations from JSON."""
        # Mock conversation data
        mock_data = [
            {
                "id": "conv_123",
                "title": "Test Conversation",
                "create_time": 1640995200.0,  # 2022-01-01
                "update_time": 1640995260.0,
                "messages": [
                    {
                        "role": "user",
                        "content": "Hello",
                        "timestamp": "2022-01-01T00:00:00Z"
                    },
                    {
                        "role": "assistant",
                        "content": "Hi there!",
                        "timestamp": "2022-01-01T00:01:00Z"
                    }
                ]
            }
        ]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(mock_data, f)
            json_path = Path(f.name)

        try:
            result = conversation_parser.parse_conversations_json(json_path)

            assert result.total_count == 1
            assert result.parsed_count == 1
            assert len(result.conversations) == 1

            conv = result.conversations[0]
            assert conv.conversation_id == "conv_123"
            assert conv.title == "Test Conversation"
            # Check that timestamp was parsed (exact value depends on local timezone)
            assert conv.created_at is not None
            assert len(conv.messages) == 2
            assert conv.messages[0].role == "user"
            assert conv.messages[1].role == "assistant"

        finally:
            json_path.unlink()

    def test_parse_conversations_json_mapping_parts(self):
        """Test parsing mapping-based export with content parts."""
        mock_data = [
            {
                "id": "conv_map",
                "title": "Mapping Conversation",
                "create_time": 1640995200.0,
                "mapping": {
                    "node_1": {
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": ["Hello", "there"]},
                            "create_time": 1640995200.0,
                        }
                    },
                    "node_2": {
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["Hi!"]},
                            "create_time": 1640995260.0,
                        }
                    },
                    "node_3": {
                        "message": {
                            "author": {"role": "system"},
                            "content": {"parts": ["system note"]},
                            "create_time": 1640995270.0,
                        }
                    },
                },
            }
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(mock_data, f)
            json_path = Path(f.name)

        try:
            result = conversation_parser.parse_conversations_json(json_path)

            assert result.total_count == 1
            assert result.parsed_count == 1
            conv = result.conversations[0]
            assert conv.conversation_id == "conv_map"
            assert len(conv.messages) == 2
            assert conv.messages[0].content == "Hello\nthere"
            assert conv.messages[1].content == "Hi!"
        finally:
            json_path.unlink()

    def test_parse_single_conversation_minimal(self):
        """Test parsing minimal conversation data."""
        conv_data = {
            "title": "Minimal",
            "messages": [
                {"role": "user", "content": "test"},
                {"role": "assistant", "content": "response"}
            ]
        }

        conv = conversation_parser._parse_single_conversation(conv_data)
        assert conv is not None
        assert conv.title == "Minimal"
        assert len(conv.messages) == 2


class TestObsidianWriter:
    """Test Obsidian note writing."""

    def test_compute_content_hash(self):
        """Test content hash computation."""
        conv = models.ChatGptConversation(
            conversation_id="test_123",
            title="Test Conversation",
            created_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
            messages=[
                models.ChatGptMessage(role="user", content="Hello"),
                models.ChatGptMessage(role="assistant", content="Hi!"),
            ]
        )

        hash1 = obsidian_writer.compute_content_hash(conv)
        hash2 = obsidian_writer.compute_content_hash(conv)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

        # Different content should give different hash
        conv.title = "Changed"
        hash3 = obsidian_writer.compute_content_hash(conv)
        assert hash3 != hash1

    def test_format_conversation_markdown(self):
        """Test markdown formatting."""
        conv = models.ChatGptConversation(
            conversation_id="test_123",
            title="Test Conversation",
            created_at=datetime(2022, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2022, 1, 1, 12, 5, 0, tzinfo=timezone.utc),
            messages=[
                models.ChatGptMessage(
                    role="user",
                    content="Hello world",
                    created_at=datetime(2022, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                ),
                models.ChatGptMessage(
                    role="assistant",
                    content="Hi there!",
                    created_at=datetime(2022, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
                ),
            ]
        )

        markdown = obsidian_writer.format_conversation_markdown(conv, "local_zip")

        assert "---" in markdown
        assert "source: chatgpt_export" in markdown
        assert "conversation_id: test_123" in markdown
        assert "ingested_from: local_zip" in markdown
        assert "# Test Conversation" in markdown
        assert "## Transcript" in markdown
        assert "### User" in markdown
        assert "### Assistant" in markdown
        assert "Hello world" in markdown
        assert "Hi there!" in markdown

    def test_write_conversation_note_path_structure(self):
        """Test note paths use YYYY/MM/DD structure."""
        conv = models.ChatGptConversation(
            conversation_id="conv_123",
            title="Path Test",
            created_at=datetime(2022, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2022, 1, 2, 12, 5, 0, tzinfo=timezone.utc),
            messages=[],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            obsidian_dir = Path(temp_dir) / "chatgpt"
            obsidian_dir.mkdir()

            note_path = obsidian_writer.write_conversation_note(
                conv,
                obsidian_dir,
                "local_zip",
                run_date_str="2022-01-03",
            )

            expected_path = obsidian_dir / "2022" / "01" / "02" / "Path Test.md"
            assert note_path == expected_path
            assert note_path.exists()


class TestDailyNote:
    """Test daily note integration."""

    def test_write_daily_note_chatgpt_block_new(self):
        """Test creating new daily note block."""
        conversations = [
            models.ChatGptConversation(
                conversation_id="conv_1",
                title="Morning Chat",
                created_at=datetime(2022, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
                updated_at=datetime(2022, 1, 1, 9, 5, 0, tzinfo=timezone.utc),
                messages=[]
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            daily_dir = Path(temp_dir) / "daily"
            daily_dir.mkdir()

            result = daily_note.write_daily_note_chatgpt_block(
                conversations,
                "2022-01-01",
                daily_dir.parent,
                Mock(),  # ledger_writer
                {"conv_1": daily_dir.parent / "40_Chatgpt" / "conversations" / "2022" / "01" / "01" / "Morning Chat.md"},
            )

            daily_file = (
                daily_dir.parent
                / "5.0 Journal"
                / "5.1 Daily"
                / "2022"
                / "01"
                / "2022-01-01.md"
            )
            assert daily_file.exists()

            content = daily_file.read_text()
            assert "<!-- TOTEM:CHATGPT:START -->" in content
            assert "<!-- TOTEM:CHATGPT:END -->" in content
            assert "Morning Chat" in content
            assert "[[Morning Chat]]" in content

    def test_write_daily_note_chatgpt_block_existing(self):
        """Test updating existing daily note block."""
        with tempfile.TemporaryDirectory() as temp_dir:
            vault_root = Path(temp_dir)
            daily_dir = vault_root / "5.0 Journal" / "5.1 Daily" / "2022" / "01"
            daily_dir.mkdir(parents=True, exist_ok=True)

            # Create existing daily note with old block
            daily_file = daily_dir / "2022-01-01.md"
            daily_file.write_text("""# 2022-01-01

Some existing content.

<!-- TOTEM:CHATGPT:START -->
## ChatGPT
- [[40_Chatgpt/conversations/2022/01/01/Old Conversation]]
<!-- TOTEM:CHATGPT:END -->
""")

            conversations = [
                models.ChatGptConversation(
                    conversation_id="new_conv",
                    title="New Conversation",
                    created_at=datetime(2022, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                    updated_at=datetime(2022, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
                    messages=[]
                )
            ]

            result = daily_note.write_daily_note_chatgpt_block(
                conversations,
                "2022-01-01",
                vault_root,
                Mock(),  # ledger_writer
                {"new_conv": vault_root / "40_Chatgpt" / "conversations" / "2022" / "01" / "01" / "New Conversation.md"},
            )

            content = daily_file.read_text()
            assert "Some existing content" in content  # Preserved
            assert "New Conversation" in content
            assert "Old Conversation" not in content  # Replaced
            assert content.count("<!-- TOTEM:CHATGPT:START -->") == 1  # No duplicates
            assert "[[New Conversation]]" in content


class TestLedgerEvent:
    """Test LedgerEvent validation with ChatGPT events."""

    def test_chatgpt_event_types_accepted(self):
        """Test that ChatGPT event types are accepted by LedgerEvent."""
        from datetime import datetime

        # Test each ChatGPT event type
        chatgpt_events = [
            "CHATGPT_EXPORT_INGEST_STARTED",
            "CHATGPT_EXPORT_EMAILS_FOUND",
            "CHATGPT_EXPORT_EMAIL_SELECTED",
            "CHATGPT_EXPORT_EMAIL_SKIPPED",
            "CHATGPT_EXPORT_DOWNLOAD_STARTED",
            "CHATGPT_EXPORT_DOWNLOADED",
            "CHATGPT_EXPORT_UNZIPPED",
            "CHATGPT_EXPORT_PARSED",
            "CHATGPT_CONVERSATIONS_WRITTEN",
            "CHATGPT_DAILY_NOTE_WRITTEN",
            "CHATGPT_METADATA_GENERATED",
            "CHATGPT_METADATA_SKIPPED",
            "CHATGPT_METADATA_FAILED",
            "CHATGPT_METADATA_BACKFILL_PROGRESS",
            "CHATGPT_EXPORT_INGEST_COMPLETED",
            "CHATGPT_EXPORT_INGEST_FAILED",
        ]

        for event_type in chatgpt_events:
            event = LedgerEvent(
                event_id="test-id",
                run_id="test-run",
                ts=datetime.now(),
                event_type=event_type,
                payload={"test": "data"}
            )
            assert event.event_type == event_type

    def test_invalid_event_type_rejected(self):
        """Test that invalid event types are rejected."""
        from datetime import datetime
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LedgerEvent(
                event_id="test-id",
                run_id="test-run",
                ts=datetime.now(),
                event_type="INVALID_EVENT_TYPE",
                payload={"test": "data"}
            )


class TestConfig:
    """Test configuration setup."""

    def test_totem_config_with_chatgpt(self):
        """Test that TotemConfig includes ChatGPT settings."""
        config = TotemConfig()

        assert hasattr(config, 'chatgpt_export')
        assert hasattr(config, 'launchd')
        assert config.chatgpt_export.gmail_query
        assert config.launchd.label
        assert config.launchd.interval_seconds == 21600
        assert hasattr(config.chatgpt_export, 'summary')
        assert config.chatgpt_export.summary.enabled is True
        assert config.chatgpt_export.summary.provider in ["auto", "gemini", "openai"]


class TestVaultResolution:
    """Test vault path resolution functionality."""

    def test_resolve_vault_root_cli_override(self, tmp_path):
        """Test CLI --vault override takes highest precedence."""
        vault_dir = tmp_path / "custom_vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        resolved = resolve_vault_root("use_existing", str(vault_dir))
        assert resolved == vault_dir

    def test_resolve_vault_root_env_override(self, tmp_path, monkeypatch):
        """Test TOTEM_VAULT environment variable override."""
        vault_dir = tmp_path / "env_vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        monkeypatch.setenv("TOTEM_VAULT", str(vault_dir))
        import os
        old_cwd = os.getcwd()
        try:
            # Ensure this test isn't affected by any repo-local .totem/config.toml
            os.chdir(tmp_path)
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == vault_dir
        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_from_repo_root(self, tmp_path, monkeypatch):
        """Test auto-discovery from repo root with ./totem_vault present."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)
        # Create repo structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Create totem_vault inside repo
        vault_dir = repo_root / "totem_vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        # Change to repo root
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(repo_root)
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == vault_dir
        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_from_vault_subdirectory(self, tmp_path, monkeypatch):
        """Test auto-discovery from inside vault directory."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)
        # Create vault structure
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        # Create subdirectory inside vault
        subdir = vault_dir / "00_inbox" / "2022-01-01"
        subdir.mkdir(parents=True)

        # Change to subdirectory
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == vault_dir
        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_no_vault_found(self, tmp_path, monkeypatch):
        """Test that FileNotFoundError is raised when no vault is found."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with pytest.raises(FileNotFoundError, match="Vault not found"):
                resolve_vault_root("use_existing", None)
        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_cli_path_does_not_exist(self, tmp_path):
        """Test that FileNotFoundError is raised for non-existent CLI path."""
        with pytest.raises(FileNotFoundError, match="does not exist"):
            resolve_vault_root("use_existing", str(tmp_path / "nonexistent"))

    def test_resolve_vault_root_env_path_does_not_exist(self, tmp_path, monkeypatch):
        """Test that FileNotFoundError is raised for non-existent env path."""
        monkeypatch.setenv("TOTEM_VAULT", str(tmp_path / "nonexistent"))
        import os
        old_cwd = os.getcwd()
        try:
            # Ensure this test isn't affected by any repo-local .totem/config.toml
            os.chdir(tmp_path)
            with pytest.raises(FileNotFoundError, match="does not exist"):
                resolve_vault_root("use_existing", None)
        finally:
            os.chdir(old_cwd)

    def test_totem_config_from_env_with_cli_override(self, tmp_path):
        """Test TotemConfig.from_env with CLI vault path override."""
        vault_dir = tmp_path / "cli_vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        config = TotemConfig.from_env(cli_vault_path=str(vault_dir), mode="use_existing")
        assert config.vault_path == vault_dir

    def test_totem_config_from_env_auto_discovery(self, tmp_path, monkeypatch):
        """Test TotemConfig.from_env with auto-discovery."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)
        # Create vault structure
        vault_dir = tmp_path / "auto_vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(vault_dir)
            config = TotemConfig.from_env(cli_vault_path=None, mode="use_existing")
            assert config.vault_path == vault_dir
        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_repo_config(self, tmp_path, monkeypatch):
        """Test repo-local .totem/config.toml resolution."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)

        # Create repo structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Create vault
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        # Create .totem/config.toml
        totem_dir = repo_root / ".totem"
        totem_dir.mkdir()
        config_file = totem_dir / "config.toml"
        config_file.write_text(f'vault_root = "{vault_dir}"\n')

        # Change to repo root
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(repo_root)
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == vault_dir
        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_create_ok_mode(self, tmp_path):
        """Test create_ok mode allows non-existent paths."""
        nonexistent_path = tmp_path / "new_vault"
        resolved = resolve_vault_root("create_ok", str(nonexistent_path))
        assert resolved == nonexistent_path

    def test_resolve_vault_root_create_ok_with_existing(self, tmp_path):
        """Test create_ok mode works with existing vault."""
        vault_dir = tmp_path / "existing_vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        resolved = resolve_vault_root("create_ok", str(vault_dir))
        assert resolved == vault_dir

    def test_resolve_vault_root_priority_order(self, tmp_path, monkeypatch):
        """Test resolution priority: CLI > repo-config > env > auto-discovery."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)

        # Create multiple vault options
        cli_vault = tmp_path / "cli_vault"
        cli_vault.mkdir()
        (cli_vault / "90_system").mkdir()
        (cli_vault / "90_system" / "config.yaml").write_text("vault_path: test")

        env_vault = tmp_path / "env_vault"
        env_vault.mkdir()
        (env_vault / "90_system").mkdir()
        (env_vault / "90_system" / "config.yaml").write_text("vault_path: test")

        auto_vault = tmp_path / "auto_vault"
        auto_vault.mkdir()
        (auto_vault / "90_system").mkdir()
        (auto_vault / "90_system" / "config.yaml").write_text("vault_path: test")

        # Create repo with config pointing to env_vault
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        totem_dir = repo_root / ".totem"
        totem_dir.mkdir()
        config_file = totem_dir / "config.toml"
        config_file.write_text(f'vault_root = "{env_vault}"\n')

        # Set env var to env_vault
        monkeypatch.setenv("TOTEM_VAULT", str(env_vault))

        # Change to auto_vault directory
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(auto_vault)
            # CLI should override everything
            resolved = resolve_vault_root("use_existing", str(cli_vault))
            assert resolved == cli_vault

            # Without CLI, repo config should be used
            os.chdir(repo_root)
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == env_vault

            # Without repo config, env should be used
            config_file.unlink()
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == env_vault

            # Without env, auto-discovery should work
            monkeypatch.delenv("TOTEM_VAULT", raising=False)
            os.chdir(auto_vault)
            resolved = resolve_vault_root("use_existing", None)
            assert resolved == auto_vault

        finally:
            os.chdir(old_cwd)

    def test_resolve_vault_root_helpful_error_message(self, tmp_path, monkeypatch):
        """Test that helpful error message is shown when no vault is found."""
        monkeypatch.delenv("TOTEM_VAULT", raising=False)

        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with pytest.raises(FileNotFoundError) as exc_info:
                resolve_vault_root("use_existing", None)

            error_msg = str(exc_info.value)
            assert "Vault not found" in error_msg
            assert "totem link-vault" in error_msg
            assert "totem --vault" in error_msg
            assert "TOTEM_VAULT" in error_msg
            assert "Searched for:" in error_msg
        finally:
            os.chdir(old_cwd)

    def test_totem_config_from_env_create_ok_mode(self, tmp_path):
        """Test TotemConfig.from_env with create_ok mode."""
        new_vault_path = tmp_path / "new_vault"
        config = TotemConfig.from_env(cli_vault_path=str(new_vault_path), mode="create_ok")
        assert config.vault_path == new_vault_path


class TestLinkVaultCommand:
    """Test the link-vault command functionality."""

    def test_link_vault_creates_config(self, tmp_path, monkeypatch):
        """Test that link-vault creates .totem/config.toml correctly."""
        # Create vault
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "90_system").mkdir()
        (vault_dir / "90_system" / "config.yaml").write_text("vault_path: test")

        # Create repo structure
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Change to repo root
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(repo_root)

            # Mock the console to capture output
            from unittest.mock import patch
            from rich.console import Console
            console = Console()

            with patch('totem.cli.console', console):
                from totem.cli import link_vault
                link_vault(str(vault_dir))

            # Check that config file was created
            config_file = repo_root / ".totem" / "config.toml"
            assert config_file.exists()

            # Check content
            content = config_file.read_text()
            assert f'vault_root = "{vault_dir}"' in content
            assert "Totem OS repository configuration" in content

        finally:
            os.chdir(old_cwd)


class TestChatGptMetadata:
    def test_build_salience_input_full_and_partial(self):
        short_text = "Hello world." * 5
        input_text, used_chars, confidence = metadata.build_salience_input(short_text, 500)
        assert confidence == "full"
        assert used_chars == len(short_text)

        long_text = ("I realized this matters?\n" * 200).strip()
        input_text, used_chars, confidence = metadata.build_salience_input(long_text, 500)
        assert confidence == "partial"
        assert used_chars <= 500
        assert len(input_text) == used_chars

    def test_metadata_skip_when_version_matches(self, tmp_path):
        note_path = tmp_path / "note.md"
        content_hash = "abc123"
        meta_hash = metadata.compute_meta_hash(content_hash, 1)
        note_path.write_text(
            """---
source: chatgpt_export
content_hash: abc123
totem_meta_version: 1
totem_meta_hash: {meta_hash}
totem_signpost: "Test signpost."
totem_summary: "Test summary."
---
# Title
""".format(meta_hash=meta_hash),
            encoding="utf-8",
        )

        summary_config = ChatGptSummaryConfig(version=1)
        ledger_writer = Mock()

        with patch("totem.chatgpt.metadata.call_metadata_llm") as call_mock:
            result = metadata.ensure_conversation_metadata(
                note_path=note_path,
                summary_config=summary_config,
                ledger_writer=ledger_writer,
            )
            assert result.status == "skipped"
            assert result.reason == "up_to_date"
            call_mock.assert_not_called()

    def test_daily_note_includes_signpost_and_question(self, tmp_path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir(parents=True)

        note_dir = vault_root / "40_chatgpt" / "conversations" / "2022" / "01" / "01"
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / "Test Conversation.md"
        note_path.write_text(
            """---
source: chatgpt_export
conversation_id: conv_1
totem_signpost: "Maps uncertainty to control signals."
totem_summary_confidence: "partial"
totem_open_questions:
  - "Which emotion am I avoiding?"
---
# Test Conversation
""",
            encoding="utf-8",
        )

        conversations = [
            models.ChatGptConversation(
                conversation_id="conv_1",
                title="Test Conversation",
                created_at=datetime(2022, 1, 1, 9, 0, 0, tzinfo=timezone.utc),
                updated_at=datetime(2022, 1, 1, 9, 5, 0, tzinfo=timezone.utc),
                messages=[],
            )
        ]

        result = daily_note.write_daily_note_chatgpt_block(
            conversations,
            "2022-01-01",
            vault_root,
            Mock(),
            {"conv_1": note_path},
            include_open_question_in_daily=True,
        )

        daily_file = (
            vault_root
            / "5.0 Journal"
            / "5.1 Daily"
            / "2022"
            / "01"
            / "2022-01-01.md"
        )
        content = daily_file.read_text(encoding="utf-8")
        assert "Test Conversation" in content
        assert "Maps uncertainty to control signals. ⏳" in content
        assert "Q: Which emotion am I avoiding?" in content

    def test_frontmatter_update_preserves_unrelated_keys(self):
        note_text = """---
source: chatgpt_export
other_key: "keep"
totem_signpost: "old"
totem_summary: "old summary"
---
# Body
"""
        front_lines, body_text, has_frontmatter = metadata.split_frontmatter(note_text)
        assert has_frontmatter is True
        updates = {
            "totem_signpost": "new",
            "totem_summary": "new summary",
            "totem_themes": ["theme"],
            "totem_open_questions": ["question?"],
            "totem_summary_confidence": "partial",
            "totem_input_chars_used": 10,
            "totem_input_chars_total": 100,
            "totem_input_coverage_ratio": 0.1,
            "totem_input_selection_strategy": "salience",
            "totem_meta_provider": "openai",
            "totem_meta_model": "gpt-4o-mini",
            "totem_meta_version": 1,
            "totem_meta_hash": "hash",
            "totem_meta_created_at": "2026-01-23T00:00:00+00:00",
        }
        updated = metadata.update_frontmatter(
            note_text=note_text,
            frontmatter_lines=front_lines,
            body_text=body_text,
            updates=updates,
        )
        front_lines_updated, body_text_updated, has_frontmatter_updated = metadata.split_frontmatter(updated)
        assert has_frontmatter_updated is True
        assert body_text_updated.strip() == "# Body"
        assert "other_key: \"keep\"" in "\n".join(front_lines_updated)
        assert "totem_signpost: \"new\"" in "\n".join(front_lines_updated)
        assert "totem_signpost: \"old\"" not in "\n".join(front_lines_updated)

    def test_metadata_failure_returns_failed(self, tmp_path, monkeypatch):
        note_path = tmp_path / "note.md"
        note_path.write_text(
            """---
source: chatgpt_export
conversation_id: conv_1
---
# Title
## Transcript
User: Test
""",
            encoding="utf-8",
        )

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        summary_config = ChatGptSummaryConfig(provider="openai", version=1)
        ledger_writer = Mock()

        with patch("totem.chatgpt.metadata.call_metadata_llm", side_effect=ValueError("boom")):
            result = metadata.ensure_conversation_metadata(
                note_path=note_path,
                summary_config=summary_config,
                ledger_writer=ledger_writer,
            )
            assert result.status == "failed"

    def test_link_vault_validates_vault_exists(self, tmp_path):
        """Test that link-vault validates vault exists and has markers."""
        nonexistent_vault = tmp_path / "nonexistent"

        from click.exceptions import Exit
        with pytest.raises(Exit):
            from totem.cli import link_vault
            link_vault(str(nonexistent_vault))

    def test_link_vault_validates_vault_has_markers(self, tmp_path):
        """Test that link-vault validates vault has proper markers."""
        # Create directory without vault markers
        invalid_vault = tmp_path / "invalid_vault"
        invalid_vault.mkdir()

        from click.exceptions import Exit
        with pytest.raises(Exit):
            from totem.cli import link_vault
            link_vault(str(invalid_vault))


class TestLocalZipIngest:
    """Tests for local ChatGPT ZIP ingestion."""

    def _create_zip(self, zip_path: Path, json_name: str, payload: dict) -> None:
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zip_ref:
            zip_ref.writestr(json_name, json.dumps(payload))

    def _make_config(self, vault_root: Path) -> TotemConfig:
        obsidian_root = vault_root / "obsidian"
        chatgpt_dir = obsidian_root / "chatgpt"
        daily_dir = obsidian_root / "daily"

        chatgpt_config = ChatGptExportConfig(
            staging_dir="state/chatgpt_exports",
            obsidian_chatgpt_dir=str(chatgpt_dir),
            tooling_chatgpt_dir="ChatGPT/Tooling",
            obsidian_daily_dir=str(daily_dir),
            timezone="America/Chicago",
        )
        return TotemConfig(
            vault_path=vault_root,
            chatgpt_export=chatgpt_config,
            obsidian=ObsidianConfig(
                vaults=ObsidianVaultsConfig(
                    daemon_path=str(obsidian_root),
                    tooling_path=str(obsidian_root / "tooling"),
                ),
            ),
        )

    def test_ingest_from_zip_success(self, tmp_path, monkeypatch):
        vault_root = tmp_path / "vault"
        vault_root.mkdir(parents=True)
        monkeypatch.setenv("TOTEM_VAULT_PATH", str(vault_root))

        config = self._make_config(vault_root)
        paths = VaultPaths.from_config(config)
        ledger_writer = LedgerWriter(paths.ledger_file)

        payload = [
            {
                "id": "conv_1",
                "title": "Test Conversation",
                "create_time": "2026-01-22T03:45:25Z",
                "update_time": "2026-01-22T03:46:00Z",
                "messages": [
                    {"role": "user", "content": "Hello", "timestamp": "2026-01-22T03:45:25Z"},
                    {"role": "assistant", "content": "Hi", "timestamp": "2026-01-22T03:45:26Z"},
                ],
            }
        ]

        zip_path = tmp_path / "export.zip"
        self._create_zip(zip_path, "conversations.json", payload)

        result = ingest_from_zip(config, paths, ledger_writer, zip_path)
        assert result is True

        chatgpt_root = Path(config.chatgpt_export.obsidian_chatgpt_dir)
        note_paths = list(chatgpt_root.rglob("Test Conversation.md"))
        assert note_paths

    def test_ingest_from_downloads_picks_newest_valid_zip(self, tmp_path, monkeypatch):
        vault_root = tmp_path / "vault"
        vault_root.mkdir(parents=True)
        monkeypatch.setenv("TOTEM_VAULT_PATH", str(vault_root))

        config = self._make_config(vault_root)
        paths = VaultPaths.from_config(config)
        ledger_writer = LedgerWriter(paths.ledger_file)

        downloads = tmp_path / "downloads"
        downloads.mkdir()

        payload = [{"id": "conv_1", "title": "A", "create_time": "2026-01-22T03:45:25Z"}]

        old_zip = downloads / "old.zip"
        new_zip = downloads / "new.zip"

        self._create_zip(old_zip, "conversations.json", payload)
        self._create_zip(new_zip, "conversations.json", payload)

        old_time = 1000
        new_time = 2000
        os.utime(old_zip, (old_time, old_time))
        os.utime(new_zip, (new_time, new_time))

        result = ingest_from_downloads(
            config=config,
            vault_paths=paths,
            ledger_writer=ledger_writer,
            downloads_dir=downloads,
            limit=50,
        )
        assert result is True

        chatgpt_root = Path(config.chatgpt_export.obsidian_chatgpt_dir)
        note_paths = list(chatgpt_root.rglob("A.md"))
        assert note_paths
