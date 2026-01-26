"""Omi ingestion runner for Totem OS (resumable + manifest-friendly)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .client import OmiClient
from .daily_note import write_daily_note_omi_block
from .trace import write_sync_trace
from .writer import write_transcripts_to_vault
from ..ledger import LedgerWriter
from ..models.omi import OmiConversation, OmiSyncResult
from ..paths import VaultPaths


@dataclass
class OmiIngestSummary:
    window_start: datetime
    window_end: datetime
    conversations_count: int
    segments_total: int
    segments_written: int
    segments_skipped: int
    days_processed: int
    last_successful_item_timestamp: Optional[str]
    errors: list[dict]
    sync_all: bool
    date_label: str


class OmiIngestCrash(RuntimeError):
    """Exception raised for injected crash testing."""

    def __init__(self, message: str, summary: OmiIngestSummary):
        super().__init__(message)
        self.summary = summary


def _count_segments(conversations: list[OmiConversation]) -> int:
    return sum(len(conv.transcript) for conv in conversations)


def sync_omi_transcripts(
    *,
    date: Optional[str],
    sync_all: bool,
    write_daily_note: bool,
    obsidian_vault: Path,
    ledger_writer: LedgerWriter,
    vault_paths: VaultPaths,
    resume_from: Optional[datetime] = None,
    crash_after_segments: Optional[int] = None,
) -> OmiIngestSummary:
    """Sync Omi transcripts and return a summary.

    Args:
        date: YYYY-MM-DD (ignored if sync_all)
        sync_all: Sync entire history
        write_daily_note: Write daily note block
        obsidian_vault: Obsidian vault root
        ledger_writer: Ledger writer
        vault_paths: Totem vault paths
        resume_from: Optional resume timestamp (UTC)
        crash_after_segments: Inject crash after N segments processed (test-only)
    """
    errors: list[dict] = []

    if sync_all:
        start_date = resume_from or datetime(2020, 1, 1, 0, 0, 0)
        end_date = datetime.now()
        date_label = "history"
    else:
        if date:
            date_str = date
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"Invalid date format '{date_str}'. Use YYYY-MM-DD")
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")
        date_label = date_str
        year, month, day = map(int, date_str.split("-"))
        start_date = datetime(year, month, day, 0, 0, 0)
        end_date = datetime(year, month, day, 23, 59, 59)

    # Fetch conversations
    client = OmiClient()
    sync_start = datetime.now(timezone.utc)
    conversations = client.fetch_conversations(start_date, end_date)

    # Log fetch event
    ledger_writer.append_event(
        event_type="OMI_SYNC_FETCHED",
        payload={
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
            "conversations_count": len(conversations),
            "api_endpoint": f"{OmiClient.BASE_URL}/user/conversations",
            "sync_all": sync_all,
        },
    )

    if not conversations:
        return OmiIngestSummary(
            window_start=start_date,
            window_end=end_date,
            conversations_count=0,
            segments_total=0,
            segments_written=0,
            segments_skipped=0,
            days_processed=0,
            last_successful_item_timestamp=None,
            errors=[],
            sync_all=sync_all,
            date_label=date_label,
        )

    # Group conversations by date
    by_date: dict[str, list[OmiConversation]] = defaultdict(list)
    for conv in conversations:
        d_str = conv.started_at.strftime("%Y-%m-%d")
        by_date[d_str].append(conv)

    total_written = 0
    total_skipped = 0
    days_processed = 0
    processed_segments = 0

    # Sort keys for chronological order
    for d_str in sorted(by_date.keys()):
        day_convs = by_date[d_str]

        if crash_after_segments is None:
            # Batch mode (faster)
            result = write_transcripts_to_vault(
                conversations=day_convs,
                date_str=d_str,
                vault_root=obsidian_vault,
                ledger_writer=ledger_writer,
            )
            total_written += result.segments_written
            total_skipped += result.segments_skipped
            days_processed += 1
        else:
            # Per-conversation mode (test crash injection)
            for conv in sorted(day_convs, key=lambda c: c.started_at):
                result = write_transcripts_to_vault(
                    conversations=[conv],
                    date_str=d_str,
                    vault_root=obsidian_vault,
                    ledger_writer=ledger_writer,
                )
                total_written += result.segments_written
                total_skipped += result.segments_skipped
                processed_segments += result.segments_written + result.segments_skipped

                if crash_after_segments and processed_segments >= crash_after_segments:
                    summary = _build_omi_summary(
                        start_date,
                        end_date,
                        conversations,
                        total_written,
                        total_skipped,
                        days_processed,
                        sync_all,
                        date_label,
                        errors,
                    )
                    raise OmiIngestCrash(
                        f"Injected crash after {processed_segments} segments",
                        summary,
                    )

            days_processed += 1

        if write_daily_note:
            try:
                write_daily_note_omi_block(
                    conversations=day_convs,
                    date_str=d_str,
                    vault_root=obsidian_vault,
                    ledger_writer=ledger_writer,
                )
            except Exception as e:
                errors.append({"item_id": d_str, "error": str(e)})

    # Write trace
    sync_end = datetime.now(timezone.utc)
    trace_date_label = "history" if sync_all else date_label
    trace_folder_date = datetime.now().strftime("%Y-%m-%d")

    combined_result = OmiSyncResult(
        date=trace_date_label,
        conversations_count=len(conversations),
        segments_written=total_written,
        segments_skipped=total_skipped,
        file_path=obsidian_vault,
    )

    write_sync_trace(
        sync_result=combined_result,
        run_id=ledger_writer.run_id,
        vault_paths=vault_paths,
        date_str=trace_folder_date,
        start_time=sync_start,
        end_time=sync_end,
        api_endpoint=f"{OmiClient.BASE_URL}/user/conversations",
        api_params={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "include_transcript": "true",
            "limit": 100,
            "sync_all": sync_all,
            "write_daily_note": write_daily_note,
        },
        conversation_ids=[conv.id for conv in conversations],
        daily_note_written=write_daily_note and days_processed > 0,
    )

    return _build_omi_summary(
        start_date,
        end_date,
        conversations,
        total_written,
        total_skipped,
        days_processed,
        sync_all,
        date_label,
        errors,
    )


def _build_omi_summary(
    start_date: datetime,
    end_date: datetime,
    conversations: list[OmiConversation],
    total_written: int,
    total_skipped: int,
    days_processed: int,
    sync_all: bool,
    date_label: str,
    errors: list[dict],
) -> OmiIngestSummary:
    segments_total = _count_segments(conversations)
    last_ts = None
    if conversations:
        last_conv = max(conversations, key=lambda c: c.finished_at)
        last_ts = last_conv.finished_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return OmiIngestSummary(
        window_start=start_date,
        window_end=end_date,
        conversations_count=len(conversations),
        segments_total=segments_total,
        segments_written=total_written,
        segments_skipped=total_skipped,
        days_processed=days_processed,
        last_successful_item_timestamp=last_ts,
        errors=errors,
        sync_all=sync_all,
        date_label=date_label,
    )
