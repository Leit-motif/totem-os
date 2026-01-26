"""Local ZIP ingestion for ChatGPT exports."""

import logging
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..ledger import LedgerWriter
from ..paths import VaultPaths
from ..config import TotemConfig
from .conversation_parser import parse_conversations_json
from .daily_note import write_daily_note_chatgpt_block
from .metadata import ensure_conversation_metadata
from .obsidian_writer import write_conversation_note

logger = logging.getLogger(__name__)


class LocalIngestError(Exception):
    """Exception raised for local ZIP ingestion errors."""

    pass


@dataclass
class LocalZipIngestSummary:
    """Summary of a local ZIP ingestion run."""

    zip_path: Path
    conversations_total: int
    conversations_parsed: int
    notes_written: int
    last_successful_item_timestamp: Optional[str]


def _write_progress_checkpoint(
    vault_paths: VaultPaths,
    zip_path: Path,
    *,
    total: int,
    processed: int,
    notes_written: int,
    last_conversation_id: Optional[str],
    last_conversation_ts: Optional[str],
    status: str,
) -> None:
    progress_dir = vault_paths.system / "ingest_progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress_path = progress_dir / f"chatgpt_local_zip_{zip_path.stem}.json"
    payload = {
        "zip_path": str(zip_path),
        "status": status,
        "total": total,
        "processed": processed,
        "notes_written": notes_written,
        "last_conversation_id": last_conversation_id,
        "last_conversation_timestamp": last_conversation_ts,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp_path = progress_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(progress_path)


def _score_json_member(name: str, has_chat_html: bool) -> int:
    name_lower = name.lower()
    score = 0

    if "conversations" in name_lower:
        score += 100
    if "message" in name_lower:
        score += 50
    if "chat" in name_lower:
        score += 40
    if name_lower.endswith(".json"):
        score += 1
    if has_chat_html and "message" in name_lower:
        score += 10

    return score


def select_conversations_json_member(zip_path: Path) -> Optional[zipfile.ZipInfo]:
    """Select the most likely conversations JSON file from a ZIP without extracting."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            infos = [info for info in zip_ref.infolist() if not info.is_dir()]
            json_infos = [info for info in infos if info.filename.lower().endswith(".json")]

            if not json_infos:
                return None

            if len(json_infos) == 1:
                return json_infos[0]

            has_chat_html = any(info.filename.lower().endswith("chat.html") for info in infos)
            scored = [
                (info, _score_json_member(info.filename, has_chat_html), info.file_size)
                for info in json_infos
            ]
            scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
            return scored[0][0]
    except zipfile.BadZipFile as e:
        logger.warning(f"Invalid ZIP file {zip_path}: {e}")
        return None


def ingest_from_zip_with_summary(
    config: TotemConfig,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
    zip_path: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> LocalZipIngestSummary:
    """Ingest a local ChatGPT export ZIP file and return a summary."""
    if not zip_path.exists():
        raise LocalIngestError(f"ZIP file not found: {zip_path}")
    if zip_path.suffix.lower() != ".zip":
        raise LocalIngestError(f"Not a ZIP file: {zip_path}")

    logger.info(f"Selected local ZIP: {zip_path}")

    json_member = select_conversations_json_member(zip_path)
    if json_member is None:
        raise LocalIngestError(f"No JSON files found in ZIP: {zip_path}")

    ledger_writer.append_event(
        event_type="CHATGPT_EXPORT_LOCAL_ZIP_SELECTED",
        payload={
            "zip_path": str(zip_path),
            "json_member": json_member.filename,
            "json_size": json_member.file_size,
        },
    )

    run_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    staging_dir = (
        vault_paths.root
        / config.chatgpt_export.staging_dir
        / "manual"
        / run_date_str
        / zip_path.stem
    )
    staging_dir.mkdir(parents=True, exist_ok=True)

    ledger_writer.append_event(
        event_type="CHATGPT_EXPORT_LOCAL_ZIP_INGEST_STARTED",
        payload={
            "zip_path": str(zip_path),
            "staging_dir": str(staging_dir),
            "json_member": json_member.filename,
        },
    )

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            extracted_path = Path(zip_ref.extract(json_member, path=staging_dir))

        logger.info(f"Extracted JSON to {extracted_path}")

        parsed_result = parse_conversations_json(extracted_path)
        if parsed_result.parsed_count == 0:
            raise LocalIngestError(
                f"Failed to parse conversations from JSON (errors: {parsed_result.errors})"
            )

        obsidian_chatgpt_dir = vault_paths.root / config.chatgpt_export.obsidian_chatgpt_dir
        obsidian_chatgpt_dir.mkdir(parents=True, exist_ok=True)
        obsidian_vault = Path(os.getenv("TOTEM_VAULT_PATH", "/Users/amrit/My Obsidian Vault"))
        written_notes = []
        conversation_note_paths = {}
        processed = 0
        last_item_ts = None
        last_conv_id = None
        last_conv_ts = None
        total_conversations = parsed_result.parsed_count
        _write_progress_checkpoint(
            vault_paths,
            zip_path,
            total=total_conversations,
            processed=0,
            notes_written=0,
            last_conversation_id=None,
            last_conversation_ts=None,
            status="running",
        )
        for conv in parsed_result.conversations:
            note_path = write_conversation_note(
                conv,
                obsidian_chatgpt_dir,
                ingest_source="local_zip",
                timezone=config.chatgpt_export.timezone,
                run_date_str=run_date_str,
            )
            written_notes.append(note_path)
            conversation_note_paths[conv.conversation_id] = note_path
            ensure_conversation_metadata(
                note_path=note_path,
                summary_config=config.chatgpt_export.summary,
                ledger_writer=ledger_writer,
            )
            ts = conv.updated_at or conv.created_at
            if ts:
                ts_str = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if not last_item_ts or ts_str > last_item_ts:
                    last_item_ts = ts_str
                last_conv_ts = ts_str
            last_conv_id = conv.conversation_id
            processed += 1
            if processed % 50 == 0 or processed == total_conversations:
                _write_progress_checkpoint(
                    vault_paths,
                    zip_path,
                    total=total_conversations,
                    processed=processed,
                    notes_written=len(written_notes),
                    last_conversation_id=last_conv_id,
                    last_conversation_ts=last_conv_ts,
                    status="running",
                )
                if progress_callback:
                    progress_callback(processed, total_conversations, conv.conversation_id)

        enable_daily_notes = True
        daily_result = None
        if enable_daily_notes:
            daily_result = write_daily_note_chatgpt_block(
                parsed_result.conversations,
                run_date_str,
                obsidian_vault,
                ledger_writer,
                conversation_note_paths,
                config.chatgpt_export.summary.include_open_question_in_daily,
            )

        _write_progress_checkpoint(
            vault_paths,
            zip_path,
            total=total_conversations,
            processed=processed,
            notes_written=len(written_notes),
            last_conversation_id=last_conv_id,
            last_conversation_ts=last_conv_ts,
            status="completed",
        )

        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_LOCAL_ZIP_INGESTED",
            payload={
                "zip_path": str(zip_path),
                "json_path": str(extracted_path),
                "conversations_parsed": parsed_result.parsed_count,
                "conversations_total": parsed_result.total_count,
                "notes_written": len(written_notes),
                "daily_note_path": str(daily_result.daily_note_path) if daily_result else None,
            },
        )

        logger.info("Local ZIP ingestion completed successfully")
        return LocalZipIngestSummary(
            zip_path=zip_path,
            conversations_total=parsed_result.total_count,
            conversations_parsed=parsed_result.parsed_count,
            notes_written=len(written_notes),
            last_successful_item_timestamp=last_item_ts,
        )

    except Exception as e:
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_LOCAL_ZIP_INGEST_FAILED",
            payload={
                "zip_path": str(zip_path),
                "error": str(e),
            },
        )
        raise


def ingest_from_zip(
    config: TotemConfig,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
    zip_path: Path,
) -> bool:
    """Ingest a local ChatGPT export ZIP file."""
    ingest_from_zip_with_summary(config, vault_paths, ledger_writer, zip_path)
    return True


def ingest_from_downloads_with_summary(
    config: TotemConfig,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
    downloads_dir: Path,
    limit: int = 50,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Optional[LocalZipIngestSummary]:
    """Find the newest valid export ZIP in downloads and ingest it."""
    if not downloads_dir.exists():
        raise LocalIngestError(f"Downloads directory not found: {downloads_dir}")

    zip_files = [
        path for path in downloads_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".zip"
    ]
    zip_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    candidates = zip_files[:limit]
    selected_zip = None

    for zip_path in candidates:
        if select_conversations_json_member(zip_path):
            selected_zip = zip_path
            break

    if not selected_zip:
        ledger_writer.append_event(
            event_type="CHATGPT_EXPORT_LOCAL_ZIP_NOT_FOUND",
            payload={
                "downloads_dir": str(downloads_dir),
                "limit": limit,
            },
        )
        return None

    return ingest_from_zip_with_summary(
        config,
        vault_paths,
        ledger_writer,
        selected_zip,
        progress_callback=progress_callback,
    )


def ingest_from_downloads(
    config: TotemConfig,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
    downloads_dir: Path,
    limit: int = 50,
) -> bool:
    """Find the newest valid export ZIP in downloads and ingest it."""
    result = ingest_from_downloads_with_summary(
        config=config,
        vault_paths=vault_paths,
        ledger_writer=ledger_writer,
        downloads_dir=downloads_dir,
        limit=limit,
    )
    return result is not None
