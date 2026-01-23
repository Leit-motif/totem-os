"""Main ChatGPT export ingestion pipeline."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..config import TotemConfig
from ..ledger import LedgerWriter
from ..paths import VaultPaths
from .conversation_parser import parse_conversations_json
from .daily_note import write_daily_note_chatgpt_block
from .downloader import download_zip, find_conversation_json, unzip_archive
from .email_parser import extract_download_url_from_email, extract_download_urls, save_debug_email_artifact
from .gmail_client import GmailClient
from .local_ingest import is_estuary_download_url
from .models import ParsedConversations
from .obsidian_writer import write_conversation_note
from .state import IngestState

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """Exception raised for ingestion pipeline errors."""
    pass


def _get_message_header(message: dict, header_name: str) -> str:
    """Get a header value from a Gmail message payload (case-insensitive)."""
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    header_name_lower = header_name.lower()
    for header in headers:
        if header.get("name", "").lower() == header_name_lower:
            return header.get("value", "")
    return ""


def ingest_latest_export(
    config: TotemConfig,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
    debug: bool = False,
    dry_run: bool = False,
    lookback_days: Optional[int] = None,
    gmail_query_override: Optional[str] = None,
    allow_http_download: bool = False,
) -> bool:
    """Run the complete ChatGPT export ingestion pipeline.

    Args:
        config: Totem configuration
        vault_paths: Vault paths
        ledger_writer: Ledger writer for events
        debug: Enable debug logging
        dry_run: Preview mode without file writes
        lookback_days: Override default Gmail query lookback
        gmail_query_override: Override Gmail search query completely
        allow_http_download: Allow HTTP download for ChatGPT estuary URLs

    Returns:
        True if successful, False otherwise

    Raises:
        IngestError: If ingestion fails critically
    """
    start_time = datetime.now(timezone.utc)

    # Override query if specified
    gmail_query = config.chatgpt_export.gmail_query
    if gmail_query_override:
        gmail_query = gmail_query_override
    elif lookback_days is not None:
        gmail_query = (
            f'newer_than:{lookback_days}d from:noreply@tm.openai.com '
            'subject:"Your data export is ready"'
        )

    logger.info("Starting ChatGPT export ingestion")
    logger.info(f"Gmail query: {gmail_query}")
    logger.info(f"Dry run: {dry_run}")

    try:
        # Initialize components
        gmail_client = GmailClient()
        state_file = vault_paths.root / config.chatgpt_export.state_file
        staging_dir = vault_paths.root / config.chatgpt_export.staging_dir

        # Load state
        state = IngestState.load(state_file)
        state.record_attempt()

        if not dry_run:
            state.save(state_file)

        # Authenticate Gmail
        logger.info("Authenticating with Gmail...")
        gmail_client.authenticate()

        # Search for export emails
        logger.info("Searching for export emails...")
        messages = gmail_client.search_messages(
            query=gmail_query,
            max_results=config.chatgpt_export.max_results
        )

        if not messages:
            logger.info("No export emails found")
            ledger_writer.append_event(
                event_type="CHATGPT_EXPORT_EMAILS_FOUND",
                payload={
                    "query": gmail_query,
                    "max_results": config.chatgpt_export.max_results,
                    "emails_found": 0,
                }
            )
            return True

        logger.info(f"Found {len(messages)} export emails")

        # Log emails found
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_EMAILS_FOUND",
            payload={
                "query": gmail_query,
                "max_results": config.chatgpt_export.max_results,
                "emails_found": len(messages),
            }
        )

        # Find unprocessed messages
        unprocessed_messages = []
        for msg in messages:
            msg_id = msg['id']
            if not state.is_message_processed(msg_id):
                unprocessed_messages.append(msg)

        if not unprocessed_messages:
            logger.info("All found emails have already been processed")
            return True

        logger.info(f"Found {len(unprocessed_messages)} unprocessed emails")

        # Try to process emails in order (newest first)
        debug_artifacts = []
        last_error = None
        has_attachment = False
        download_url = None
        attachment_info = None
        matched_candidates = 0
        selected_message_id = None
        selected_message_date = None

        for i, message in enumerate(unprocessed_messages):
            message_id = message['id']
            message_date = datetime.fromtimestamp(int(message['internalDate']) / 1000, timezone.utc)

            subject = _get_message_header(message, "subject")
            sender = _get_message_header(message, "from")
            subject_lower = subject.lower().strip()
            sender_lower = sender.lower()

            subject_match = (
                "data export is ready" in subject_lower
                or subject_lower == "your data export is ready"
            )
            sender_match = "noreply@tm.openai.com" in sender_lower

            if not (subject_match and sender_match):
                logger.info(
                    f"Skipping message {message_id}: subject/from did not match export-ready criteria"
                )
                continue

            matched_candidates += 1
            logger.info(f"Trying email {i+1}/{len(unprocessed_messages)}: {message_id} from {message_date}")

            # Log email selection
            ledger_writer.append_event(
                event_type="CHATGPT_EXPORT_EMAIL_SELECTED",
                payload={
                    "message_id": message_id,
                    "message_date": message_date.isoformat(),
                    "attempt_number": i + 1,
                    "total_candidates": len(unprocessed_messages),
                }
            )

            # Check for ZIP attachments first (preferred method)
            attachments = gmail_client.get_message_attachments(message)
            zip_attachments = [
                att for att in attachments
                if att['filename'].lower().endswith('.zip') and att['mime_type'] in ['application/zip', 'application/x-zip-compressed']
            ]

            if zip_attachments:
                # Found ZIP attachment - use this
                attachment = zip_attachments[0]  # Use first ZIP attachment
                logger.info(f"Found ZIP attachment in email {message_id}: {attachment['filename']} ({attachment['size']} bytes)")
                attachment_info = attachment
                has_attachment = True
                download_url = None  # We'll download attachment instead
                selected_message_id = message_id
                selected_message_date = message_date
                break
            else:
                # No ZIP attachment - try URL extraction
                email_body = gmail_client.get_message_body(message)
                download_url = extract_download_url_from_email(email_body, debug=debug)

                if download_url:
                    if is_estuary_download_url(download_url) and not allow_http_download:
                        error_msg = (
                            "This export URL requires browser authentication (403 via requests). "
                            "Click the email button to download in Chrome, then run: "
                            "totem chatgpt ingest-from-downloads"
                        )
                        logger.error(error_msg)
                        raise IngestError(error_msg)
                    logger.info(f"Found download URL in email {message_id}")
                    has_attachment = False
                    attachment_info = None
                    # Found a valid URL - proceed with this email
                    selected_message_id = message_id
                    selected_message_date = message_date
                    break
                else:
                    # No URL found - log and save debug artifact
                    logger.warning(f"No download URL or ZIP attachment found in email {message_id}")

                    ledger_writer.append_event(
                        event_type="CHATGPT_EXPORT_EMAIL_SKIPPED",
                        payload={
                            "message_id": message_id,
                            "message_date": message_date.isoformat(),
                            "attempt_number": i + 1,
                            "attachments_found": len(attachments),
                            "zip_attachments_found": len(zip_attachments),
                            "reason": "no_zip_url_or_attachment",
                        }
                    )

                    # Save debug artifact
                    extracted_urls = extract_download_urls(email_body)
                    debug_dir = vault_paths.root / config.chatgpt_export.staging_dir / "debug_emails"
                    debug_file = save_debug_email_artifact(
                        message, email_body, extracted_urls, str(debug_dir), message_id
                    )
                    debug_artifacts.append(debug_file)

                    last_error = f"No download URL or ZIP attachment found in email {message_id}"
                    continue

        if matched_candidates == 0:
            logger.info("No messages matched export-ready subject/from criteria")
            return True

        # Check if we found a valid URL or attachment
        if not download_url and not has_attachment:
            error_msg = f"No export ZIP found (neither attachment nor valid download URL) in any of the {len(unprocessed_messages)} candidate emails. Debug artifacts saved to {config.chatgpt_export.staging_dir}/debug_emails/"
            logger.error(error_msg)
            state.record_attempt(error_msg)
            if not dry_run:
                state.save(state_file)
            raise IngestError(error_msg)

        # Create staging directory for this run
        run_date_str = datetime.now().strftime("%Y-%m-%d")
        message_id = selected_message_id
        message_date = selected_message_date
        run_staging_dir = staging_dir / run_date_str / message_id
        run_staging_dir.mkdir(parents=True, exist_ok=True)

        # Download or get attachment
        zip_path = run_staging_dir / f"chatgpt_export__{message_id}.zip"
        logger.info(f"Getting ZIP data for {zip_path}")

        download_method = "attachment" if has_attachment else "url"
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_DOWNLOAD_STARTED",
            payload={
                "message_id": message_id,
                "download_url": download_url,
                "attachment_filename": attachment_info['filename'] if has_attachment else None,
                "attachment_id": attachment_info['attachment_id'] if has_attachment else None,
                "download_method": download_method,
                "zip_path": str(zip_path),
            }
        )

        if not dry_run:
            if has_attachment:
                # Download attachment
                attachment_data = gmail_client.download_attachment(message_id, attachment_info['attachment_id'])
                with open(zip_path, 'wb') as f:
                    f.write(attachment_data)
                logger.info(f"Downloaded attachment: {attachment_info['filename']} ({len(attachment_data)} bytes)")
            else:
                # Download from URL
                download_zip(download_url, zip_path, debug=debug)

            ledger_writer.append_event(
                event_type="CHATGPT_EXPORT_DOWNLOADED",
                payload={
                    "message_id": message_id,
                    "zip_path": str(zip_path),
                    "zip_size": zip_path.stat().st_size,
                    "download_method": download_method,
                }
            )
        else:
            if has_attachment:
                logger.info(f"[DRY RUN] Would download attachment: {attachment_info['filename']} ({attachment_info['size']} bytes)")
            else:
                logger.info("[DRY RUN] Would download ZIP file from URL")

        # Unzip archive
        extract_dir = run_staging_dir / f"{message_id}_unpacked"
        logger.info(f"Extracting to {extract_dir}")

        if dry_run:
            # In dry-run mode, don't actually extract - just log what would happen
            logger.info("[DRY RUN] Would extract ZIP file")
            logger.info(f"[DRY RUN] Extract directory would be: {extract_dir}")
            logger.info("[DRY RUN] Would search for conversations JSON file (likely conversations.json)")
            logger.info("[DRY RUN] Would parse conversations from JSON")
            logger.info("[DRY RUN] Would write conversation notes to Obsidian")
            logger.info("[DRY RUN] Would update daily notes")

            # Mark message as processed and record success
            state.mark_message_processed(message_id)
            state.record_success()
            state.save(state_file)

            ledger_writer.append_event(
                event_type="CHATGPT_EXPORT_INGEST_COMPLETED",
                payload={
                    "message_id": message_id,
                    "download_url": download_url,
                    "attachment_filename": attachment_info['filename'] if has_attachment else None,
                    "dry_run": True,
                    "processing_time_seconds": (datetime.now(timezone.utc) - start_time).total_seconds(),
                },
            )

            logger.info("DRY-RUN completed successfully - no files were modified")
            return True

        # Production mode - actually extract and process
        unzip_archive(zip_path, extract_dir)
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_UNZIPPED",
            payload={
                "message_id": message_id,
                "extract_dir": str(extract_dir),
                "zip_path": str(zip_path),
            }
        )

        # Find conversations JSON
        json_path = find_conversation_json(extract_dir)
        if not json_path:
            error_msg = "No conversations JSON file found in export"
            logger.error(error_msg)
            state.record_attempt(error_msg)
            if not dry_run:
                state.save(state_file)
            raise IngestError(error_msg)

        logger.info(f"Found conversations JSON: {json_path}")

        # Parse conversations
        logger.info("Parsing conversations...")

        parsed_result = parse_conversations_json(json_path)

        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_PARSED",
            payload={
                "message_id": message_id,
                "json_path": str(json_path),
                "total_conversations": parsed_result.total_count,
                "parsed_conversations": parsed_result.parsed_count,
                "parse_errors": parsed_result.errors,
            }
        )

        if parsed_result.parsed_count == 0:
            error_msg = f"Failed to parse any conversations from JSON (errors: {parsed_result.errors})"
            logger.error(error_msg)
            state.record_attempt(error_msg)
            if not dry_run:
                state.save(state_file)
            raise IngestError(error_msg)

        logger.info(f"Parsed {parsed_result.parsed_count}/{parsed_result.total_count} conversations")

        # Write conversation notes
        obsidian_chatgpt_dir = vault_paths.root / config.chatgpt_export.obsidian_chatgpt_dir
        written_notes = []

        for conv in parsed_result.conversations:
            logger.info(f"Writing conversation: {conv.title}")

            if not dry_run:
                note_path = write_conversation_note(
                    conv,
                    obsidian_chatgpt_dir,
                    message_id,
                    config.chatgpt_export.timezone
                )
                written_notes.append(note_path)
            else:
                logger.info(f"[DRY RUN] Would write conversation note for {conv.conversation_id}")

        # Log conversations written
        if not dry_run:
            ledger_writer.append_event(
                event_type="CHATGPT_CONVERSATIONS_WRITTEN",
                payload={
                    "message_id": message_id,
                    "conversations_written": len(written_notes),
                    "obsidian_dir": str(obsidian_chatgpt_dir),
                }
            )

        # Update daily notes
        obsidian_daily_dir = vault_paths.root / config.chatgpt_export.obsidian_daily_dir

        if not dry_run:
            daily_result = write_daily_note_chatgpt_block(
                parsed_result.conversations,
                run_date_str,  # Use processing date
                obsidian_daily_dir,
                ledger_writer
            )
            logger.info(f"Updated daily note: {daily_result.daily_note_path}")
        else:
            logger.info("[DRY RUN] Would update daily notes")

        # Mark message as processed
        state.mark_message_processed(message_id)
        state.record_success()

        if not dry_run:
            state.save(state_file)

        # Log success
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_INGEST_COMPLETED",
            payload={
                "message_id": message_id,
                "message_date": message_date.isoformat(),
                "download_url": download_url,
                "conversations_parsed": parsed_result.parsed_count,
                "conversations_total": parsed_result.total_count,
                "notes_written": len(written_notes),
                "processing_time_seconds": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "dry_run": dry_run,
            },
        )

        logger.info("ChatGPT export ingestion completed successfully")
        return True

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ingestion failed: {error_msg}")

        # Record failure in state
        try:
            state.record_attempt(error_msg)
            if not dry_run:
                state.save(state_file)
        except Exception as state_error:
            logger.error(f"Failed to save error state: {state_error}")

        # Log failure event
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_INGEST_FAILED",
            payload={
                "error": error_msg,
                "processing_time_seconds": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "dry_run": dry_run,
            },
        )

        raise IngestError(error_msg) from e


def doctor_check(
    config: TotemConfig,
    vault_paths: VaultPaths,
) -> dict:
    """Run diagnostic checks for ChatGPT ingestion setup.

    Args:
        config: Totem configuration
        vault_paths: Vault paths

    Returns:
        Dict with check results
    """
    results = {
        "config_valid": True,
        "directories_writable": True,
        "gmail_auth_works": None,  # None = not tested, True/False = result
        "obsidian_dirs_exist": True,
        "errors": [],
        "warnings": [],
    }

    # Check configuration
    try:
        assert config.chatgpt_export.gmail_query
        assert config.chatgpt_export.state_file
        assert config.chatgpt_export.staging_dir
        assert config.chatgpt_export.obsidian_chatgpt_dir
        assert config.chatgpt_export.obsidian_daily_dir
        assert config.chatgpt_export.timezone
        assert config.launchd.label
        assert config.launchd.interval_seconds > 0
    except (AttributeError, AssertionError) as e:
        results["config_valid"] = False
        results["errors"].append(f"Configuration invalid: {e}")

    # Check directories exist and are writable
    dirs_to_check = [
        vault_paths.root / config.chatgpt_export.staging_dir,
        vault_paths.root / config.chatgpt_export.obsidian_chatgpt_dir,
        vault_paths.root / config.chatgpt_export.obsidian_daily_dir,
    ]

    for dir_path in dirs_to_check:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            # Test writability
            test_file = dir_path / ".totem_write_test"
            test_file.write_text("test")
            test_file.unlink()
        except OSError as e:
            results["directories_writable"] = False
            results["errors"].append(f"Directory not writable: {dir_path} ({e})")

    # Check Obsidian directories exist
    obsidian_dirs = [
        vault_paths.root / config.chatgpt_export.obsidian_chatgpt_dir,
        vault_paths.root / config.chatgpt_export.obsidian_daily_dir,
    ]

    for dir_path in obsidian_dirs:
        if not dir_path.exists():
            results["obsidian_dirs_exist"] = False
            results["warnings"].append(f"Obsidian directory does not exist: {dir_path}")

    # Test Gmail authentication (optional)
    try:
        gmail_client = GmailClient()
        gmail_client.authenticate()
        results["gmail_auth_works"] = gmail_client.test_connection()
        if not results["gmail_auth_works"]:
            results["errors"].append("Gmail authentication failed")
    except Exception as e:
        results["gmail_auth_works"] = False
        results["errors"].append(f"Gmail authentication error: {e}")

    return results
