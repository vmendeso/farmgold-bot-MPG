"""Decide when recent measured yield justifies ending a stage measurement."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .models import BattleRecord, Stage, StageStats
from .storage import stats


@dataclass(frozen=True)
class ProfitabilityDrop:
    candidate: StageStats
    reference: StageStats
    difference: float  # Absolute Gold/min, never a percentage.


def validate_profitability_threshold(value: float | None) -> float | None:
    """An absent threshold disables early departure; enabled values are positive."""
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError("Profitability drop must be finite positive Gold/min or null")
    return float(value)


def _clock(now: datetime | None) -> datetime:
    instant = datetime.now(timezone.utc) if now is None else now
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Profitability comparison requires a timezone-aware clock")
    return instant.astimezone(timezone.utc)


def _validate_window(cache_hours: float) -> None:
    if (isinstance(cache_hours, bool) or not isinstance(cache_hours, (int, float))
            or not math.isfinite(cache_hours) or cache_hours < 0):
        raise ValueError("Profitability history window must be finite and nonnegative")


def _recent(timestamp: str, now: datetime, cache_hours: float) -> bool:
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return False
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < cache_hours * 3600
    except (AttributeError, TypeError, ValueError):
        return False


def recent_profitability(records: Iterable[BattleRecord], *, battles_per_stage: int = 5,
                         min_win_rate: float = 0.8, cache_hours: float = 6,
                         battle_restart_seconds: float = 0.0,
                         now: datetime | None = None) -> list[StageStats]:
    """Aggregate only observations inside the cache window, excluding future data.

    Filtering records *before* aggregation prevents a recent interruption from
    making old completed battles eligible again. The ratio remains total rewards
    divided by total observed time plus configured restart time per completed
    battle. Interruptions retain their observed wall time without restart overhead.
    """
    _validate_window(cache_hours)
    instant = _clock(now)
    return stats((record for record in records
                  if _recent(record.observed_at, instant, cache_hours)),
                 min_samples=battles_per_stage, min_win_rate=min_win_rate,
                 battle_restart_seconds=battle_restart_seconds)


def profitability_drop(current: Stage, measurements: Iterable[StageStats], *,
                       threshold: float | None = None, battles_per_stage: int = 5,
                       min_win_rate: float = 0.8, cache_hours: float = 6,
                       accessible: Iterable[Stage] | None = None,
                       now: datetime | None = None) -> ProfitabilityDrop | None:
    """Compare a measured candidate to the best other trustworthy recent stage.

    ``measurements`` must already aggregate the desired window of observations,
    as returned by ``recent_profitability``. A candidate needs one completed
    result; the reference needs the full configured sample and win rate. When
    supplied, ``accessible`` restricts references to the caller's known catalog.
    Neither duplicate visible rows nor individual rates are averaged here.
    """
    limit = validate_profitability_threshold(threshold)
    if limit is None:
        return None
    if (isinstance(battles_per_stage, bool) or not isinstance(battles_per_stage, int)
            or battles_per_stage < 1):
        raise ValueError("Profitability comparison needs a positive battle count")
    if (isinstance(min_win_rate, bool) or not isinstance(min_win_rate, (int, float))
            or not math.isfinite(min_win_rate) or not 0 <= min_win_rate <= 1):
        raise ValueError("Profitability win rate must be between zero and one")
    _validate_window(cache_hours)
    instant = _clock(now)
    eligible = [item for item in measurements
                if _recent(item.latest_at, instant, cache_hours)
                and math.isfinite(item.gold_per_minute) and item.gold_per_minute >= 0
                and math.isfinite(item.total_seconds) and item.total_seconds > 0
                and item.victories + item.defeats >= 1]
    candidates = [item for item in eligible if item.target == current]
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    catalog = None if accessible is None else set(accessible)
    references = [item for item in eligible if item.target != current
                  and item.reliable and item.victories + item.defeats >= battles_per_stage
                  and math.isfinite(item.win_rate) and item.win_rate >= min_win_rate
                  and (catalog is None or item.target in catalog)]
    if not references:
        return None
    reference = max(references, key=lambda item: (item.gold_per_minute, item.win_rate,
                                                 item.victories + item.defeats, item.target.key))
    difference = reference.gold_per_minute - candidate.gold_per_minute
    if difference <= 0:
        return None
    if difference < limit and not math.isclose(difference, limit, rel_tol=1e-12, abs_tol=1e-9):
        return None
    return ProfitabilityDrop(candidate=candidate, reference=reference, difference=difference)
