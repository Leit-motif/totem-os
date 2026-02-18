from __future__ import annotations

from dataclasses import dataclass

from .engine import search_daemon
from .models import DaemonSearchConfig, SearchFilters


@dataclass(frozen=True)
class RetrievedSnippet:
    score: float
    rel_path: str
    effective_date: str
    heading_path: str
    start_byte: int
    end_byte: int
    excerpt: str
    expanded_context: bool

    @property
    def citation(self) -> str:
        return f"{self.rel_path}:{self.start_byte}-{self.end_byte}"


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    snippets: list[RetrievedSnippet]


def retrieve_context(
    cfg: DaemonSearchConfig,
    *,
    query: str,
    top_k: int,
    filters: SearchFilters,
    prefer_recent: bool = True,
    expand_links: int = 0,
    include_expanded: bool = False,
) -> RetrievalResult:
    """Retrieve bounded snippets with provenance for downstream answering.

    This is the daemon-search wrapper intended for evidence-first answer pipelines.
    It returns snippets suitable for citation display (path + byte-range).
    """
    hits = search_daemon(
        cfg,
        query=query,
        top_k=top_k,
        prefer_recent=prefer_recent,
        filters=filters,
        expand_links=expand_links,
    )

    snippets: list[RetrievedSnippet] = []
    for h in hits:
        if (not include_expanded) and h.expanded_context:
            continue
        snippets.append(
            RetrievedSnippet(
                score=h.score,
                rel_path=h.rel_path,
                effective_date=h.effective_date,
                heading_path=h.heading_path,
                start_byte=h.start_byte,
                end_byte=h.end_byte,
                excerpt=h.excerpt,
                expanded_context=h.expanded_context,
            )
        )

    return RetrievalResult(query=query, snippets=snippets)


def format_sources(result: RetrievalResult, *, max_items: int = 3) -> str:
    if not result.snippets:
        return "Sources: (none)"

    lines = ["Sources:"]
    for s in result.snippets[: max(0, int(max_items))]:
        heading = f" · {s.heading_path}" if s.heading_path else ""
        lines.append(
            f"- {s.rel_path} ({s.effective_date}){heading} [{s.start_byte}-{s.end_byte}]"
        )
    return "\n".join(lines)
