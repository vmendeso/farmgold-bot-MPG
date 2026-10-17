import csv
import tempfile
import unittest
from pathlib import Path

from farmbot.models import BattleRecord, Stage
from farmbot.storage import Store, export_ranking, stats


def record(identifier, duration=10, gold=100, outcome="victory", stage="4-8",
           difficulty="Pesadelo", timestamp="2026-10-15T12:00:00+00:00"):
    return BattleRecord(str(identifier), "run-1", stage, difficulty,
                        duration, gold, 10, outcome, timestamp, "visual evidence")


class StoreTests(unittest.TestCase):
    def test_unique_event_ids_preserve_identical_battle_occurrences(self):
        with Store(":memory:") as store:
            self.assertTrue(store.add(record("first")))
            self.assertFalse(store.add(record("first")))
            self.assertTrue(store.add(record("second")))
            self.assertEqual(len(store.records()), 2)

    def test_database_survives_reopen_and_filters_utc_instants(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.sqlite3"
            with Store(path) as store:
                store.add(record("a", timestamp="2026-10-15T10:00:00-03:00"))
                store.add(record("b", stage="4-7", timestamp="2026-10-15T14:00:00Z"))
                store.save_catalog([Stage("4-8", "Pesadelo"), Stage("4-7", "Normal")])
            with Store(path) as store:
                self.assertEqual([row.event_id for row in store.records(since="2026-10-15T13:30:00Z")], ["b"])
                self.assertEqual(len(store.records(Stage("4-8", "Pesadelo"))), 1)
                self.assertEqual(len(store.catalog()), 2)
                self.assertIn("+00:00", store.records()[0].observed_at)

    def test_interruption_has_no_unconfirmed_reward(self):
        with Store(":memory:") as store:
            store.add(record("a", outcome="interrupted", gold=999))
            self.assertEqual((store.records()[0].gold, store.records()[0].exp), (0, 0))

    def test_bad_data_does_not_enter_profitability(self):
        with Store(":memory:") as store:
            for item in [record("a", duration=0), record("b", duration=float("nan")),
                         record("c", gold=-5), record("d", timestamp="2026-10-15T13:00:00")]:
                with self.subTest(item=item), self.assertRaises(ValueError):
                    store.add(item)


class StatisticsTests(unittest.TestCase):
    def test_yield_uses_total_reward_over_total_time_including_failures(self):
        rows = [record("a", duration=10, gold=100), record("b", duration=30, gold=300),
                record("c", duration=20, gold=0, outcome="defeat"),
                record("d", duration=40, gold=999, outcome="interrupted")]
        result = stats(rows, min_samples=3, min_win_rate=0.5)[0]
        self.assertEqual((result.victories, result.defeats, result.interruptions), (2, 1, 1))
        self.assertEqual((result.total_gold, result.total_seconds), (400, 100))
        self.assertEqual((result.gold_per_minute, result.gold_per_hour), (240, 14400))
        self.assertEqual((result.mean_gold, result.mean_seconds, result.win_rate), (100, 25, 0.5))
        self.assertTrue(result.reliable)

    def test_interruptions_cannot_satisfy_completed_sample_minimum(self):
        rows = [record(i) for i in range(4)] + [record(4, outcome="interrupted")]
        self.assertFalse(stats(rows)[0].reliable)

    def test_restart_overhead_counts_each_completed_battle_without_changing_raw_data(self):
        rows = [record("a", duration=10, gold=100), record("b", duration=30, gold=300),
                record("c", duration=20, gold=0, outcome="defeat"),
                record("d", duration=40, gold=999, outcome="interrupted")]
        with Store(":memory:") as store:
            for row in rows:
                store.add(row)
            before = store.records()
            result = stats(before, min_samples=3, min_win_rate=0.5, battle_restart_seconds=5)[0]
            self.assertEqual(store.records(), before)
            self.assertEqual([row.duration for row in before], [10, 30, 20, 40])
            self.assertTrue(all(row.raw == "visual evidence" for row in before))
        self.assertEqual(result.total_seconds, 115)
        self.assertEqual(result.mean_seconds, 115 / 4)
        self.assertEqual(result.total_gold, 400)
        self.assertAlmostEqual(result.gold_per_second, 400 / 115)
        self.assertAlmostEqual(result.gold_per_minute, 400 / 115 * 60)
        self.assertAlmostEqual(result.gold_per_hour, 400 / 115 * 3600)
        self.assertEqual((result.samples, result.victories, result.defeats, result.interruptions), (4, 2, 1, 1))
        self.assertTrue(result.reliable)

    def test_interruptions_only_receive_no_restart_overhead(self):
        rows = [record("a", duration=10, outcome="interrupted"),
                record("b", duration=30, outcome="interrupted")]
        result = stats(rows, battle_restart_seconds=5)[0]
        self.assertEqual(result.total_seconds, 40)
        self.assertEqual(result.mean_seconds, 20)
        self.assertEqual(result.total_gold, 0)

    def test_duplicate_event_does_not_double_count_restart(self):
        result = stats([record("same"), record("same")], battle_restart_seconds=5)[0]
        self.assertEqual(result.samples, 1)
        self.assertEqual(result.total_seconds, 15)

    def test_zero_restart_preserves_legacy_calculation_and_invalid_values_raise(self):
        rows = [record("a")]
        self.assertEqual(stats(rows), stats(rows, battle_restart_seconds=0))
        for invalid in (-1, True, False, float("nan"), float("inf"), "5", None):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    stats(rows, battle_restart_seconds=invalid)

    def test_duplicates_of_an_event_id_do_not_change_statistics(self):
        self.assertEqual(stats([record("same"), record("same")])[0].samples, 1)

    def test_simulated_farming_ranks_real_yield_and_rejects_unreliable_stage(self):
        measurements = []
        scenarios = [
            ("4-8", 1362, 43.37, ["victory"] * 5),
            ("4-7", 1200, 35, ["victory"] * 5),
            ("4-6", 1050, 28, ["victory"] * 5),
            ("4-9", 5000, 20, ["victory"] * 3 + ["defeat"] * 2),
        ]
        for stage, gold, duration, outcomes in scenarios:
            for index, outcome in enumerate(outcomes):
                measurements.append(record(f"{stage}-{index}", stage=stage,
                                           duration=duration, gold=gold if outcome == "victory" else 0,
                                           outcome=outcome))
        ranking = stats(measurements)
        self.assertEqual([item.stage for item in ranking], ["4-6", "4-7", "4-8", "4-9"])
        self.assertAlmostEqual(ranking[0].gold_per_minute, 2250)
        self.assertFalse(ranking[-1].reliable)
        with tempfile.TemporaryDirectory() as directory:
            path = export_ranking(ranking, Path(directory) / "ranking.csv")
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["stage"], "4-6")
            self.assertEqual(float(rows[0]["gold_per_minute"]), 2250)


if __name__ == "__main__":
    unittest.main()
