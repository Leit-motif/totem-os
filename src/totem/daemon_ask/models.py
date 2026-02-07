from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    rel_path: str
    start_byte: int
    end_byte: int

    def to_compact_str(self) -> str:
        return f"{self.rel_path}:{self.start_byte}-{self.end_byte}"


@dataclass(frozen=True)
class PackedExcerpt:
    citation: Citation
    title: str
    heading_path: str
    effective_date: str
    excerpt: str
    score: float
    expanded_context: bool


@dataclass(frozen=True)
class DaemonAskResult:
    answer: str
    citations: list[Citation]
    why_these_sources: list[str]
    packed: list[PackedExcerpt]
    trace_path: str

