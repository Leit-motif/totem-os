from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import ChunkingConfig, PlannedChunk


@dataclass(frozen=True)
class HeadingRow:
    id: int
    level: int
    text: str
    start_byte: int


def _sha256_hex(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _sha256_hex_str(s: str) -> str:
    return _sha256_hex(s.encode("utf-8"))


def _safe_utf8_end(data: bytes, start: int, end: int) -> int:
    end = min(end, len(data))
    if end <= start:
        return start
    # Back off at most 4 bytes (UTF-8 max codepoint length).
    for back in range(0, 5):
        cand = end - back
        if cand <= start:
            continue
        try:
            data[start:cand].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        return cand
    raise UnicodeDecodeError("utf-8", data, start, end, "unable to split on UTF-8 boundary")


def _split_by_paragraph_boundaries(section: bytes, absolute_start: int) -> list[tuple[int, int]]:
    # Split points are after occurrences of "\n\n" (keeps exact bytes; no trimming).
    spans: list[tuple[int, int]] = []
    rel_start = 0
    idx = 0
    while True:
        found = section.find(b"\n\n", idx)
        if found == -1:
            break
        cut = found + 2
        spans.append((absolute_start + rel_start, absolute_start + cut))
        rel_start = cut
        idx = cut
    spans.append((absolute_start + rel_start, absolute_start + len(section)))
    return [(s, e) for (s, e) in spans if e > s]


def _window_split(
    data: bytes,
    start: int,
    end: int,
    max_bytes: int,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cur = start
    while cur < end:
        hard_end = min(cur + max_bytes, end)
        safe_end = _safe_utf8_end(data, cur, hard_end)
        if safe_end <= cur:
            raise ValueError(f"Unable to make progress splitting UTF-8 window at byte {cur}")
        spans.append((cur, safe_end))
        cur = safe_end
    return spans


def _heading_path_for(headings: list[HeadingRow], idx: int) -> str:
    stack: list[tuple[int, str]] = []
    for j in range(0, idx + 1):
        level = headings[j].level
        text = headings[j].text.strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
    return " > ".join([f"H{lvl} {txt}" for (lvl, txt) in stack])


def compute_headings_signature(headings: list[HeadingRow]) -> str:
    parts = [f"{h.level}:{h.text}:{h.start_byte}" for h in headings]
    return _sha256_hex_str("|".join(parts))


def compute_chunk_plan_hash(
    *,
    file_content_hash: str,
    headings_signature: str,
    chunking: ChunkingConfig,
    embeddings_model: str,
    embeddings_dim: int,
) -> str:
    return _sha256_hex_str(
        f"{file_content_hash}:{headings_signature}:{chunking.signature()}:model={embeddings_model}:dim={embeddings_dim}"
    )


def plan_chunks_for_file(
    *,
    vault_root: Path,
    rel_path: str,
    file_id: int,
    file_size_bytes: int,
    headings: list[HeadingRow],
    chunking: ChunkingConfig,
    embeddings_model: str,
) -> list[PlannedChunk]:
    abs_path = (vault_root / rel_path)
    data = abs_path.read_bytes()
    if len(data) != file_size_bytes:
        # Use actual bytes length as authoritative for slicing safety.
        file_size_bytes = len(data)

    spans: list[tuple[Optional[int], str, int, int]] = []
    if chunking.include_preamble and headings:
        spans.append((None, "", 0, max(0, min(headings[0].start_byte, file_size_bytes))))

    for i, h in enumerate(headings):
        start = max(0, min(h.start_byte, file_size_bytes))
        if i + 1 < len(headings):
            end = max(0, min(headings[i + 1].start_byte, file_size_bytes))
        else:
            end = file_size_bytes
        if end > start:
            spans.append((h.id, _heading_path_for(headings, i), start, end))

    planned: list[PlannedChunk] = []
    for heading_id, heading_path, section_start, section_end in spans:
        section_bytes = data[section_start:section_end]

        piece_spans: list[tuple[int, int]] = [(section_start, section_end)]
        if chunking.split_strategy == "paragraph_then_window":
            piece_spans = _split_by_paragraph_boundaries(section_bytes, section_start)
        else:
            raise ValueError(f"Unknown chunks_split_strategy: {chunking.split_strategy}")

        final_spans: list[tuple[int, int]] = []
        for s, e in piece_spans:
            if (e - s) <= chunking.max_bytes:
                final_spans.append((s, e))
            else:
                final_spans.extend(_window_split(data, s, e, chunking.max_bytes))

        for s, e in final_spans:
            chunk_bytes = data[s:e]
            # Required: strict UTF-8 decode (fail-fast).
            chunk_text = chunk_bytes.decode("utf-8", errors="strict")
            chunk_bytes_norm = chunk_text.encode("utf-8")
            if chunk_bytes_norm != chunk_bytes:
                raise ValueError(f"UTF-8 round-trip mismatch for {rel_path} bytes[{s}:{e}]")

            text_hash = _sha256_hex(chunk_bytes)
            chunk_id = _sha256_hex_str(f"{rel_path}:{heading_path}:{s}:{e}")
            chunk_hash = _sha256_hex_str(f"{embeddings_model}:{chunk_id}:{text_hash}")
            planned.append(
                PlannedChunk(
                    file_id=file_id,
                    heading_id=heading_id,
                    heading_path=heading_path,
                    ord=0,  # filled after sorting
                    start_byte=s,
                    end_byte=e,
                    text_hash=text_hash,
                    chunk_id=chunk_id,
                    chunk_hash=chunk_hash,
                    byte_len=(e - s),
                )
            )

    planned.sort(key=lambda c: (c.start_byte, c.end_byte, c.chunk_id))
    with_ord: list[PlannedChunk] = []
    for i, c in enumerate(planned):
        with_ord.append(
            PlannedChunk(
                file_id=c.file_id,
                heading_id=c.heading_id,
                heading_path=c.heading_path,
                ord=i,
                start_byte=c.start_byte,
                end_byte=c.end_byte,
                text_hash=c.text_hash,
                chunk_id=c.chunk_id,
                chunk_hash=c.chunk_hash,
                byte_len=c.byte_len,
            )
        )
    return with_ord


def load_headings_for_file(conn: sqlite3.Connection, file_id: int) -> list[HeadingRow]:
    rows = conn.execute(
        # Deterministic ordering: do not depend on DB row IDs.
        "SELECT id, level, text, start_byte FROM headings WHERE file_id = ? ORDER BY start_byte ASC, level ASC, text ASC, ord ASC",
        (file_id,),
    ).fetchall()
    return [HeadingRow(id=int(r["id"]), level=int(r["level"]), text=str(r["text"]), start_byte=int(r["start_byte"])) for r in rows]
