from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Citation, PackedExcerpt


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def write_trace(
    *,
    traces_dir: Path,
    payload: dict[str, Any],
    trace_prefix: str,
    trace_dedupe_key: str,
) -> Path:
    traces_dir.mkdir(parents=True, exist_ok=True)
    ts = _iso_now().replace(":", "").replace("-", "")
    suffix = _short_hash(trace_dedupe_key)
    path = traces_dir / f"{trace_prefix}_{ts}_{suffix}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def trace_payload(
    *,
    query: str,
    pipeline_version: str,
    ask_config: dict[str, Any],
    ask_config_effective: dict[str, Any] | None,
    search_config: dict[str, Any],
    graph_enabled: bool,
    candidates: list[dict[str, Any]],
    packed: list[PackedExcerpt],
    answer: str,
    citations: list[Citation],
    why_these_sources: list[str],
    session_before: dict[str, Any] | None,
    session_after: dict[str, Any] | None,
    session_rw_log: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "pipeline_version": pipeline_version,
        "ts_utc": _iso_now(),
        "query": query,
        "ask_config": ask_config,
        "ask_config_effective": ask_config_effective,
        "search_config": search_config,
        "graph_enabled": graph_enabled,
        "session_before": session_before,
        "session_after": session_after,
        "session_rw_log": session_rw_log or [],
        "candidates": candidates,
        "packed": [asdict(p) for p in packed],
        "answer": answer,
        "citations": [asdict(c) for c in citations],
        "why_these_sources": list(why_these_sources),
    }
