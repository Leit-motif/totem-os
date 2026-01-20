"""Trace writer for Omi sync operations."""

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models.omi import OmiSyncResult
from ..paths import VaultPaths


def write_sync_trace(
    sync_result: OmiSyncResult,
    run_id: str,
    vault_paths: VaultPaths,
    date_str: str,
    start_time: datetime,
    end_time: datetime,
    api_endpoint: str,
    api_params: dict,
    conversation_ids: list[str],
) -> Path:
    """Write trace JSON for Omi sync operation.
    
    Follows naming convention: omi_sync_<run_id>.json
    Written to: 90_system/traces/omi/YYYY-MM-DD/
    
    Args:
        sync_result: Result of sync operation
        run_id: Run identifier from ledger writer
        vault_paths: VaultPaths instance
        date_str: Date string (YYYY-MM-DD)
        start_time: Sync start timestamp
        end_time: Sync end timestamp
        api_endpoint: API endpoint URL
        api_params: API request parameters
        conversation_ids: List of conversation IDs synced
        
    Returns:
        Path to written trace file
    """
    # Create trace directory
    trace_dir = vault_paths.traces_omi_date_folder(date_str)
    trace_dir.mkdir(parents=True, exist_ok=True)
    
    # Build trace data
    duration_ms = int((end_time - start_time).total_seconds() * 1000)
    
    # Calculate total segments
    segments_count = sync_result.segments_written + sync_result.segments_skipped
    
    trace_data = {
        "run_id": run_id,
        "date": date_str,
        "timestamp": end_time.isoformat(),
        "conversation_ids": conversation_ids,
        "segments_count": segments_count,
        "api_request": {
            "endpoint": api_endpoint,
            "params": api_params,
        },
        "api_response": {
            "conversations_count": sync_result.conversations_count,
        },
        "sync_result": {
            "segments_written": sync_result.segments_written,
            "segments_skipped": sync_result.segments_skipped,
            "file_path": str(sync_result.file_path),
        },
        "duration_ms": duration_ms,
    }
    
    # Write trace file with aligned naming: omi_sync_<run_id>.json
    trace_path = trace_dir / f"omi_sync_{run_id}.json"
    trace_path.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")
    
    return trace_path
