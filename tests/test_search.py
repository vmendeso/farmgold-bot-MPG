import unittest
from datetime import datetime, timedelta, timezone

from farmbot.models import BattleRecord, Stage
from farmbot.search import SearchPlanner
from farmbot.storage import stats


def measured(target, gold=100, victories=5, age_hours=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    return stats([
        BattleRecord(f"{target.key}-{index}", "simulation", target.stage, target.difficulty,
                     10, gold if index < victories else 0, 0,
                     "victory" if index < victories else "defeat", timestamp)
        for index in range(5)
    ])[0]


def partial_measurement(target, gold_per_minute=597, age_hours=1):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    return stats([BattleRecord(f"{target.key}-partial", "previous-run", target.stage,
                               target.difficulty, 60, gold_per_minute, 0,
                               "victory", timestamp)])[0]


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.current = Stage("4-8", "Pesadelo")
        self.candidates = [Stage(f"4-{i}", "Pesadelo") for i in range(5, 11)]
        self.candidates += [Stage("4-8", "Normal"), Stage("4-8", "Difícil")]

    def test_starts_current_then_nearby_unlocked_stages(self):
        planner = SearchPlanner(self.current, self.candidates)
        self.assertEqual(planner.next_stage([]), self.current)
        planner.mark_tested(self.current)
        first = planner.next_stage([])
        self.assertEqual(first, Stage("4-7", "Pesadelo"))
        planner.mark_tested(first)
        self.assertEqual(planner.next_stage([]), Stage("4-9", "Pesadelo"))

    def test_recent_reliable_cache_and_unreliable_results_are_skipped(self):
        cached = measured(self.current)
        unreliable = measured(Stage("4-7", "Pesadelo"), victories=3)
        planner = SearchPlanner(self.current, self.candidates)
        self.assertEqual(planner.next_stage([cached, unreliable]), Stage("4-9", "Pesadelo"))

    def test_expired_cache_can_be_retested(self):
        planner = SearchPlanner(self.current, self.candidates, cache_hours=6)
        self.assertEqual(planner.next_stage([measured(self.current, age_hours=7)]), self.current)

    def test_other_difficulties_are_explored(self):
        planner = SearchPlanner(self.current, self.candidates, exploration_every=3)
        for stage in [self.current, Stage("4-7", "Pesadelo"), Stage("4-9", "Pesadelo")]:
            planner.mark_tested(stage)
        next_stage = planner.next_stage([measured(self.current)])
        self.assertEqual(next_stage.stage, "4-8")
        self.assertNotEqual(next_stage.difficulty, "Pesadelo")

    def test_simulation_follows_promising_region_with_a_strict_budget(self):
        planner = SearchPlanner(self.current, self.candidates, max_stages=6)
        observations = []
        visited = []
        while True:
            target = planner.next_stage(observations)
            if target is None:
                break
            self.assertIn(target, self.candidates)
            self.assertNotIn(target, visited)
            visited.append(target)
            observations.append(measured(target, gold=1000 if target.stage == "4-7" else 100))
            planner.mark_tested(target)
        self.assertEqual(len(visited), 6)
        self.assertIn(Stage("4-6", "Pesadelo"), visited)
        self.assertTrue(any(target.difficulty != "Pesadelo" for target in visited))

    def test_no_implicit_unlock_or_unknown_target(self):
        planner = SearchPlanner(self.current, [Stage("4-7", "Normal")])
        self.assertEqual(planner.next_stage([]), Stage("4-7", "Normal"))
        with self.assertRaises(ValueError):
            planner.mark_tested(self.current)

    def test_one_recent_historical_completion_three_gold_below_reference_skips_stage(self):
        planner = SearchPlanner(self.current, self.candidates, early_stop_gold_per_minute=3)
        candidate = partial_measurement(self.current)
        reference = measured(Stage("4-9", "Pesadelo"))  # 600 Gold/min, 5 completions.
        self.assertFalse(candidate.reliable)
        self.assertEqual(candidate.victories, 1)
        with self.assertLogs("farmbot.search", level="INFO") as captured:
            self.assertEqual(planner.next_stage([candidate, reference]), Stage("4-7", "Pesadelo"))
        self.assertIn(self.current.key, captured.output[0])
        self.assertIn("3.00", captured.output[0])

    def test_stale_candidate_or_reference_cannot_eliminate_candidate(self):
        for candidate_age, reference_age in ((7, 0), (1, 7)):
            with self.subTest(candidate_age=candidate_age, reference_age=reference_age):
                planner = SearchPlanner(self.current, self.candidates, early_stop_gold_per_minute=3)
                observations = [partial_measurement(self.current, age_hours=candidate_age),
                                measured(Stage("4-9", "Pesadelo"), age_hours=reference_age)]
                self.assertEqual(planner.next_stage(observations), self.current)

    def test_reference_outside_confirmed_catalog_cannot_eliminate_candidate(self):
        planner = SearchPlanner(self.current, self.candidates, early_stop_gold_per_minute=3)
        unconfirmed = measured(Stage("2-6", "Inferno"), gold=10000)
        observations = [partial_measurement(self.current), unconfirmed]
        self.assertEqual(planner.next_stage(observations), self.current)

    def test_disabled_rule_or_absent_reliable_reference_keeps_normal_exploration(self):
        candidate = partial_measurement(self.current)
        reference = measured(Stage("4-9", "Pesadelo"))
        disabled = SearchPlanner(self.current, self.candidates)
        self.assertEqual(disabled.next_stage([candidate, reference]), self.current)
        for observations in ([candidate], [candidate, partial_measurement(reference.target, 600)]):
            with self.subTest(observations=observations):
                planner = SearchPlanner(self.current, self.candidates, early_stop_gold_per_minute=3)
                self.assertEqual(planner.next_stage(observations), self.current)

    def test_history_skip_does_not_consume_budget_for_new_measurements(self):
        planner = SearchPlanner(self.current, self.candidates, max_stages=1,
                                early_stop_gold_per_minute=3)
        observations = [partial_measurement(self.current), measured(Stage("4-9", "Pesadelo"))]
        target = planner.next_stage(observations)
        self.assertEqual(target, Stage("4-7", "Pesadelo"))
        planner.mark_tested(target)
        self.assertIsNone(planner.next_stage(observations))

    def test_threshold_validation_matches_profitability_policy(self):
        for invalid in (0, -3, True, "3", float("inf"), float("nan")):
            with self.subTest(threshold=invalid):
                with self.assertRaises(ValueError):
                    SearchPlanner(self.current, self.candidates, early_stop_gold_per_minute=invalid)


if __name__ == "__main__":
    unittest.main()
