from __future__ import annotations

from totem.daemon_ask.packer import PackConfig, pack_context
from totem.daemon_search.models import SearchHit


def test_pack_context_is_deterministic_and_budgeted() -> None:
    hits = [
        SearchHit(
            score=1.0,
            rel_path="a.md",
            title="A",
            heading_path="",
            start_byte=0,
            end_byte=10,
            effective_date="2026-01-01",
            mtime_ns=1,
            excerpt="x" * 50,
            expanded_context=False,
        ),
        SearchHit(
            score=0.9,
            rel_path="b.md",
            title="B",
            heading_path="",
            start_byte=0,
            end_byte=10,
            effective_date="2026-01-02",
            mtime_ns=2,
            excerpt="y" * 50,
            expanded_context=False,
        ),
    ]

    p1 = pack_context(hits, cfg=PackConfig(packed_max_chars=10_000))
    p2 = pack_context(hits, cfg=PackConfig(packed_max_chars=10_000))
    assert p1 == p2
    assert len(p1) == 2

    # Small budget should cut deterministically after the first excerpt.
    p3 = pack_context(hits, cfg=PackConfig(packed_max_chars=130))
    assert len(p3) == 1
    assert p3[0].citation.rel_path == "a.md"
