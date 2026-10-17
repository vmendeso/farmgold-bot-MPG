import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from farmbot.models import BattleRecord, Stage
from farmbot.recovery import failed_stages, nearest_lower_stage


NOW = datetime(2026, 10, 16, 21, tzinfo=timezone.utc)
TARGET = Stage("2-8", "Inferno")


def record(outcome="defeat", *, age_hours=1, target=TARGET, raw=""):
    return BattleRecord(f"{target.key}-{age_hours}-{outcome}", "test", target.stage,
                        target.difficulty, 60, 0, 0, outcome,
                        (NOW - timedelta(hours=age_hours)).isoformat(), raw)


class RecoveryTests(unittest.TestCase):
    def test_selects_immediately_previous_confirmed_stage(self):
        failed = Stage("2-8", "Inferno")
        catalog = [Stage("2-6", "Inferno"), Stage("2-9", "Inferno"),
                   failed, Stage("2-7", "Inferno")]
        self.assertEqual(nearest_lower_stage(failed, catalog), Stage("2-7", "Inferno"))

    def test_gaps_do_not_create_unconfirmed_stage(self):
        failed = Stage("2-8", "Inferno")
        catalog = [Stage("2-4", "Inferno"), Stage("2-6", "Inferno")]
        self.assertEqual(nearest_lower_stage(failed, catalog), Stage("2-6", "Inferno"))

    def test_act_boundary_uses_highest_confirmed_previous_act_level(self):
        failed = Stage("2-1", "Inferno")
        catalog = [Stage("1-9", "Inferno"), Stage("1-10", "Inferno"),
                   Stage("2-2", "Inferno")]
        self.assertEqual(nearest_lower_stage(failed, catalog), Stage("1-10", "Inferno"))

    def test_never_switches_difficulty(self):
        failed = Stage("2-8", "Inferno")
        catalog = [Stage("2-7", "Pesadelo"), Stage("2-6", "Inferno"),
                   Stage("2-7", "Averno")]
        self.assertEqual(nearest_lower_stage(failed, catalog), Stage("2-6", "Inferno"))
        self.assertIsNone(nearest_lower_stage(failed, [Stage("2-7", "Pesadelo")]))

    def test_no_lower_confirmed_stage_returns_none(self):
        failed = Stage("1-1", "Inferno")
        for catalog in ([], [failed], [Stage("1-2", "Inferno")]):
            with self.subTest(catalog=catalog):
                self.assertIsNone(nearest_lower_stage(failed, catalog))

    def test_accepts_generator_and_ignores_duplicate_candidates(self):
        failed = Stage("2-8", "Inferno")
        catalog = (Stage(stage, "Inferno") for stage in ("2-4", "2-7", "2-7", "2-6"))
        self.assertEqual(nearest_lower_stage(failed, catalog), Stage("2-7", "Inferno"))


class FailedHistoryTests(unittest.TestCase):
    def failed(self, history, **kwargs):
        return failed_stages(history, cache_hours=6, now=NOW, **kwargs)

    def test_timed_defeat_and_explicit_collector_failure_are_evidence(self):
        second = Stage("1-8", "Averno")
        history = [record(), record("interrupted", target=second,
                                    raw="StageDefeated: 1-8 falhou; sem duração")]
        self.assertEqual(self.failed(history), {TARGET, second})

    def test_ordinary_interruptions_and_arbitrary_failure_text_are_not_evidence(self):
        for raw in ("2-8 falhou", "NavigationError: 2-8 falhou",
                    "VisionError: StageDefeated: 2-8", " StageDefeated: 2-8",
                    "StageDefeated sem dois pontos", "Timeout sem resultado"):
            with self.subTest(raw=raw):
                self.assertEqual(self.failed([record("interrupted", raw=raw)]), set())
        self.assertEqual(self.failed([record("victory", raw="StageDefeated: texto")]), set())

    def test_later_victory_clears_failure_regardless_of_input_order(self):
        failure, victory = record(age_hours=2), record("victory", age_hours=1)
        self.assertEqual(self.failed([failure, victory]), set())
        self.assertEqual(self.failed([victory, failure]), set())
        self.assertEqual(self.failed([victory, failure, record(age_hours=0.5)]), {TARGET})

    def test_victory_at_same_time_does_not_override_failure(self):
        failure, victory = record(age_hours=1), record("victory", age_hours=1)
        self.assertEqual(self.failed([failure, victory]), {TARGET})
        self.assertEqual(self.failed([victory, failure]), {TARGET})

    def test_unrelated_interruptions_neither_clear_nor_refresh_failure(self):
        interruption = record("interrupted", age_hours=0.5, raw="NavigationError: foco perdido")
        self.assertEqual(self.failed([record(age_hours=2), interruption]), {TARGET})
        self.assertEqual(self.failed([record(age_hours=7), interruption]), set())
        self.assertEqual(self.failed([record(age_hours=2), record("victory", age_hours=1),
                                      interruption]), set())

    def test_future_stale_and_timezone_less_evidence_is_excluded(self):
        for invalid in (record(age_hours=6), record(age_hours=7), record(age_hours=-1),
                        replace(record(), observed_at=NOW.replace(tzinfo=None).isoformat()),
                        replace(record(), observed_at="bad timestamp")):
            with self.subTest(record=invalid):
                self.assertEqual(self.failed([invalid]), set())
        self.assertEqual(self.failed([record(), record("victory", age_hours=-1)]), {TARGET})

    def test_victory_in_other_difficulty_cannot_clear_failure(self):
        other = Stage(TARGET.stage, "Pesadelo")
        self.assertEqual(self.failed([record(), record("victory", age_hours=0.5, target=other)]),
                         {TARGET})

    def test_timezone_offsets_and_utc_z_are_compared_as_instants(self):
        failure = replace(record(), observed_at="2026-10-16T17:00:00-03:00")
        victory = replace(record("victory"), observed_at="2026-10-16T20:30:00Z")
        self.assertEqual(self.failed([failure]), {TARGET})
        self.assertEqual(self.failed([failure, victory]), set())

    def test_zero_window_disables_failure_cache_and_invalid_options_raise(self):
        self.assertEqual(failed_stages([record()], cache_hours=0, now=NOW), set())
        for invalid in (-1, float("inf"), float("nan"), True, "6"):
            with self.subTest(cache_hours=invalid):
                with self.assertRaises(ValueError):
                    failed_stages([record()], cache_hours=invalid, now=NOW)
        with self.assertRaises(ValueError):
            failed_stages([record()], cache_hours=6, now=NOW.replace(tzinfo=None))

if __name__ == "__main__":
    unittest.main()
