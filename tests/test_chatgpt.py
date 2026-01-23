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
    email_parser,
    models,
    obsidian_writer,
    state,
)
from totem.chatgpt.local_ingest import (
    ingest_from_downloads,
    ingest_from_zip,
    is_estuary_download_url,
)
from totem.chatgpt.downloader import find_conversation_json
from totem.chatgpt.gmail_client import GmailClient
from totem.config import resolve_vault_root
from totem.ledger import LedgerWriter
from totem.paths import VaultPaths
from totem.models.ledger import LedgerEvent
from totem.config import ChatGptExportConfig, LaunchdConfig, TotemConfig


class TestEmailParser:
    """Test email URL extraction."""

    def test_extract_download_urls_html(self):
        """Test URL extraction from HTML email."""
        html_email = '''
        <html>
        <body>
        <p>Your export is ready!</p>
        <a href="https://example.com/download/chatgpt_export.zip">Download</a>
        <a href="https://unsubscribe.example.com">Unsubscribe</a>
        <a href="https://storage.googleapis.com/chatgpt/abc123.zip">Direct link</a>
        </body>
        </html>
        '''

        urls = email_parser.extract_download_urls(html_email)
        assert len(urls) == 3
        assert "https://example.com/download/chatgpt_export.zip" in urls
        assert "https://storage.googleapis.com/chatgpt/abc123.zip" in urls

    def test_extract_download_urls_plain_text(self):
        """Test URL extraction from plain text email."""
        text_email = '''
        Your export is ready!
        Download: https://example.com/download/chatgpt_export.zip
        Unsubscribe: https://unsubscribe.example.com
        '''

        urls = email_parser.extract_download_urls(text_email)
        assert len(urls) == 2
        assert "https://example.com/download/chatgpt_export.zip" in urls

    def test_extract_download_urls_with_html_entities(self):
        """Test URL extraction handles HTML entities."""
        html_email = '''
        <html>
        <body>
        <a href="https://example.com/download?token=abc&amp;id=123">Download</a>
        </body>
        </html>
        '''

        urls = email_parser.extract_download_urls(html_email)
        assert len(urls) == 1
        assert "https://example.com/download?token=abc&id=123" in urls

    def test_score_download_url_openai_domain(self):
        """Test scoring favors OpenAI domains."""
        score, reason = email_parser.score_download_url("https://chatgpt.com/export/abc.zip")
        assert score >= 10
        assert "openai_domain" in reason

    def test_score_download_url_export_keyword(self):
        """Test scoring favors export keywords."""
        score, reason = email_parser.score_download_url("https://example.com/export/data.zip")
        assert score >= 5
        assert "export_keyword" in reason

    def test_score_download_url_unsubscribe_negative(self):
        """Test scoring penalizes unsubscribe links."""
        score, reason = email_parser.score_download_url("https://example.com/unsubscribe")
        assert score < 0
        assert "unsubscribe_link" in reason

    def test_score_download_url_image_negative(self):
        """Test scoring penalizes image assets."""
        score, reason = email_parser.score_download_url("https://example.com/logo.png")
        assert score < 0
        assert "image_asset" in reason

    def test_filter_download_urls_scoring(self):
        """Test URL filtering uses scoring system."""
        urls = [
            "https://chatgpt.com/export/abc.zip",      # High score - export keyword
            "https://chatgpt.com/backend-api/estuary/content?id=test.zip",  # High score - API endpoint
            "https://example.com/unsubscribe",         # Negative score
            "https://example.com/logo.png",            # Negative score
        ]

        candidates = email_parser.filter_download_urls(urls)
        assert len(candidates) == 2  # Should include the two valid URLs
        assert "https://chatgpt.com/export/abc.zip" in candidates
        assert "https://chatgpt.com/backend-api/estuary/content?id=test.zip" in candidates
        assert "https://example.com/unsubscribe" not in candidates
        assert "https://example.com/logo.png" not in candidates

    def test_select_best_download_url_scoring(self):
        """Test best URL selection uses scoring."""
        urls = [
            "https://example.com/download.zip",      # Lower score
            "https://chatgpt.com/export/abc.zip",    # Higher score
        ]

        best = email_parser.select_best_download_url(urls)
        assert best == "https://chatgpt.com/export/abc.zip"

    def test_extract_download_url_from_email_openai_cdn_rejected(self):
        """Test that cdn.openai.com URLs are rejected (only chatgpt.com accepted)."""
        email = '''
        <html>
        <body>
        <p>Your ChatGPT export is ready!</p>
        <a href="https://cdn.openai.com/chatgpt/exports/abc123.zip">Download Export</a>
        <a href="https://unsubscribe.openai.com">Unsubscribe</a>
        </body>
        </html>
        '''

        url = email_parser.extract_download_url_from_email(email)
        assert url is None  # Should reject cdn.openai.com

    def test_save_debug_email_artifact(self, tmp_path):
        """Test debug artifact creation."""
        message = {
            'id': 'test_msg_123',
            'internalDate': '1640995200000',  # 2022-01-01
            'snippet': 'Your export is ready',
            'payload': {
                'headers': [
                    {'name': 'Subject', 'value': 'ChatGPT Export Ready'},
                    {'name': 'From', 'value': 'noreply@openai.com'},
                ]
            }
        }

        email_body = "Your export is ready! Download: https://example.com/export.zip"
        extracted_urls = ["https://example.com/export.zip"]
        debug_dir = str(tmp_path / "debug")

        debug_file = email_parser.save_debug_email_artifact(
            message, email_body, extracted_urls, debug_dir, 'test_msg_123'
        )

        assert debug_file.endswith("test_msg_123.json")

        # Verify debug file contents
        with open(debug_file, 'r') as f:
            data = json.load(f)

        assert data['message_id'] == 'test_msg_123'
        assert data['subject'] == 'ChatGPT Export Ready'
        assert data['from'] == 'noreply@openai.com'
        assert data['extracted_urls'] == extracted_urls
        assert len(data['url_scores']) == 1

    def test_score_download_url_excludes_chat_links(self):
        """Test that ChatGPT conversation links are heavily penalized."""
        score, reason = email_parser.score_download_url("https://chatgpt.com/c/abc123")
        assert score == -100
        assert "chat_conversation_link" in reason

    def test_score_download_url_settings_links(self):
        """Test that settings links are penalized."""
        score, reason = email_parser.score_download_url("https://chatgpt.com/settings")
        assert score < 0
        assert "settings_link" in reason

    def test_filter_download_urls_requires_keywords(self):
        """Test that URLs must contain download keywords to be accepted."""
        # URL with OpenAI domain but no download keywords - should be rejected
        urls = ["https://chatgpt.com/some-page"]
        candidates = email_parser.filter_download_urls(urls)
        assert len(candidates) == 0

        # ChatGPT conversation link - should be rejected even with OpenAI domain
        urls = ["https://chatgpt.com/c/abc123"]
        candidates = email_parser.filter_download_urls(urls)
        assert len(candidates) == 0

        # URL with download keywords - should be accepted
        urls = ["https://example.com/export/data.zip"]
        candidates = email_parser.filter_download_urls(urls)
        assert len(candidates) == 1

    def test_chatgpt_export_url_accepted(self):
        """Test that actual ChatGPT export URLs are accepted."""
        # Real ChatGPT export URL from user
        chatgpt_url = "https://chatgpt.com/backend-api/estuary/content?id=d9f905ef04b551186a15ffe06e807f2408b7a5bcfa499e22ed04375ac1763810-2026-01-22-03-45-25-1105ec32ae8245598ed647dc271bd9a9.zip&ts=491404&p=de&cid=2&sig=9d2e27ecb49aca4ccd67229f3a7e5f4b6f85419ab89d7d9cee0771b1e3bb42dd&v=0"

        score, reason = email_parser.score_download_url(chatgpt_url)
        assert score >= 20  # Should be highly scored
        assert "openai_domain" in reason
        assert "chatgpt_api_endpoint" in reason

        candidates = email_parser.filter_download_urls([chatgpt_url])
        assert len(candidates) == 1
        assert candidates[0] == chatgpt_url

    def test_extract_download_url_from_email_strict_estuary_required(self):
        """Test that only ChatGPT estuary URLs are accepted - not random .zip URLs."""
        email_body = '''
        Your export is ready!
        Download: https://example.com/download/chatgpt_export.zip
        Unsubscribe: https://unsubscribe.example.com
        '''

        url = email_parser.extract_download_url_from_email(email_body)
        assert url is None  # Should reject non-ChatGPT URLs

    def test_extract_download_url_from_email_fastpath_estuary(self):
        """Test fast-path URL extraction for estuary content URLs."""
        email_body = '''
        <html>
        <body>
        <p>Your ChatGPT data export is ready!</p>
        <a href="https://chatgpt.com/backend-api/estuary/content?id=abc123.zip&sig=xyz">Download Export</a>
        <a href="https://unsubscribe.openai.com">Unsubscribe</a>
        </body>
        </html>
        '''

        url = email_parser.extract_download_url_from_email(email_body)
        assert url == "https://chatgpt.com/backend-api/estuary/content?id=abc123.zip&sig=xyz"

    def test_extract_download_url_from_email_strict_domain_required(self):
        """Test that only chatgpt.com domains are accepted."""
        email_body = '''
        Your export is ready!
        Direct link: https://files.openai.com/content?id=export_123.zip&token=abc
        '''

        url = email_parser.extract_download_url_from_email(email_body)
        assert url is None  # Should reject non-chatgpt.com domains

    def test_extract_download_url_from_email_no_zip_url(self):
        """Test that emails without ZIP URLs return None."""
        email_body = '''
        Your task has been scheduled for processing.
        We'll send you an email when it's ready.
        Unsubscribe: https://unsubscribe.openai.com
        '''

        url = email_parser.extract_download_url_from_email(email_body)
        assert url is None

    def test_extract_download_url_from_email_task_update_skipped(self):
        """Test that task update emails are skipped (return None)."""
        # Sample task update email content
        task_update_email = '''
        <html>
        <body>
        <p>Your task has been scheduled.</p>
        <p>Task update: Processing your data export request.</p>
        <a href="https://chatgpt.com/settings">Settings</a>
        <a href="https://unsubscribe.openai.com">Unsubscribe</a>
        </body>
        </html>
        '''

        url = email_parser.extract_download_url_from_email(task_update_email)
        assert url is None  # Should be skipped, no ZIP URL

    def test_extract_download_url_from_email_conversation_links_rejected(self):
        """Test that ChatGPT conversation links are rejected."""
        email_body = '''
        <html>
        <body>
        <p>Check out your conversation:</p>
        <a href="https://chatgpt.com/c/abc123">View Conversation</a>
        <a href="https://chatgpt.com/settings">Settings</a>
        <a href="https://help.openai.com/">Help</a>
        </body>
        </html>
        '''

        url = email_parser.extract_download_url_from_email(email_body)
        assert url is None  # Should reject conversation links

    def test_extract_download_url_from_email_export_ready_selected(self):
        """Test that export-ready emails with estuary URLs are selected."""
        # Sample export-ready email content
        export_ready_email = '''
        <html>
        <body>
        <h2>Your data export is ready</h2>
        <p>Download your ChatGPT data export:</p>
        <a href="https://chatgpt.com/backend-api/estuary/content?id=d9f905ef04b551186a15ffe06e807f2408b7a5bcfa499e22ed04375ac1763810-2026-01-22-03-45-25-1105ec32ae8245598ed647dc271bd9a9.zip&ts=491404&p=de&cid=2&sig=9d2e27ecb49aca4ccd67229f3a7e5f4b6f85419ab89d7d9cee0771b1e3bb42dd&v=0">Download Export</a>
        <p>This link will expire in 24 hours.</p>
        </body>
        </html>
        '''

        url = email_parser.extract_download_url_from_email(export_ready_email)
        assert url is not None
        assert "backend-api/estuary/content" in url
        assert ".zip" in url


class TestGmailAttachments:
    """Test Gmail attachment detection and download."""

    def test_get_message_attachments_zip(self):
        """Test detecting ZIP attachments."""
        from unittest.mock import Mock

        # Mock Gmail client
        client = GmailClient.__new__(GmailClient)  # Create without __init__

        # Mock message with ZIP attachment
        message = {
            'payload': {
                'parts': [{
                    'filename': 'chatgpt_export.zip',
                    'mimeType': 'application/zip',
                    'body': {
                        'attachmentId': 'att_123',
                        'size': 1024000
                    }
                }]
            }
        }

        attachments = client.get_message_attachments(message)
        assert len(attachments) == 1
        assert attachments[0]['filename'] == 'chatgpt_export.zip'
        assert attachments[0]['mime_type'] == 'application/zip'
        assert attachments[0]['attachment_id'] == 'att_123'
        assert attachments[0]['size'] == 1024000

    def test_get_message_attachments_no_zip(self):
        """Test that non-ZIP attachments are returned but ZIP logic filters them."""
        from unittest.mock import Mock

        client = GmailClient.__new__(GmailClient)

        message = {
            'payload': {
                'parts': [{
                    'filename': 'image.png',
                    'mimeType': 'image/png',
                    'body': {
                        'attachmentId': 'att_456',
                        'size': 50000
                    }
                }]
            }
        }

        attachments = client.get_message_attachments(message)
        assert len(attachments) == 1

        # But ZIP-specific filtering should exclude it
        zip_attachments = [
            att for att in attachments
            if att['filename'].lower().endswith('.zip') and att['mime_type'] in ['application/zip', 'application/x-zip-compressed']
        ]
        assert len(zip_attachments) == 0


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
                    timestamp=datetime(2022, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                ),
                models.ChatGptMessage(
                    role="assistant",
                    content="Hi there!",
                    timestamp=datetime(2022, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
                ),
            ]
        )

        markdown = obsidian_writer.format_conversation_markdown(conv, "gmail_123")

        assert "---" in markdown
        assert "source: chatgpt_export" in markdown
        assert "conversation_id: test_123" in markdown
        assert "ingested_from: gmail:gmail_123" in markdown
        assert "# Test Conversation" in markdown
        assert "## User (12:00)" in markdown
        assert "## Assistant (12:01)" in markdown
        assert "Hello world" in markdown
        assert "Hi there!" in markdown


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
                daily_dir,
                Mock()  # ledger_writer
            )

            daily_file = daily_dir / "2022-01-01.md"
            assert daily_file.exists()

            content = daily_file.read_text()
            assert "<!-- TOTEM:CHATGPT:START -->" in content
            assert "<!-- TOTEM:CHATGPT:END -->" in content
            assert "Morning Chat" in content

    def test_write_daily_note_chatgpt_block_existing(self):
        """Test updating existing daily note block."""
        with tempfile.TemporaryDirectory() as temp_dir:
            daily_dir = Path(temp_dir) / "daily"
            daily_dir.mkdir()

            # Create existing daily note with old block
            daily_file = daily_dir / "2022-01-01.md"
            daily_file.write_text("""# 2022-01-01

Some existing content.

<!-- TOTEM:CHATGPT:START -->
## ChatGPT
- [[../chatgpt/2022-01-01/chatgpt__old_conv|Old Conversation]] (09:00)
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
                daily_dir,
                Mock()  # ledger_writer
            )

            content = daily_file.read_text()
            assert "Some existing content" in content  # Preserved
            assert "New Conversation" in content
            assert "Old Conversation" not in content  # Replaced
            assert content.count("<!-- TOTEM:CHATGPT:START -->") == 1  # No duplicates


class TestState:
    """Test state management."""

    def test_state_load_save(self):
        """Test loading and saving state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"

            # Test empty state
            state_obj = state.IngestState.load(state_file)
            assert state_obj.processed_message_ids == []
            assert state_obj.last_success_at is None

            # Modify and save
            state_obj.mark_message_processed("msg_123")
            state_obj.record_success()
            state_obj.save(state_file)

            # Load and verify
            loaded = state.IngestState.load(state_file)
            assert "msg_123" in loaded.processed_message_ids
            assert loaded.last_success_at is not None

    def test_state_operations(self):
        """Test state operations."""
        state_obj = state.IngestState()

        # Test message processing
        assert not state_obj.is_message_processed("msg_1")
        state_obj.mark_message_processed("msg_1")
        assert state_obj.is_message_processed("msg_1")

        # Test unprocessed filtering
        all_msgs = ["msg_1", "msg_2", "msg_3"]
        unprocessed = state_obj.get_unprocessed_messages(all_msgs)
        assert unprocessed == ["msg_2", "msg_3"]


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
        resolved = resolve_vault_root("use_existing", None)
        assert resolved == vault_dir

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
        with pytest.raises(FileNotFoundError, match="does not exist"):
            resolve_vault_root("use_existing", None)

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


class TestConversationJsonFinder:
    """Test conversation JSON file discovery."""

    def test_find_conversation_json_by_filename(self, tmp_path):
        """Test finding JSON by filename match."""
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        # Create a conversations.json file
        conv_file = extract_dir / "conversations.json"
        conv_file.write_text('{"test": "data"}')

        # Create other JSON files
        (extract_dir / "other.json").write_text('{"other": "data"}')

        result = find_conversation_json(extract_dir)
        assert result == conv_file

    def test_find_conversation_json_by_content(self, tmp_path):
        """Test finding JSON by content keywords."""
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        # Create a JSON file with conversation keywords
        conv_file = extract_dir / "data.json"
        conv_file.write_text('{"conversations": [], "messages": [], "title": "test"}')

        # Create a JSON file without keywords
        other_file = extract_dir / "other.json"
        other_file.write_text('{"unrelated": "data"}')

        result = find_conversation_json(extract_dir)
        assert result == conv_file

    def test_find_conversation_json_no_files(self, tmp_path):
        """Test behavior when no JSON files exist."""
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        result = find_conversation_json(extract_dir)
        assert result is None

    def test_find_conversation_json_invalid_json(self, tmp_path):
        """Test behavior with invalid JSON files."""
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        # Create invalid JSON file
        invalid_file = extract_dir / "invalid.json"
        invalid_file.write_text('{invalid json')

        # Create valid JSON file
        valid_file = extract_dir / "valid.json"
        valid_file.write_text('{"valid": "json"}')

        result = find_conversation_json(extract_dir)
        assert result == valid_file


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
            obsidian_daily_dir=str(daily_dir),
            timezone="America/Chicago",
        )
        return TotemConfig(vault_path=vault_root, chatgpt_export=chatgpt_config)

    def test_ingest_from_zip_success(self, tmp_path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir(parents=True)

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
        note_paths = list(chatgpt_root.rglob("chatgpt__conv_1.md"))
        assert note_paths

    def test_ingest_from_downloads_picks_newest_valid_zip(self, tmp_path):
        vault_root = tmp_path / "vault"
        vault_root.mkdir(parents=True)

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
        note_paths = list(chatgpt_root.rglob("chatgpt__conv_1.md"))
        assert note_paths


class TestEstuaryGuard:
    def test_estuary_url_detection(self):
        url = "https://chatgpt.com/backend-api/estuary/content?id=abc.zip"
        assert is_estuary_download_url(url) is True

    def test_estuary_url_blocks_http_download(self, tmp_path):
        from totem.chatgpt.ingest import ingest_latest_export, IngestError

        vault_root = tmp_path / "vault"
        vault_root.mkdir(parents=True)
        config = TotemConfig(
            vault_path=vault_root,
            chatgpt_export=ChatGptExportConfig(
                obsidian_chatgpt_dir=str(vault_root / "obsidian" / "chatgpt"),
                obsidian_daily_dir=str(vault_root / "obsidian" / "daily"),
            ),
        )
        paths = VaultPaths.from_config(config)
        ledger_writer = LedgerWriter(paths.ledger_file)

        message = {
            "id": "msg_1",
            "internalDate": "1769073720000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "OpenAI <noreply@tm.openai.com>"},
                    {"name": "Subject", "value": "Your data export is ready"},
                ]
            },
        }
        estuary_url = "https://chatgpt.com/backend-api/estuary/content?id=abc.zip"

        class FakeGmailClient:
            def authenticate(self):
                return None

            def search_messages(self, query, max_results=10):
                return [message]

            def get_message_attachments(self, msg):
                return []

            def get_message_body(self, msg):
                return f"Download: {estuary_url}"

        with patch("totem.chatgpt.ingest.GmailClient", return_value=FakeGmailClient()):
            with patch("totem.chatgpt.ingest.download_zip") as download_mock:
                with pytest.raises(IngestError) as exc_info:
                    ingest_latest_export(
                        config=config,
                        vault_paths=paths,
                        ledger_writer=ledger_writer,
                        debug=False,
                        dry_run=False,
                        allow_http_download=False,
                    )
                assert "requires browser authentication" in str(exc_info.value)
                download_mock.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
