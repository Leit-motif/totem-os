"""Tests for ingestion manifest and resumability."""

import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from totem.ingest_manifest import (
    IngestCounts,
    IngestErrorItem,
    append_manifest_record,
    build_manifest_record,
    latest_record_by_source,
    load_manifest_records,
    totals_by_source,
)
from totem.omi.ingest import OmiIngestCrash, sync_omi_transcripts
from totem.models.omi import OmiConversation, OmiTranscriptSegment
from totem.ledger import LedgerWriter
from totem.paths import VaultPaths
from totem.config import (
    TotemConfig,
    ChatGptExportConfig,
    ObsidianConfig,
    ObsidianVaultsConfig,
)
from totem.chatgpt.local_ingest import ingest_from_zip_with_summary


def test_manifest_append_and_summary(vault_paths):
    record_omi = build_manifest_record(
        source="omi",
        run_id="run_1",
        run_type="full_history",
        window_start="2026-01-01T00:00:00Z",
        window_end="2026-01-02T00:00:00Z",
        counts=IngestCounts(discovered=10, ingested=8, skipped=2, errored=0),
        last_successful_item_timestamp="2026-01-02T00:00:00Z",
        errors=[],
        app_version="0.1.0",
    )
    record_chatgpt = build_manifest_record(
        source="chatgpt",
        run_id="run_2",
        run_type="latest",
        window_start="2026-01-03T00:00:00Z",
        window_end="2026-01-03T23:59:59Z",
        counts=IngestCounts(discovered=5, ingested=4, skipped=1, errored=0),
        last_successful_item_timestamp="2026-01-03T12:00:00Z",
        errors=[],
        app_version="0.1.0",
    )

    append_manifest_record(vault_paths, record_omi)
    append_manifest_record(vault_paths, record_chatgpt)

    records = load_manifest_records(vault_paths)
    latest = latest_record_by_source(records)
    totals = totals_by_source(records)

    assert latest["omi"].counts.ingested == 8
    assert latest["chatgpt"].counts.ingested == 4
    assert totals["omi"].ingested == 8
    assert totals["chatgpt"].ingested == 4


def test_omi_crash_resume_idempotent(tmp_path, vault_paths, monkeypatch):
    obsidian_vault = tmp_path / "obsidian"
    obsidian_vault.mkdir(parents=True, exist_ok=True)

    conversations = [
        OmiConversation(
            id="conv_1",
            started_at=datetime(2026, 1, 19, 10, 0, 0),
            finished_at=datetime(2026, 1, 19, 10, 15, 0),
            transcript=[
                OmiTranscriptSegment(segment_id="seg_1", speaker_id="S1", text="Hello"),
                OmiTranscriptSegment(segment_id="seg_2", speaker_id="S2", text="World"),
            ],
        )
    ]

    def _fake_fetch(self, start_date, end_date):
        return conversations

    monkeypatch.setattr("totem.omi.ingest.OmiClient.fetch_conversations", _fake_fetch)

    ledger_writer = LedgerWriter(vault_paths.ledger_file)

    with pytest.raises(OmiIngestCrash) as exc_info:
        sync_omi_transcripts(
            date="2026-01-19",
            sync_all=False,
            write_daily_note=False,
            obsidian_vault=obsidian_vault,
            ledger_writer=ledger_writer,
            vault_paths=vault_paths,
            crash_after_segments=1,
        )

    summary1 = exc_info.value.summary
    record1 = build_manifest_record(
        source="omi",
        run_id=ledger_writer.run_id,
        run_type="date",
        window_start=summary1.window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_end=summary1.window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        counts=IngestCounts(
            discovered=summary1.segments_total,
            ingested=summary1.segments_written,
            skipped=summary1.segments_skipped,
            errored=1,
        ),
        last_successful_item_timestamp=summary1.last_successful_item_timestamp,
        errors=[IngestErrorItem(item_id="injected_crash", error="crash")],
        app_version="0.1.0",
    )
    append_manifest_record(vault_paths, record1)

    summary2 = sync_omi_transcripts(
        date="2026-01-19",
        sync_all=False,
        write_daily_note=False,
        obsidian_vault=obsidian_vault,
        ledger_writer=ledger_writer,
        vault_paths=vault_paths,
    )

    record2 = build_manifest_record(
        source="omi",
        run_id=ledger_writer.run_id,
        run_type="date",
        window_start=summary2.window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_end=summary2.window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        counts=IngestCounts(
            discovered=summary2.segments_total,
            ingested=summary2.segments_written,
            skipped=summary2.segments_skipped,
            errored=0,
        ),
        last_successful_item_timestamp=summary2.last_successful_item_timestamp,
        errors=[],
        app_version="0.1.0",
    )
    append_manifest_record(vault_paths, record2)

    file_path = obsidian_vault / "Omi Transcripts" / "2026" / "01" / "2026-01-19.md"
    content = file_path.read_text(encoding="utf-8")
    assert content.count("<!-- seg_id: seg_1 -->") == 1
    assert content.count("<!-- seg_id: seg_2 -->") == 1


def test_chatgpt_local_zip_idempotent(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True)
    obsidian_root = vault_root / "obsidian"
    obsidian_root.mkdir(parents=True)

    monkeypatch.setenv("TOTEM_VAULT_PATH", str(obsidian_root))

    config = TotemConfig(
        vault_path=vault_root,
        chatgpt_export=ChatGptExportConfig(
            staging_dir="state/chatgpt_exports",
            obsidian_chatgpt_dir=str(obsidian_root / "chatgpt"),
            tooling_chatgpt_dir="ChatGPT",
            obsidian_daily_dir=str(obsidian_root / "daily"),
            timezone="America/Chicago",
        ),
        obsidian=ObsidianConfig(
            vaults=ObsidianVaultsConfig(
                daemon_path=str(obsidian_root),
                tooling_path=str(obsidian_root / "tooling"),
            ),
        ),
    )
    paths = VaultPaths.from_config(config)
    paths.system.mkdir(parents=True, exist_ok=True)
    paths.ledger_file.parent.mkdir(parents=True, exist_ok=True)
    paths.ledger_file.touch()
    ledger_writer = LedgerWriter(paths.ledger_file)

    conversations_payload = [
        {
            "id": "conv_1",
            "title": "Test One",
            "create_time": "2026-01-22T03:45:25Z",
            "update_time": "2026-01-22T03:46:00Z",
            "messages": [
                {"role": "user", "content": "Hello", "timestamp": "2026-01-22T03:45:25Z"},
                {"role": "assistant", "content": "Hi", "timestamp": "2026-01-22T03:45:26Z"},
            ],
        },
        {
            "id": "conv_2",
            "title": "Test Two",
            "create_time": "2026-01-22T04:00:00Z",
            "update_time": "2026-01-22T04:05:00Z",
            "messages": [
                {"role": "user", "content": "Ping", "timestamp": "2026-01-22T04:00:00Z"},
            ],
        },
    ]

    zip_bytes = _create_zip_bytes(conversations_payload)
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(zip_bytes)

    summary1 = ingest_from_zip_with_summary(
        config=config,
        vault_paths=paths,
        ledger_writer=ledger_writer,
        zip_path=zip_path,
    )

    chatgpt_root = Path(config.chatgpt_export.obsidian_chatgpt_dir)
    note_paths = list(chatgpt_root.rglob("*.md"))
    assert note_paths

    ingest_from_zip_with_summary(
        config=config,
        vault_paths=paths,
        ledger_writer=ledger_writer,
        zip_path=zip_path,
    )
    note_paths_after = list(chatgpt_root.rglob("*.md"))
    assert len(note_paths_after) == len(note_paths)


def _create_zip_bytes(payload: list[dict]) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_ref:
        zip_ref.writestr("conversations.json", json.dumps(payload))
    return buffer.getvalue()
