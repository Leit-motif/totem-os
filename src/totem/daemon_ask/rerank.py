from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from totem.daemon_search.models import SearchHit


@dataclass(frozen=True)
class RerankConfig:
    per_file_cap: int
    keep_expanded: bool


def rerank_and_filter(
    hits: list[SearchHit],
    *,
    cfg: RerankConfig,
) -> list[SearchHit]:
    """Deterministic baseline reranker/filter.

    Invariants:
    - Does not reorder inputs; only filters/dedupes in-order.
    - Applies per-file caps deterministically.
    - Allows optionally dropping expanded-context hits.
    """
    seen = set()
    kept: list[SearchHit] = []
    per_file = defaultdict(int)

    for h in hits:
        if h.expanded_context and not cfg.keep_expanded:
            continue

        key = (h.rel_path, int(h.start_byte), int(h.end_byte))
        if key in seen:
            continue
        seen.add(key)

        if cfg.per_file_cap > 0 and per_file[h.rel_path] >= cfg.per_file_cap:
            continue
        per_file[h.rel_path] += 1

        kept.append(h)

    return kept
