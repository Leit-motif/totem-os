from __future__ import annotations

from totem.daemon_ask.time import TemporalConfig, apply_temporal_layer
from totem.daemon_search.models import SearchHit


def _hit(
    *,
    rel_path: str,
    effective_date: str,
    score: float = 1.0,
    mtime_ns: int = 1,
    expanded_context: bool = False,
) -> SearchHit:
    return SearchHit(
        score=score,
        rel_path=rel_path,
        title=rel_path,
        heading_path="",
        start_byte=0,
        end_byte=10,
        effective_date=effective_date,
        mtime_ns=mtime_ns,
        excerpt="x",
        expanded_context=expanded_context,
    )


def _cfg() -> TemporalConfig:
    return TemporalConfig(
        default_mode="hybrid",
        window_recent_days=7,
        window_month_days=30,
        window_year_days=180,
        decay_half_life_days=30.0,
        weight_journal=0.20,
        weight_evergreen=0.04,
    )


def test_hybrid_prefers_recent_journal_notes() -> None:
    hits = [
        _hit(rel_path="20_memory/daily/2026-02-01.md", effective_date="2026-02-01", score=1.0, mtime_ns=2),
        _hit(rel_path="20_memory/values.md", effective_date="2026-02-01", score=1.0, mtime_ns=1),
    ]

    out = apply_temporal_layer(hits, mode="hybrid", cfg=_cfg())
    assert [h.rel_path for h in out.hits] == ["20_memory/daily/2026-02-01.md", "20_memory/values.md"]
    assert out.features[0].note_type == "journal"
    assert out.features[0].boost > out.features[1].boost


def test_recent_mode_applies_window_filter() -> None:
    hits = [
        _hit(rel_path="20_memory/daily/2026-02-01.md", effective_date="2026-02-01", mtime_ns=2),
        _hit(rel_path="20_memory/daily/2025-12-01.md", effective_date="2025-12-01", mtime_ns=1),
    ]

    out = apply_temporal_layer(hits, mode="recent", cfg=_cfg())
    assert out.window_days == 7
    assert [h.rel_path for h in out.hits] == ["20_memory/daily/2026-02-01.md"]
    assert out.features[0].within_window is True


def test_all_mode_disables_temporal_boost() -> None:
    hits = [
        _hit(rel_path="20_memory/daily/2026-02-01.md", effective_date="2026-02-01", score=0.6, mtime_ns=2),
        _hit(rel_path="20_memory/values.md", effective_date="2020-01-01", score=0.7, mtime_ns=1),
    ]

    out = apply_temporal_layer(hits, mode="all", cfg=_cfg())
    assert [h.rel_path for h in out.hits] == ["20_memory/values.md", "20_memory/daily/2026-02-01.md"]
    assert out.features[0].boost == 0.0
    assert out.features[1].boost == 0.0
