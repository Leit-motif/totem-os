from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from totem.daemon_search.models import SearchHit

_DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$", re.IGNORECASE)
_TIME_MODES = {"recent", "month", "year", "all", "hybrid"}


def _parse_date(value: str) -> date | None:
    try:
        y, m, d = value.split("-", 2)
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _note_type(rel_path: str) -> str:
    path = rel_path.replace("\\", "/").lower()
    filename = path.rsplit("/", 1)[-1]
    if "/20_memory/daily/" in path or "/journal/" in path or "/journals/" in path or _DATE_FILE_RE.match(filename):
        return "journal"
    return "evergreen"


def _effective_date(hit: SearchHit) -> date:
    parsed = _parse_date(hit.effective_date)
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(hit.mtime_ns / 1_000_000_000, tz=timezone.utc).date()


@dataclass(frozen=True)
class TemporalConfig:
    default_mode: str
    window_recent_days: int
    window_month_days: int
    window_year_days: int
    decay_half_life_days: float
    weight_journal: float
    weight_evergreen: float


@dataclass(frozen=True)
class TemporalFeature:
    mode: str
    note_type: str
    reference_date: str
    effective_date: str
    age_days: int
    window_days: int | None
    within_window: bool
    half_life_days: float
    weight: float
    decay: float
    boost: float
    base_score: float
    temporal_score: float


@dataclass(frozen=True)
class TemporalResult:
    hits: list[SearchHit]
    features: list[TemporalFeature]
    mode: str
    reference_date: str | None
    window_days: int | None


def _normalize_mode(mode: str | None, *, default_mode: str) -> str:
    candidate = (mode or default_mode).strip().lower()
    return candidate if candidate in _TIME_MODES else default_mode


def apply_temporal_layer(
    hits: list[SearchHit],
    *,
    mode: str | None,
    cfg: TemporalConfig,
) -> TemporalResult:
    if not hits:
        selected_mode = _normalize_mode(mode, default_mode=cfg.default_mode)
        return TemporalResult(hits=[], features=[], mode=selected_mode, reference_date=None, window_days=None)

    selected_mode = _normalize_mode(mode, default_mode=cfg.default_mode)
    hit_dates = [_effective_date(h) for h in hits]
    reference = max(hit_dates)

    windows = {
        "recent": max(0, int(cfg.window_recent_days)),
        "month": max(0, int(cfg.window_month_days)),
        "year": max(0, int(cfg.window_year_days)),
        "all": None,
        "hybrid": None,
    }
    window_days = windows.get(selected_mode)
    half_life = max(0.0, float(cfg.decay_half_life_days))

    scored: list[tuple[float, SearchHit, TemporalFeature]] = []
    for h in hits:
        eff = _effective_date(h)
        age_days = max(0, (reference - eff).days)
        note_type = _note_type(h.rel_path)
        weight = 0.0
        if selected_mode != "all":
            weight = float(cfg.weight_journal if note_type == "journal" else cfg.weight_evergreen)
        decay = 0.0
        if half_life > 0.0:
            decay = math.exp(-math.log(2.0) * (age_days / half_life))
        boost = weight * decay
        temporal_score = float(h.score) + boost
        within_window = window_days is None or age_days <= window_days
        scored.append(
            (
                temporal_score,
                h,
                TemporalFeature(
                    mode=selected_mode,
                    note_type=note_type,
                    reference_date=reference.isoformat(),
                    effective_date=eff.isoformat(),
                    age_days=age_days,
                    window_days=window_days,
                    within_window=within_window,
                    half_life_days=half_life,
                    weight=weight,
                    decay=decay,
                    boost=boost,
                    base_score=float(h.score),
                    temporal_score=temporal_score,
                ),
            )
        )

    filtered = [row for row in scored if row[2].within_window]
    active = filtered if filtered else scored

    active.sort(
        key=lambda row: (
            row[1].expanded_context,
            -row[0],
            -int(row[1].mtime_ns),
            row[1].rel_path,
            int(row[1].start_byte),
            int(row[1].end_byte),
        )
    )

    return TemporalResult(
        hits=[row[1] for row in active],
        features=[row[2] for row in active],
        mode=selected_mode,
        reference_date=reference.isoformat(),
        window_days=window_days,
    )
