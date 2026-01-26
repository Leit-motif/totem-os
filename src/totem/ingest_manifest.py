"""Ingestion manifest: append-only run records for ingestion reliability."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .paths import VaultPaths


MANIFEST_SCHEMA_VERSION = 1


class IngestCounts(BaseModel):
    """Counts for an ingestion run."""

    discovered: int = 0
    ingested: int = 0
    skipped: int = 0
    errored: int = 0


class IngestErrorItem(BaseModel):
    """Single error item in an ingestion run."""

    item_id: str
    error: str


class IngestRunRecord(BaseModel):
    """Append-only manifest record for one ingestion run."""

    schema_version: int = Field(default=MANIFEST_SCHEMA_VERSION)
    source: Literal["omi", "chatgpt"]
    run_id: str
    run_type: str
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    counts: IngestCounts
    last_successful_item_timestamp: Optional[str] = None
    errors: list[IngestErrorItem] = Field(default_factory=list)
    app_version: str
    git_sha: Optional[str] = None
    created_at: str
    details: dict = Field(default_factory=dict)
    cursor: Optional[dict] = None


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_manifest_record(vault_paths: VaultPaths, record: IngestRunRecord) -> None:
    """Append a manifest record to the JSONL manifest file."""
    manifest_path = vault_paths.ingest_manifest_file
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")


def load_manifest_records(vault_paths: VaultPaths) -> list[IngestRunRecord]:
    """Load manifest records (best-effort; skips malformed lines)."""
    manifest_path = vault_paths.ingest_manifest_file
    raw_records = _safe_read_jsonl(manifest_path)
    records: list[IngestRunRecord] = []
    for raw in raw_records:
        try:
            records.append(IngestRunRecord(**raw))
        except Exception:
            continue
    return records


def latest_record_by_source(records: list[IngestRunRecord]) -> dict[str, IngestRunRecord]:
    """Return the latest record per source based on append order."""
    latest: dict[str, IngestRunRecord] = {}
    for record in records:
        latest[record.source] = record
    return latest


def totals_by_source(records: list[IngestRunRecord]) -> dict[str, IngestCounts]:
    """Compute cumulative counts per source."""
    totals: dict[str, IngestCounts] = {}
    for record in records:
        if record.source not in totals:
            totals[record.source] = IngestCounts()
        totals[record.source].discovered += record.counts.discovered
        totals[record.source].ingested += record.counts.ingested
        totals[record.source].skipped += record.counts.skipped
        totals[record.source].errored += record.counts.errored
    return totals


def build_manifest_record(
    *,
    source: Literal["omi", "chatgpt"],
    run_id: str,
    run_type: str,
    counts: IngestCounts,
    app_version: str,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
    last_successful_item_timestamp: Optional[str] = None,
    errors: Optional[list[IngestErrorItem]] = None,
    git_sha: Optional[str] = None,
    details: Optional[dict] = None,
    cursor: Optional[dict] = None,
    created_at: Optional[str] = None,
) -> IngestRunRecord:
    """Helper to build a manifest record with defaults."""
    return IngestRunRecord(
        source=source,
        run_id=run_id,
        run_type=run_type,
        window_start=window_start,
        window_end=window_end,
        counts=counts,
        last_successful_item_timestamp=last_successful_item_timestamp,
        errors=errors or [],
        app_version=app_version,
        git_sha=git_sha,
        created_at=created_at or _now_utc_iso(),
        details=details or {},
        cursor=cursor,
    )


def try_get_git_sha(repo_root: Path) -> Optional[str]:
    """Best-effort git SHA lookup (no git invocation)."""
    try:
        head_path = repo_root / ".git" / "HEAD"
        if not head_path.exists():
            return None
        head = head_path.read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head.split("ref: ", 1)[1].strip()
            ref_path = repo_root / ".git" / ref
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
            return None
        return head
    except Exception:
        return None
