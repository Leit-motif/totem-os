from __future__ import annotations

from dataclasses import dataclass

from totem.daemon_search.models import SearchHit

from .models import Citation, PackedExcerpt


@dataclass(frozen=True)
class PackConfig:
    packed_max_chars: int


def pack_context(
    hits: list[SearchHit],
    *,
    cfg: PackConfig,
) -> list[PackedExcerpt]:
    packed: list[PackedExcerpt] = []
    budget = max(0, int(cfg.packed_max_chars))

    for h in hits:
        c = Citation(rel_path=h.rel_path, start_byte=int(h.start_byte), end_byte=int(h.end_byte))
        pe = PackedExcerpt(
            citation=c,
            title=str(h.title or ""),
            heading_path=str(h.heading_path or ""),
            effective_date=str(h.effective_date or ""),
            excerpt=str(h.excerpt or ""),
            score=float(h.score),
            expanded_context=bool(h.expanded_context),
        )
        # Deterministic approximate accounting (characters).
        cost = len(pe.excerpt) + len(pe.title) + len(pe.heading_path) + 64
        if budget and cost > budget:
            break
        budget = max(0, budget - cost)
        packed.append(pe)

    return packed

