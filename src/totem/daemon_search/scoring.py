from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable


def min_max_normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    vmin = min(values.values())
    vmax = max(values.values())
    if vmax == vmin:
        return {k: 1.0 for k in values.keys()}
    return {k: (v - vmin) / (vmax - vmin) for k, v in values.items()}


def recency_boost(
    *,
    effective_date: str,
    now_utc: date,
    half_life_days: float,
    weight: float,
) -> float:
    if weight <= 0:
        return 0.0
    if half_life_days <= 0:
        return 0.0
    try:
        y, m, d = effective_date.split("-", 2)
        dt = date(int(y), int(m), int(d))
    except Exception:
        return 0.0
    age_days = max(0, (now_utc - dt).days)
    decay = math.exp(-math.log(2.0) * (age_days / half_life_days))
    return float(weight) * decay


@dataclass(frozen=True)
class ScoredItem:
    key: str
    score: float

