from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExcerptConfig:
    max_chars: int
    before_chars: int
    after_chars: int


def make_excerpt(
    *,
    file_bytes: bytes,
    start_byte: int,
    end_byte: int,
    query: str,
    cfg: ExcerptConfig,
) -> str:
    chunk_bytes = file_bytes[start_byte:end_byte]
    try:
        text = chunk_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = chunk_bytes.decode("utf-8", errors="replace")

    if cfg.max_chars <= 0:
        return ""

    q = query.strip().lower()
    idx = text.lower().find(q) if q else -1
    if idx == -1:
        window_start = 0
    else:
        window_start = max(0, idx - max(0, cfg.before_chars))

    window_end = min(len(text), window_start + max(1, cfg.max_chars))
    # Try to provide after-context if match exists.
    if idx != -1:
        desired_end = min(len(text), idx + len(q) + max(0, cfg.after_chars))
        window_end = min(max(window_end, desired_end), window_start + max(1, cfg.max_chars))

    excerpt = text[window_start:window_end]
    if window_start > 0:
        excerpt = "…" + excerpt
    if window_end < len(text):
        excerpt = excerpt + "…"
    return excerpt

