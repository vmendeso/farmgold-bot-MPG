"""Choose a lower stage only from the caller's visually confirmed catalog."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

from .models import BattleRecord, Stage


def nearest_lower_stage(failed: Stage, catalog: Iterable[Stage]) -> Stage | None:
    """Return the closest earlier position at the failed stage's difficulty.

    Catalog gaps and act boundaries are respected; no missing stage or other
    difficulty is inferred to be accessible.
    """
    return max((target for target in catalog
                if target.difficulty == failed.difficulty and target.position < failed.position),
               key=lambda target: target.position, default=None)


def failed_stages(records: Iterable[BattleRecord], *, cache_hours: float,
                  now: datetime | None = None) -> set[Stage]:
    """Find recent confirmed failures not superseded by a later real victory.

    Timed defeats and the collector's explicit ``StageDefeated:`` interruption
    marker are evidence. Other interruptions neither clear a failure nor refresh
    its age. A victory must be strictly later; equal timestamps retain failure.
    """
    if (isinstance(cache_hours, bool) or not isinstance(cache_hours, (int, float))
            or not math.isfinite(cache_hours) or cache_hours < 0):
        raise ValueError("Failure history window must be finite and nonnegative")
    instant = datetime.now(timezone.utc) if now is None else now
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Failure history requires a timezone-aware clock")
    instant = instant.astimezone(timezone.utc)
    latest: dict[Stage, tuple[datetime, bool]] = {}
    for record in records:
        failed = record.outcome == "defeat" or (
            record.outcome == "interrupted" and record.raw.startswith("StageDefeated:"))
        if not failed and record.outcome != "victory":
            continue
        try:
            observed = datetime.fromisoformat(record.observed_at.replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.utcoffset() is None:
                continue
            observed = observed.astimezone(timezone.utc)
            age = (instant - observed).total_seconds()
        except (AttributeError, TypeError, ValueError):
            continue
        if not 0 <= age < cache_hours * 3600:
            continue
        target = Stage(record.stage, record.difficulty)
        evidence = (observed, failed)
        if target not in latest or evidence > latest[target]:
            latest[target] = evidence
    return {target for target, (_, failed) in latest.items() if failed}
