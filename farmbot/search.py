"""Bounded, measurement-driven search across visually confirmed stages."""
from __future__ import annotations

from datetime import datetime, timezone
import logging

from .models import Stage, StageStats
from .profitability import profitability_drop, validate_profitability_threshold

log = logging.getLogger(__name__)


class SearchPlanner:
    def __init__(self, current: Stage, candidates: list[Stage], battles_per_stage: int = 5,
                 max_stages: int = 12, min_win_rate: float = 0.8,
                 cache_hours: float = 6, exploration_every: int = 3,
                 early_stop_gold_per_minute: float | None = None) -> None:
        if battles_per_stage < 1 or max_stages < 1 or exploration_every < 1:
            raise ValueError("Search counts must be positive")
        if not 0 <= min_win_rate <= 1 or cache_hours < 0:
            raise ValueError("Invalid search thresholds")
        early_stop_gold_per_minute = validate_profitability_threshold(early_stop_gold_per_minute)
        self.current = current
        # Only the caller's visually confirmed unlocked catalog is eligible.
        self.candidates = sorted(set(candidates), key=lambda target: (target.position, target.difficulty))
        self.battles_per_stage = battles_per_stage
        self.max_stages = max_stages
        self.min_win_rate = min_win_rate
        self.cache_hours = cache_hours
        self.exploration_every = exploration_every
        self.early_stop_gold_per_minute = early_stop_gold_per_minute
        self._history_skipped: set[Stage] = set()
        self._tested: set[Stage] = set()

    def mark_tested(self, stage: Stage) -> None:
        if stage not in self.candidates:
            raise ValueError("Cannot mark an unconfirmed stage as tested")
        self._tested.add(stage)

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_stages - len(self._tested))

    def _distance(self, left: Stage, right: Stage) -> tuple[int, int]:
        # Prefer the same act; the catalog may contain different act lengths.
        return abs(left.position[0] - right.position[0]), abs(left.position[1] - right.position[1])

    def _recent(self, item: StageStats, now: datetime) -> bool:
        try:
            observed = datetime.fromisoformat(item.latest_at.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                return False
            age = (now - observed.astimezone(timezone.utc)).total_seconds()
            return 0 <= age < self.cache_hours * 3600
        except (TypeError, ValueError):
            return False

    def next_stage(self, stats: list[StageStats]) -> Stage | None:
        if len(self._tested) >= self.max_stages:
            return None
        measurements = {item.target: item for item in stats}
        now = datetime.now(timezone.utc)
        eligible: list[Stage] = []
        for target in self.candidates:
            if target in self._tested:
                continue
            decision = profitability_drop(target, stats, threshold=self.early_stop_gold_per_minute,
                                          battles_per_stage=self.battles_per_stage,
                                          min_win_rate=self.min_win_rate, cache_hours=self.cache_hours,
                                          accessible=self.candidates, now=now)
            if decision is not None:
                if target not in self._history_skipped:
                    log.info("Histórico evita nova coleta em %s: %.2f Gold/min, %.2f abaixo de %s (%.2f Gold/min)",
                             target.key, decision.candidate.gold_per_minute, decision.difference,
                             decision.reference.target.key, decision.reference.gold_per_minute)
                    self._history_skipped.add(target)
                continue
            item = measurements.get(target)
            if item is not None:
                completed = item.victories + item.defeats
                if completed >= self.battles_per_stage and item.win_rate < self.min_win_rate and self._recent(item, now):
                    continue
                if (item.reliable and completed >= self.battles_per_stage
                        and item.win_rate >= self.min_win_rate and self._recent(item, now)):
                    continue
            eligible.append(target)
        if not eligible:
            return None
        if not self._tested and self.current in eligible:
            return self.current
        trustworthy = [item for item in stats if item.target in self.candidates
                       and item.reliable and item.win_rate >= self.min_win_rate and self._recent(item, now)]
        best = max(trustworthy, key=lambda item: item.gold_per_minute).target if trustworthy else self.current
        explore = bool(self._tested) and len(self._tested) % self.exploration_every == 0
        if explore:
            other_difficulties = [target for target in eligible if target.difficulty != best.difficulty]
            if other_difficulties:
                return min(other_difficulties, key=lambda target: (
                    self._distance(target, best), self._distance(target, self.current),
                    target.difficulty, target.position))
        # Establish nearby comparisons, then follow the neighborhood with the
        # best measured yield. The exploration budget periodically tries another
        # difficulty even if the current difficulty continues looking promising.
        anchor = self.current if len(self._tested) < 3 else best
        return min(eligible, key=lambda target: (
            target.difficulty != anchor.difficulty, self._distance(target, anchor),
            self._distance(target, self.current), target.position, target.difficulty))
