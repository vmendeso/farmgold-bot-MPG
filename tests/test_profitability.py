import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from farmbot.models import BattleRecord, Stage
from farmbot.profitability import (profitability_drop, recent_profitability,
                                   validate_profitability_threshold)
from farmbot.storage import stats


NOW = datetime(2026, 10, 16, 21, tzinfo=timezone.utc)
CANDIDATE = Stage("2-8", "Inferno")
REFERENCE = Stage("2-6", "Inferno")


def records(target, gold, count=5, duration=60, age_hours=1, outcome="victory"):
    return [BattleRecord(f"{target.key}-{index}-{age_hours}-{outcome}", "test", target.stage,
                         target.difficulty, duration, gold, 0, outcome,
                         (NOW - timedelta(hours=age_hours)).isoformat())
            for index in range(count)]


def measured(target, gold, count=5, **kwargs):
    return stats(records(target, gold, count, **kwargs))[0]


class ProfitabilityTests(unittest.TestCase):
    def decide(self, rows, **kwargs):
        options = dict(threshold=3, now=NOW)
        options.update(kwargs)
        return profitability_drop(CANDIDATE, rows, **options)

    def test_exact_absolute_three_gold_per_minute_triggers_with_one_completion(self):
        candidate = measured(CANDIDATE, 1846, count=1)
        reference = measured(REFERENCE, 1849)
        result = self.decide([candidate, reference])
        self.assertFalse(candidate.reliable)
        self.assertEqual(result.candidate, candidate)
        self.assertEqual(result.reference, reference)
        self.assertAlmostEqual(result.difference, 3)

    def test_less_than_three_or_better_candidate_keeps_measuring(self):
        reference = measured(REFERENCE, 1849)
        for gold in (1847, 1849, 1900):
            with self.subTest(gold=gold):
                self.assertIsNone(self.decide([measured(CANDIDATE, gold, count=1), reference]))

    def test_boundary_tolerates_only_float_roundoff(self):
        candidate = measured(CANDIDATE, 1846, count=1)
        reference = measured(REFERENCE, 1849)
        self.assertIsNotNone(self.decide([candidate, replace(reference, gold_per_minute=1849 - 1e-11)]))
        self.assertIsNone(self.decide([candidate, replace(reference, gold_per_minute=1849 - 1e-6)]))
        self.assertIsNone(self.decide([candidate, replace(reference, gold_per_minute=1846)], threshold=1e-12))

    def test_disabled_by_default_or_null(self):
        rows = [measured(CANDIDATE, 1, count=1), measured(REFERENCE, 2000)]
        self.assertIsNone(profitability_drop(CANDIDATE, rows))
        self.assertIsNone(self.decide(rows, threshold=None))

    def test_threshold_rejects_nonpositive_nonfinite_and_boolean_values(self):
        for value in (0, -3, float("nan"), float("inf"), -float("inf"), True, False, "3"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_profitability_threshold(value)
        self.assertEqual(validate_profitability_threshold(3), 3.0)

    def test_reference_requires_full_reliable_sample_and_win_rate(self):
        candidate = measured(CANDIDATE, 100, count=1)
        reference = measured(REFERENCE, 1000)
        for bad in (replace(reference, reliable=False),
                    replace(reference, victories=4),
                    replace(reference, win_rate=0.79),
                    replace(reference, win_rate=float("nan"))):
            with self.subTest(reference=bad):
                self.assertIsNone(self.decide([candidate, bad]))
        self.assertIsNotNone(self.decide([candidate, reference]))
        self.assertIsNone(self.decide([candidate, reference], battles_per_stage=6))

    def test_missing_or_only_interrupted_candidate_cannot_trigger(self):
        reference = measured(REFERENCE, 1000)
        interruptions = measured(CANDIDATE, 0, count=6, outcome="interrupted")
        self.assertIsNone(self.decide([reference]))
        self.assertIsNone(self.decide([interruptions, reference]))

    def test_only_accessible_reference_is_used_when_catalog_provided(self):
        inaccessible = measured(Stage("4-8", "Pesadelo"), 3000)
        candidate = measured(CANDIDATE, 1000, count=1)
        reference = measured(REFERENCE, 1004)
        result = self.decide([candidate, reference, inaccessible], accessible=[REFERENCE])
        self.assertEqual(result.reference, reference)
        self.assertIsNone(self.decide([candidate, reference, inaccessible], accessible=[]))

    def test_same_stage_is_excluded_but_same_number_other_difficulty_can_compare(self):
        candidate = measured(CANDIDATE, 1000)
        other_difficulty = measured(Stage(CANDIDATE.stage, "Pesadelo"), 1010)
        self.assertIsNone(self.decide([candidate]))
        self.assertEqual(self.decide([candidate, other_difficulty]).reference, other_difficulty)

    def test_best_reference_selected_and_rates_are_weighted_by_observed_time(self):
        # 60 Gold in 60s plus 180 Gold in 30s yields 160 Gold/min,
        # not the 210 Gold/min arithmetic mean of the individual rates.
        candidate_records = records(CANDIDATE, 60, count=1)
        candidate_records += [replace(candidate_records[0], event_id="second", gold=180, duration=30)]
        rows = stats(candidate_records + records(REFERENCE, 163)
                     + records(Stage("2-7", "Inferno"), 162))
        result = self.decide(rows)
        self.assertAlmostEqual(result.candidate.gold_per_minute, 160)
        self.assertEqual(result.reference.target, REFERENCE)
        self.assertAlmostEqual(result.difference, 3)

    def test_stale_future_or_unzoned_aggregates_are_excluded(self):
        candidate = measured(CANDIDATE, 100, count=1)
        reference = measured(REFERENCE, 1000)
        for timestamp in ((NOW - timedelta(hours=6)).isoformat(),
                          (NOW + timedelta(seconds=1)).isoformat(),
                          NOW.replace(tzinfo=None).isoformat(), "bad"):
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(self.decide([candidate, replace(reference, latest_at=timestamp)]))
                self.assertIsNone(self.decide([replace(candidate, latest_at=timestamp), reference]))

    def test_recent_interruption_does_not_refresh_old_completed_results(self):
        history = records(CANDIDATE, 100, age_hours=7)
        history += records(CANDIDATE, 0, count=1, outcome="interrupted", age_hours=0.5)
        history += records(REFERENCE, 1000)
        rows = recent_profitability(history, now=NOW)
        candidate = next(item for item in rows if item.target == CANDIDATE)
        self.assertEqual(candidate.victories, 0)
        self.assertEqual(candidate.interruptions, 1)
        self.assertIsNone(self.decide(rows))

    def test_recent_records_exclude_future_and_expired_samples_before_aggregation(self):
        history = records(CANDIDATE, 100, count=1)
        history += records(CANDIDATE, 10000, age_hours=-0.1)
        history += records(REFERENCE, 10000, age_hours=6)
        history += records(REFERENCE, 104)
        rows = recent_profitability(history, now=NOW)
        result = self.decide(rows)
        self.assertEqual(result.candidate.samples, 1)
        self.assertEqual(result.reference.samples, 5)
        self.assertAlmostEqual(result.difference, 4)

    def test_interruptions_keep_their_observed_time_without_providing_gold(self):
        history = records(CANDIDATE, 100, count=1)
        history += records(CANDIDATE, 999, count=1, outcome="interrupted", duration=60)
        history += records(REFERENCE, 53)
        rows = recent_profitability(history, now=NOW)
        result = self.decide(rows)
        self.assertAlmostEqual(result.candidate.gold_per_minute, 50)
        self.assertEqual(result.candidate.total_gold, 100)
        self.assertEqual(result.difference, 3)

    def test_cache_zero_disables_history_decisions(self):
        history = records(CANDIDATE, 1, count=1) + records(REFERENCE, 2000)
        self.assertEqual(recent_profitability(history, cache_hours=0, now=NOW), [])
        self.assertIsNone(self.decide(stats(history), cache_hours=0))

    def test_restart_time_recalculates_historical_ranking_and_reference(self):
        slower_reference = Stage("2-7", "Inferno")
        history = records(REFERENCE, 100, duration=10)
        history += records(slower_reference, 590, duration=60)
        history += records(CANDIDATE, 500, count=1, duration=55)
        unchanged = tuple(history)
        without_restart = recent_profitability(history, now=NOW)
        with_restart = recent_profitability(history, now=NOW, battle_restart_seconds=5)
        self.assertEqual(self.decide(without_restart).reference.target, REFERENCE)
        decision = self.decide(with_restart)
        self.assertEqual(with_restart[0].target, slower_reference)
        self.assertEqual(decision.reference.target, slower_reference)
        self.assertAlmostEqual(decision.candidate.gold_per_minute, 500)
        self.assertAlmostEqual(decision.reference.gold_per_minute, 590 / 65 * 60)
        self.assertEqual(tuple(history), unchanged)

    def test_restart_time_can_change_three_gold_decision_using_same_history(self):
        # Without restart the gap is 3. With 5s per result it is below 3;
        # both sides must use the same adjustment before applying the threshold.
        history = records(CANDIDATE, 1846, count=1) + records(REFERENCE, 1849)
        self.assertIsNotNone(self.decide(recent_profitability(history, now=NOW)))
        adjusted = recent_profitability(history, now=NOW, battle_restart_seconds=5)
        self.assertIsNone(self.decide(adjusted))

    def test_restart_overhead_applies_only_to_recent_completed_records(self):
        history = records(CANDIDATE, 100, count=1, age_hours=1)
        history += records(CANDIDATE, 0, count=1, outcome="interrupted", duration=30, age_hours=0.5)
        history += records(CANDIDATE, 10000, count=1, age_hours=7)
        adjusted = recent_profitability(history, now=NOW, battle_restart_seconds=5)[0]
        self.assertEqual(adjusted.total_seconds, 95)
        self.assertEqual(adjusted.samples, 2)
        self.assertAlmostEqual(adjusted.gold_per_minute, 100 / 95 * 60)


if __name__ == "__main__":
    unittest.main()
