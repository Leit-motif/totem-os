from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DaemonSearchConfig:
    vault_root: Path
    db_path: Path

    # Ranking / fusion
    hybrid_weight_lex: float
    hybrid_weight_vec: float
    prefer_recent_half_life_days: float
    prefer_recent_weight: float

    # Excerpts
    excerpt_max_chars: int
    context_before_chars: int
    context_after_chars: int

    # Defaults / expansion
    top_k_default: int
    expand_links_default: int
    expand_links_cap: int

    # Vector backend/model
    vector_backend: str
    model: str
    dim: int


@dataclass(frozen=True)
class SearchFilters:
    tags: list[str]
    tag_or: bool
    date_from: Optional[str]
    date_to: Optional[str]


@dataclass(frozen=True)
class Candidate:
    file_id: int
    rel_path: str
    title: str
    mtime_ns: int
    effective_date: str  # YYYY-MM-DD
    chunk_id: str
    chunk_hash: str
    heading_path: str
    start_byte: int
    end_byte: int
    lex_raw: Optional[float]
    vec_raw: Optional[float]


@dataclass(frozen=True)
class SearchHit:
    score: float
    rel_path: str
    title: str
    heading_path: str
    start_byte: int
    end_byte: int
    effective_date: str
    mtime_ns: int
    excerpt: str
    expanded_context: bool

