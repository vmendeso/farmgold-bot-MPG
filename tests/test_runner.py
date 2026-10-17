import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from farmbot.models import BattleRecord, LogMarker, ParsedBattle, Stage, UntimedDefeat
from farmbot.runner import Collector, StageDefeated, read_results, run_search, save_log_diagnostic
from farmbot.storage import Store


INITIAL = Stage("4-8", "Pesadelo")
NEIGHBOR = Stage("4-7", "Pesadelo")


def configuration(directory):
    return {
        "_base_dir": Path(directory), "dry_run": False, "auto_switch_best": False,
        "measurement": {
            "battles_per_stage": 2, "min_win_rate": 0.8,
            "log_order": "newest_first", "max_log_gap": 30,
            "poll_interval": 1, "battle_timeout": 5, "stage_timeout": 30,
            "logs_roi": [0, 0, 1, 0.3],
        },
        "navigation": {"open_world": [{"click": {"kind": "menu"}, "expect": {"text": "Mundo"}}]},
        "search": {"cache_hours": 6, "max_stages": 2, "exploration_every": 3},
        "output": {"catalog": "catalog.json", "ranking": "ranking.csv", "artifacts": "artifacts"},
    }


def row(number, stage="4-8", outcome="victory"):
    return ParsedBattle(stage, 1.0, number, number * 2, outcome, f"evidence {number}")


def add_measurements(store, target, gold, count=2, outcome="victory", age_hours=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    for index in range(count):
        store.add(BattleRecord(f"{target.key}-{outcome}-{age_hours}-{index}", "test", target.stage,
                               target.difficulty, 10, gold, 0, outcome, timestamp))


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds

    def check(self):
        pass


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = configuration(self.directory.name)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.clock = Clock()
        self.window = Mock()
        self.window.capture.return_value = SimpleNamespace(image=object())
        self.navigator = Mock()
        self.navigator.current_stage.return_value = INITIAL
        self.collector = Collector(self.window, object(), self.navigator, self.clock,
                                   self.store, self.config)

    def collect_snapshots(self, snapshots):
        sequence = iter(snapshots)
        last = snapshots[-1]

        def next_snapshot(*_args, **_kwargs):
            return next(sequence, last)

        with patch("farmbot.runner.read_results", side_effect=next_snapshot), \
                patch("farmbot.runner.time.monotonic", side_effect=self.clock.monotonic), \
                patch("farmbot.runner.error_screenshot"):
            return self.collector.collect(INITIAL)

    def test_anchored_identical_rows_are_distinct_records_with_unique_ids(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), row(6), *baseline[:3]]
        completed = self.collect_snapshots([baseline, baseline, updated, updated])
        records = self.store.records()
        self.assertEqual(completed, 2)
        self.assertEqual([record.gold for record in records], [6, 6])
        self.assertEqual(len({record.event_id for record in records}), 2)
        self.assertEqual(len({record.run_id for record in records}), 1)
        self.assertTrue(all(record.outcome == "victory" for record in records))

    def enable_profitability(self):
        self.config["search"]["early_stop_gold_per_minute"] = 3
        self.config["measurement"]["battles_per_stage"] = 5
        self.collector.comparison_catalog = [INITIAL, NEIGHBOR]

    def test_recent_inferior_history_skips_capture_without_inventing_an_interruption(self):
        self.enable_profitability()
        add_measurements(self.store, NEIGHBOR, 100, count=5)
        add_measurements(self.store, INITIAL, 50, count=1)
        previous = self.store.records()
        with self.assertLogs("farmbot.runner", level="INFO") as messages:
            self.assertEqual(self.collector.collect(INITIAL), 0)
        self.assertIn("Coleta dispensada pelo histórico", " ".join(messages.output))
        self.assertEqual(self.store.records(), previous)
        self.navigator.current_stage.assert_not_called()
        self.window.capture.assert_not_called()

    def test_first_confirmed_inferior_battle_ends_batch_without_false_interruption(self):
        self.enable_profitability()
        add_measurements(self.store, NEIGHBOR, 100, count=5)
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), *baseline[:4]]
        with self.assertLogs("farmbot.runner", level="INFO") as messages:
            self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 1)
        self.assertIn("Troca antecipada após 1 batalha(s)", " ".join(messages.output))
        self.assertEqual([(r.outcome, r.gold) for r in self.store.records(INITIAL)], [("victory", 6)])

    def test_early_exit_preserves_each_identical_confirmed_row_in_current_snapshot(self):
        self.enable_profitability()
        add_measurements(self.store, NEIGHBOR, 100, count=5)
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), row(6), *baseline[:3]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 2)
        records = self.store.records(INITIAL)
        self.assertEqual([(r.outcome, r.gold) for r in records], [("victory", 6), ("victory", 6)])
        self.assertEqual(len({r.event_id for r in records}), 2)

    def test_unreliable_stale_or_inaccessible_reference_does_not_cut_batch(self):
        for count, age, catalog in [(1, 0, [INITIAL, NEIGHBOR]),
                                    (5, 7, [INITIAL, NEIGHBOR]), (5, 0, [INITIAL])]:
            with self.subTest(count=count, age=age, catalog=catalog), Store(":memory:") as store:
                self.enable_profitability()
                self.config["measurement"].update(battles_per_stage=2, battle_timeout=10)
                self.collector.store = store
                self.collector.comparison_catalog = catalog
                add_measurements(store, NEIGHBOR, 100, count=count, age_hours=age)
                baseline = [row(i) for i in range(5, 0, -1)]
                first = [row(6), *baseline[:4]]
                second = [row(7), *first[:4]]
                self.assertEqual(self.collect_snapshots([baseline, baseline, first, first, second, second]), 2)
                self.assertEqual([r.gold for r in store.records(INITIAL)], [6, 7])

    def test_explicit_observation_without_comparison_catalog_keeps_full_batch(self):
        self.config["search"]["early_stop_gold_per_minute"] = 3
        self.config["measurement"]["battle_timeout"] = 10
        add_measurements(self.store, NEIGHBOR, 100)
        baseline = [row(i) for i in range(5, 0, -1)]
        first = [row(6), *baseline[:4]]
        second = [row(7), *first[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, first, first, second, second]), 2)
        self.assertEqual([r.gold for r in self.store.records(INITIAL)], [6, 7])

    def test_unchanged_identical_table_times_out_without_fabricated_victories(self):
        baseline = [row(1)] * 5
        self.assertEqual(self.collect_snapshots([baseline] * 8), 0)
        records = self.store.records()
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].outcome, records[0].gold, records[0].duration),
                         ("interrupted", 0, 5))

    def test_transient_ocr_change_requires_two_equal_snapshots(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        wrong = [row(999), *baseline[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, wrong, baseline, baseline]), 0)
        self.assertEqual([record.outcome for record in self.store.records()], ["interrupted"])

    def test_timeout_after_real_result_records_only_remaining_observation_time(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), *baseline[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 1)
        records = self.store.records()
        victory = next(record for record in records if record.outcome == "victory")
        interrupted = next(record for record in records if record.outcome == "interrupted")
        self.assertEqual(victory.gold, 6)
        self.assertEqual(interrupted.duration, 5)
        self.assertEqual(interrupted.gold, 0)

    def test_confirmed_defeat_counts_toward_completed_batch(self):
        self.config["measurement"]["battles_per_stage"] = 1
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(0, outcome="defeat"), *baseline[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 1)
        self.assertEqual(self.store.records()[0].outcome, "defeat")

    def test_old_untimed_defeat_does_not_poison_baseline_or_count_as_battle(self):
        self.config["measurement"]["battles_per_stage"] = 1
        baseline = [row(3), UntimedDefeat("4-8", "4-8 falhou"), row(1)]
        updated = [row(4), *baseline[:2]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 1)
        self.assertEqual([(r.outcome, r.gold) for r in self.store.records()], [("victory", 4)])

    def test_new_untimed_defeat_interrupts_without_a_fabricated_battle_duration(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [UntimedDefeat("4-8", "4-8 falhou"), *baseline[:4]]
        with self.assertRaisesRegex(RuntimeError, "Derrota confirmada sem duração"):
            self.collect_snapshots([baseline, baseline, updated, updated])
        records = self.store.records()
        self.assertEqual([(r.outcome, r.gold, r.exp, r.duration) for r in records],
                         [("interrupted", 0, 0, 3)])
        self.assertIn("4-8 falhou", records[0].raw)

    def test_untimed_defeat_from_other_stage_does_not_abort_current_measurement(self):
        self.config["measurement"].update(battles_per_stage=1, battle_timeout=10)
        baseline = [row(i) for i in range(5, 0, -1)]
        defeat = [UntimedDefeat("1-8", "1-8 falhou"), *baseline[:4]]
        updated = [row(6), *defeat[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, defeat, defeat, updated, updated]), 1)
        self.assertEqual([(r.outcome, r.gold) for r in self.store.records()], [("victory", 6)])

    def test_new_untimed_defeat_after_automatic_retreat_is_confirmed_twice(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [UntimedDefeat("4-8", "4-8 falhou"), *baseline[:4]]
        self.collector.comparison_catalog = [INITIAL, NEIGHBOR]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaises(StageDefeated) as caught:
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertEqual((caught.exception.target, caught.exception.completed,
                          caught.exception.observed_stage, caught.exception.timed),
                         (INITIAL, 0, NEIGHBOR, False))
        records = self.store.records()
        self.assertEqual([(r.outcome, r.duration, r.gold) for r in records], [("interrupted", 3, 0)])
        self.assertIn("4-8 falhou", records[0].raw)
        self.navigator.select.assert_not_called()

    def test_retreat_may_cross_act_only_with_confirmed_catalog_entry(self):
        previous = Stage("3-9", "Pesadelo")
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [UntimedDefeat("4-8", "4-8 falhou"), *baseline[:4]]
        self.collector.comparison_catalog = [INITIAL, previous]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, previous, previous]
        with self.assertRaises(StageDefeated) as caught:
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertEqual(caught.exception.observed_stage, previous)

    def test_old_defeat_cannot_authorize_changed_stage(self):
        baseline = [row(3), UntimedDefeat("4-8", "4-8 falhou"), row(1)]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaisesRegex(RuntimeError, "sem derrota nova") as caught:
            self.collect_snapshots([baseline] * 4)
        self.assertNotIsInstance(caught.exception, StageDefeated)
        self.assertEqual([r.outcome for r in self.store.records()], ["interrupted"])

    def test_retreat_without_primed_baseline_never_claims_a_defeat(self):
        self.navigator.current_stage.return_value = NEIGHBOR
        with self.assertRaises(RuntimeError) as caught:
            self.collect_snapshots([[UntimedDefeat("4-8", "4-8 falhou")]])
        self.assertNotIsInstance(caught.exception, StageDefeated)
        self.window.capture.assert_not_called()

    def test_retreat_requires_two_equal_complete_log_reads(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        defeat = [UntimedDefeat("4-8", "4-8 falhou"), *baseline[:4]]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaisesRegex(RuntimeError, "duas leituras") as caught:
            self.collect_snapshots([baseline, baseline, defeat, baseline])
        self.assertNotIsInstance(caught.exception, StageDefeated)
        self.assertEqual(self.window.capture.call_count, 4)

    def test_retreat_to_unconfirmed_catalog_entry_is_not_recovered(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        self.collector.comparison_catalog = [INITIAL]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR]
        with self.assertRaises(RuntimeError) as caught:
            self.collect_snapshots([baseline, baseline])
        self.assertNotIsInstance(caught.exception, StageDefeated)
        self.assertEqual(self.window.capture.call_count, 2)

    def test_retreat_proof_expires_when_ocr_keeps_failing(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        self.config["navigation"]["timeout"] = 2
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaisesRegex(RuntimeError, "sem derrota nova") as caught:
            self.collect_snapshots([baseline, baseline, None])
        self.assertNotIsInstance(caught.exception, StageDefeated)
        self.assertEqual(self.clock.now, 4)
        self.assertEqual(self.window.capture.call_count, 4)

    def test_timed_defeat_in_search_stops_with_exact_record_and_no_interruption(self):
        self.collector.stop_on_defeat = True
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(0, outcome="defeat"), *baseline[:4]]
        with self.assertRaises(StageDefeated) as caught:
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertEqual((caught.exception.completed, caught.exception.timed), (1, True))
        self.assertEqual([(r.outcome, r.duration, r.gold) for r in self.store.records()], [("defeat", 1, 0)])

    def test_timed_defeat_after_retreat_never_credits_other_stage_rows(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(100, stage="4-7"), row(0, outcome="defeat"), *baseline[:3]]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaises(StageDefeated):
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertEqual([(r.stage, r.outcome, r.gold) for r in self.store.records()], [("4-8", "defeat", 0)])

    def test_new_defeat_is_not_hidden_by_completing_victory_in_same_snapshot(self):
        self.config["measurement"]["battles_per_stage"] = 1
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [UntimedDefeat("4-8", "4-8 falhou"), row(6), *baseline[:3]]
        with self.assertRaises(StageDefeated) as caught:
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertEqual(caught.exception.completed, 1)
        self.assertEqual([r.outcome for r in self.store.records()], ["victory", "interrupted"])

    def test_retreat_without_overlap_never_claims_new_defeat(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [UntimedDefeat("4-8", "4-8 falhou"), row(99), row(98), row(97), row(96)]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaisesRegex(RuntimeError, "sem derrota nova") as caught:
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertNotIsInstance(caught.exception, StageDefeated)

    def test_retreat_notification_does_not_count_as_defeat(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [LogMarker("Caixa obtida"), *baseline[:4]]
        self.navigator.current_stage.side_effect = [INITIAL, INITIAL, NEIGHBOR, NEIGHBOR]
        with self.assertRaisesRegex(RuntimeError, "sem derrota nova") as caught:
            self.collect_snapshots([baseline, baseline, updated, updated])
        self.assertNotIsInstance(caught.exception, StageDefeated)

    def test_results_from_another_stage_are_not_credited(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(100, stage="4-7"), *baseline[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 0)
        self.assertEqual([record.outcome for record in self.store.records()], ["interrupted"])

    def test_wrong_visible_difficulty_aborts_and_preserves_interruption(self):
        self.navigator.current_stage.return_value = Stage("4-8", "Normal")
        with self.assertRaises(RuntimeError):
            self.collect_snapshots([[row(1)]])
        self.assertEqual([record.outcome for record in self.store.records()], ["interrupted"])
        self.window.capture.assert_not_called()

    def test_emergency_stop_is_propagated_after_recording_interrupted_attempt(self):
        def check():
            if self.clock.now >= 2:
                raise KeyboardInterrupt()

        self.clock.check = check
        with self.assertRaises(KeyboardInterrupt):
            self.collect_snapshots([[row(1)] * 5])
        self.assertEqual(self.store.records()[0].outcome, "interrupted")
        self.assertEqual(self.store.records()[0].duration, 2)

    def test_transient_stage_ocr_failure_recovers_without_inventing_result(self):
        self.config["measurement"]["battle_timeout"] = 10
        self.navigator.current_stage.side_effect = [None, INITIAL, INITIAL, INITIAL, INITIAL]
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), row(6), *baseline[:3]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, updated, updated]), 2)
        self.assertEqual([record.outcome for record in self.store.records()], ["victory", "victory"])

    def test_repeated_incomplete_ocr_is_bounded_and_records_interruption(self):
        from farmbot.vision import VisionError
        self.config["measurement"]["max_ocr_failures"] = 3
        with self.assertRaises(VisionError):
            self.collect_snapshots([None])
        self.assertEqual(self.navigator.current_stage.call_count, 3)
        self.assertEqual(self.store.records()[0].outcome, "interrupted")
        self.assertEqual(self.store.records()[0].duration, 2)

    def test_repeated_ocr_warning_is_throttled_but_final_attempt_is_reported(self):
        from farmbot.vision import VisionError

        self.config["measurement"].update(max_ocr_failures=13, battle_timeout=30)
        with self.assertLogs("farmbot.runner", level="WARNING") as messages, self.assertRaises(VisionError):
            self.collect_snapshots([None])
        warnings = [line for line in messages.output if "OCR não confirmado" in line]
        self.assertEqual(len(warnings), 4)
        for message, attempt in zip(warnings, (1, 5, 10, 13)):
            self.assertIn(f"({attempt}/13)", message)
        self.assertEqual(self.window.capture.call_count, 13)

    def test_ocr_recovery_is_reported_before_confirming_new_battles(self):
        self.config["measurement"]["battle_timeout"] = 20
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), row(6), *baseline[:3]]
        with self.assertLogs("farmbot.runner", level="INFO") as messages:
            self.assertEqual(self.collect_snapshots([None, None, baseline, baseline, updated, updated]), 2)
        recovered = [line for line in messages.output if "OCR recuperado" in line]
        self.assertEqual(len(recovered), 1)
        self.assertIn("após 2 tentativa(s)", recovered[0])
        self.assertEqual([record.outcome for record in self.store.records()], ["victory", "victory"])

    def test_only_first_rejected_frame_is_saved_without_extra_captures(self):
        self.config["measurement"]["battle_timeout"] = 20
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), row(6), *baseline[:3]]
        snapshots = [None, None, baseline, baseline, None, baseline, baseline, updated, updated]
        frames = [SimpleNamespace(image=object()) for _ in snapshots]
        self.window.capture.side_effect = frames
        sequence = iter(snapshots)

        def read(_vision, _image, _cfg, diagnostics=None):
            result = next(sequence)
            diagnostics.update(roi=[0, 0, 1, .3], line_count=5)
            if result is None:
                diagnostics.update(cause="rejected_rows", rejected_lines=[{"line": 1, "reason": "empty_or_unconfirmed"}])
            return result

        with patch("farmbot.runner.read_results", side_effect=read), \
                patch("farmbot.runner.time.monotonic", side_effect=self.clock.monotonic), \
                patch("farmbot.runner.save_log_diagnostic") as save, \
                patch("farmbot.runner.error_screenshot"):
            self.assertEqual(self.collector.collect(INITIAL), 2)
        save.assert_called_once()
        self.assertIs(save.call_args.args[0], frames[0])
        self.assertEqual(save.call_args.args[4]["rejected_lines"], [{"line": 1, "reason": "empty_or_unconfirmed"}])
        self.assertEqual(self.window.capture.call_count, len(snapshots))

    def test_label_failure_never_saves_previous_successful_log_frame(self):
        from farmbot.vision import VisionError

        self.config["measurement"]["max_ocr_failures"] = 2
        self.navigator.current_stage.side_effect = [INITIAL, None, None]

        def read(_vision, _image, _cfg, diagnostics=None):
            diagnostics.update(roi=[0, 0, 1, .3], line_count=1)
            return [row(1)]

        with patch("farmbot.runner.read_results", side_effect=read), \
                patch("farmbot.runner.time.monotonic", side_effect=self.clock.monotonic), \
                patch("farmbot.runner.save_log_diagnostic") as save, \
                patch("farmbot.runner.error_screenshot"), self.assertRaises(VisionError):
            self.collector.collect(INITIAL)
        save.assert_not_called()
        self.window.capture.assert_called_once()

    def test_crop_write_failure_does_not_stop_collection_or_repeat_writes(self):
        import numpy as np

        self.window.capture.return_value = SimpleNamespace(image=np.zeros((20, 30, 3), dtype=np.uint8))
        self.config["measurement"]["battle_timeout"] = 20
        baseline = [row(i) for i in range(5, 0, -1)]
        updated = [row(6), row(6), *baseline[:3]]
        sequence = iter([None, None, baseline, baseline, updated, updated])

        def read(_vision, _image, _cfg, diagnostics=None):
            diagnostics.update(roi=[0, 0, 1, .3])
            return next(sequence)

        with patch("farmbot.runner.read_results", side_effect=read), \
                patch("farmbot.runner.time.monotonic", side_effect=self.clock.monotonic), \
                patch("farmbot.desktop.save_frame", side_effect=OSError("disk full")) as save, \
                patch("farmbot.runner.error_screenshot"):
            self.assertEqual(self.collector.collect(INITIAL), 2)
        save.assert_called_once()
        self.assertEqual([record.outcome for record in self.store.records()], ["victory", "victory"])

    def test_total_stage_timeout_applies_even_with_recent_progress(self):
        self.config["measurement"].update(battles_per_stage=5, battle_timeout=10, stage_timeout=7)
        baseline = [row(i) for i in range(5, 0, -1)]
        first = [row(6), *baseline[:4]]
        second = [row(7), *first[:4]]
        third = [row(8), *second[:4]]
        completed = self.collect_snapshots([baseline, baseline, first, first, second, second, third, third])
        self.assertEqual(completed, 2)
        interrupted = next(record for record in self.store.records() if record.outcome == "interrupted")
        self.assertEqual(interrupted.duration, 2)

    def test_new_notifications_neither_count_as_battles_nor_reset_battle_timeout(self):
        baseline = [row(i) for i in range(5, 0, -1)]
        first = [LogMarker("Caixa de Chefe obtida"), *baseline[:4]]
        second = [LogMarker("Caixa de Chefe aberta"), *first[:4]]
        self.assertEqual(self.collect_snapshots([baseline, baseline, first, first, second, second]), 0)
        records = self.store.records()
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].outcome, records[0].duration), ("interrupted", 5))

    def test_live_text_sequence_of_loot_battle_and_box_opening_counts_only_battles(self):
        self.config["measurement"].update(log_order="oldest_first", battle_timeout=20)
        baseline = [
            "4-8 concluída · 0:43.37 · +1,362 · +3,055 EXP",
            "Caixa obtida",
            "4-8 concluída · 0:43.21 · +1,362 · +3,055 EXP",
            "Caixa de Chefe obtida",
            "4-8 concluída · 0:43.07 · +1,362 · +3,055 EXP",
        ]
        repeated_result = "4-8 concluída · 0:43.12 · +1,362 · +3,055 EXP"
        new_events = ["Caixa obtida", repeated_result, "Caixa de Chefe aberta",
                      "Você obteve Escudo do Guardião x1", repeated_result]
        snapshots = [baseline, baseline]
        visible = baseline
        for event in new_events:
            visible = [*visible[1:], event]
            snapshots.extend([visible, visible])
        vision = Mock()
        vision.read_lines.side_effect = [[SimpleNamespace(text=text) for text in rows] for rows in snapshots]
        self.collector.vision = vision
        with patch("farmbot.runner.time.monotonic", side_effect=self.clock.monotonic), \
                patch("farmbot.runner.error_screenshot"):
            completed = self.collector.collect(INITIAL)
        records = self.store.records()
        self.assertEqual(completed, 2)
        self.assertEqual(len(records), 2)
        self.assertEqual([record.raw for record in records], [repeated_result, repeated_result])
        self.assertEqual(len({record.event_id for record in records}), 2)
        self.assertEqual(sum(record.gold for record in records), 2724)


class ResultReadTests(unittest.TestCase):
    def test_diagnostics_identify_rejected_rows_without_exposing_raw_text(self):
        vision = Mock()
        private_text = "unrelated overlay text"
        vision.read_lines.return_value = [
            SimpleNamespace(text="Caixa obtida"), SimpleNamespace(text=""), SimpleNamespace(text=private_text),
        ]
        diagnostics = {"stale": "previous read"}
        self.assertIsNone(read_results(vision, object(), {"logs_roi": [0, 0, 1, 1]}, diagnostics))
        self.assertEqual(diagnostics["cause"], "rejected_rows")
        self.assertEqual(diagnostics["line_count"], 3)
        self.assertEqual(diagnostics["rejected_lines"], [
            {"line": 2, "reason": "empty_or_unconfirmed"}, {"line": 3, "reason": "unrecognized_format"},
        ])
        self.assertNotIn("stale", diagnostics)
        self.assertNotIn(private_text, json.dumps(diagnostics))

    def test_ocr_engine_exception_retains_only_current_roi_and_cause(self):
        from farmbot.vision import VisionError

        vision = Mock()
        vision.read_lines.side_effect = VisionError("timeout")
        diagnostics = {"cause": "rejected_rows", "rejected_lines": [{"line": 3}]}
        with self.assertRaises(VisionError):
            read_results(vision, object(), {"logs_roi": [0, 0, 1, .3]}, diagnostics)
        self.assertEqual(diagnostics, {"roi": [0, 0, 1, .3], "cause": "ocr_error"})

    def test_partial_row_invalidates_entire_snapshot(self):
        vision = Mock()
        vision.read_lines.return_value = [
            SimpleNamespace(text="4-8 concluída · 0:43.37 · +1,362 · +3,055 EXP"),
            SimpleNamespace(text="4-8 concluída · 0:43.37 · +1,362"),
        ]
        self.assertIsNone(read_results(vision, object(), {"logs_roi": [0, 0, 1, 1]}))

    def test_five_equal_visible_rows_remain_five_occurrences(self):
        vision = Mock()
        vision.read_lines.return_value = [
            SimpleNamespace(text="4-8 concluída · 0:43.37 · +1,362 · +3,055 EXP")
        ] * 5
        self.assertEqual(len(read_results(vision, object(), {"logs_roi": [0, 0, 1, 1]})), 5)

    def test_mixed_log_keeps_all_five_rows_in_their_visible_order(self):
        vision = Mock()
        texts = ["Caixa obtida", "Caixa de Chefe obtida",
                 "4-8 concluída · 0:43.37 · +1,362 · +3,055 EXP",
                 "Caixa de Chefe aberta", "Você obteve Escudo do Guardião x1"]
        vision.read_lines.return_value = [SimpleNamespace(text=text) for text in texts]
        entries = read_results(vision, object(), {"logs_roi": [0, 0, 1, 1]})
        self.assertEqual([entry.raw for entry in entries], texts)
        self.assertEqual(sum(isinstance(entry, ParsedBattle) for entry in entries), 1)
        self.assertEqual(sum(isinstance(entry, LogMarker) for entry in entries), 4)

    def test_incomplete_battle_with_known_loot_still_invalidates_snapshot(self):
        vision = Mock()
        vision.read_lines.return_value = [SimpleNamespace(text="Caixa obtida"),
                                         SimpleNamespace(text="4-8 concluída · 0:43.37 · +1,362")]
        self.assertIsNone(read_results(vision, object(), {"logs_roi": [0, 0, 1, 1]}))

    def test_configured_notification_pattern_extends_known_forms(self):
        vision = Mock()
        vision.read_lines.return_value = [SimpleNamespace(text="Missão diária completa")]
        cfg = {"logs_roi": [0, 0, 1, 1], "notification_patterns": [r"missao diaria completa"]}
        self.assertIsInstance(read_results(vision, object(), cfg)[0], LogMarker)

    def test_actual_live_ocr_snapshot_with_box_typo_and_hyphens_is_complete(self):
        texts = ["Caixa obtida", "Calxa de Chefe obtida",
                 "4-7 concluida - 0:43.10 - € +1,362 - +2.965 EXP",
                 "Caixa de Chefe aberta", "Você obteve Escudo do Guardião x1"]
        vision = Mock()
        vision.read_lines.return_value = [SimpleNamespace(text=text) for text in texts]
        entries = read_results(vision, object(), {"logs_roi": [0, 0, 1, 1]})
        self.assertEqual(len(entries), 5)
        self.assertIsInstance(entries[2], ParsedBattle)
        self.assertEqual((entries[2].gold, entries[2].exp), (1362, 2965))


class AnchoredResultReadTests(unittest.TestCase):
    def setUp(self):
        self.image = SimpleNamespace(shape=(1440, 2560, 3))
        self.vision = Mock()
        self.vision.read_lines.return_value = []
        self.target = {"template": "templates/menu.png", "threshold": 0.9, "scales": [1]}
        self.config = {"logs_roi": [0, 0, 1, 1], "logs_anchor": {
            "target": self.target, "reference_width": 52, "offset": [-589, -329, 576, 136],
        }}

    def test_menu_anchor_resolves_log_roi_in_captured_image_coordinates(self):
        self.vision.locate.return_value = SimpleNamespace(x=1581, y=1329, width=52)
        self.assertEqual(read_results(self.vision, self.image, self.config), [])
        self.vision.locate.assert_called_once_with(self.image, self.target)
        roi = self.vision.read_lines.call_args.kwargs["roi"]
        for actual, expected in zip(roi, [992 / 2560, 1000 / 1440, 576 / 2560, 136 / 1440]):
            self.assertAlmostEqual(actual, expected)

    def test_anchor_width_scales_offsets_and_region_after_internal_resize(self):
        self.vision.locate.return_value = SimpleNamespace(x=1700, y=1200, width=104)
        read_results(self.vision, self.image, self.config)
        roi = self.vision.read_lines.call_args.kwargs["roi"]
        for actual, expected in zip(roi, [522 / 2560, 542 / 1440, 1152 / 2560, 272 / 1440]):
            self.assertAlmostEqual(actual, expected)

    def test_missing_configured_anchor_never_falls_back_to_fixed_roi(self):
        self.vision.locate.return_value = None
        diagnostics = {"roi": [0, 0, 1, 1]}
        self.assertIsNone(read_results(self.vision, self.image, self.config, diagnostics))
        self.assertEqual(diagnostics, {"cause": "missing_anchor"})
        self.vision.read_lines.assert_not_called()

    def test_out_of_bounds_anchored_region_is_rejected(self):
        for match in (SimpleNamespace(x=100, y=100, width=52),
                      SimpleNamespace(x=2600, y=1329, width=52)):
            with self.subTest(match=match):
                self.vision.locate.return_value = match
                diagnostics = {"roi": [0, 0, 1, 1]}
                self.assertIsNone(read_results(self.vision, self.image, self.config, diagnostics))
                self.assertEqual(diagnostics, {"cause": "invalid_roi"})
        self.vision.read_lines.assert_not_called()

    def test_fixed_proportional_roi_remains_supported_without_anchor_config(self):
        config = {"logs_roi": [0.37, 0.07, 0.55, 0.33]}
        read_results(self.vision, self.image, config)
        self.vision.locate.assert_not_called()
        self.assertEqual(self.vision.read_lines.call_args.kwargs["roi"], config["logs_roi"])


class LogDiagnosticTests(unittest.TestCase):
    def test_saved_png_contains_only_the_supplied_log_crop(self):
        import cv2
        import numpy as np

        image = np.arange(20 * 40 * 3, dtype=np.uint8).reshape((20, 40, 3))
        diagnostics = {"roi": [.25, .1, .5, .3], "cause": "rejected_rows", "line_count": 2,
                       "rejected_lines": [{"line": 1, "reason": "empty_or_unconfirmed"}],
                       "unrelated_raw": "must not persist"}
        with tempfile.TemporaryDirectory() as directory:
            path = save_log_diagnostic(SimpleNamespace(image=image), configuration(directory), INITIAL, "run", diagnostics)
            self.assertIsNotNone(path)
            actual = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(actual, image[2:8, 10:30])
            metadata_text = path.with_suffix(".json").read_text(encoding="utf-8")
            metadata = json.loads(metadata_text)
            self.assertEqual(metadata["crop_box"], [10, 2, 20, 6])
            self.assertEqual(metadata["stage"], INITIAL.stage)
            self.assertNotIn("must not persist", metadata_text)
            self.assertEqual(len(list(path.parent.glob("*.png"))), 1)

    def test_missing_or_invalid_roi_never_falls_back_to_full_frame(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as directory, patch("farmbot.desktop.save_frame") as save:
            frame = SimpleNamespace(image=np.zeros((20, 30, 3), dtype=np.uint8))
            for diagnostics in ({"cause": "missing_anchor"}, {"roi": [.9, 0, .5, .3]}):
                with self.subTest(diagnostics=diagnostics):
                    self.assertIsNone(save_log_diagnostic(frame, configuration(directory), INITIAL, "run", diagnostics))
            save.assert_not_called()


class SearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = configuration(self.directory.name)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.window = Mock()
        self.window.capture.return_value = SimpleNamespace(image=object())
        self.navigator = Mock()
        self.navigator.current_stage.return_value = INITIAL
        self.navigator.discover.return_value = [INITIAL, NEIGHBOR]
        self.inputs = SimpleNamespace(dry_run=False)

    def run_search(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_search(self.window, object(), self.navigator, self.inputs,
                              Clock(), self.config, self.store)

    def test_effective_input_dry_run_overrides_config_false_without_measurements(self):
        self.inputs.dry_run = True
        with patch("farmbot.runner.read_results", return_value=[row(1)] * 5), \
                patch("farmbot.runner.Collector") as collector:
            self.assertEqual(self.run_search(), [])
        collector.assert_not_called()
        self.navigator.preflight.assert_not_called()
        self.navigator.discover.assert_not_called()
        self.navigator.select.assert_not_called()
        self.navigator.execute_steps.assert_called_once_with(self.config["navigation"]["open_world"][:1])
        self.assertEqual(self.store.records(), [])

    def test_recent_cache_is_ranked_without_collecting_and_initial_is_restored(self):
        add_measurements(self.store, INITIAL, 100)
        add_measurements(self.store, NEIGHBOR, 200)
        # A very profitable historical stage outside the confirmed catalog must
        # not be offered as the winner or selected.
        add_measurements(self.store, Stage("9-9", "Pesadelo"), 100000)
        with patch("farmbot.runner.Collector") as collector:
            ranking = self.run_search()
        collector.return_value.collect.assert_not_called()
        self.assertEqual([item.target for item in ranking], [NEIGHBOR, INITIAL])
        self.navigator.select.assert_called_once_with(INITIAL)
        self.navigator.ensure_farming.assert_not_called()
        self.assertTrue((Path(self.directory.name) / "ranking.csv").is_file())

    def test_icon_map_prepares_closed_world_before_initial_measurement(self):
        self.config["navigation"]["mode"] = "icon_map"
        self.navigator.prepare_battle.return_value = INITIAL
        add_measurements(self.store, INITIAL, 100)
        add_measurements(self.store, NEIGHBOR, 200)
        with patch("farmbot.runner.Collector"):
            self.run_search()
        self.assertEqual([call[0] for call in self.navigator.mock_calls[:2]],
                         ["preflight", "prepare_battle"])
        self.navigator.current_stage.assert_not_called()

    def test_icon_map_dry_run_does_not_prepare_by_clicking(self):
        self.config["navigation"]["mode"] = "icon_map"
        self.inputs.dry_run = True
        with patch("farmbot.runner.read_results", return_value=[row(1)]):
            self.run_search()
        self.navigator.prepare_battle.assert_not_called()
        self.navigator.current_stage.assert_called_once()

    def test_enabled_final_switch_selects_reliable_best_and_confirms_repeat(self):
        self.config["auto_switch_best"] = True
        add_measurements(self.store, INITIAL, 100)
        add_measurements(self.store, NEIGHBOR, 200)
        with patch("farmbot.runner.Collector"):
            self.run_search()
        self.navigator.select.assert_called_once_with(NEIGHBOR)
        self.navigator.ensure_farming.assert_called_once_with(NEIGHBOR)

    def test_history_skip_preserves_budget_for_new_stage_then_returns_to_best(self):
        self.config["auto_switch_best"] = True
        self.config["search"].update(early_stop_gold_per_minute=3, max_stages=1)
        fresh = Stage("4-6", "Pesadelo")
        self.navigator.discover.return_value = [INITIAL, NEIGHBOR, fresh]
        add_measurements(self.store, INITIAL, 50, count=1)
        add_measurements(self.store, NEIGHBOR, 200)
        add_measurements(self.store, Stage("9-9", "Pesadelo"), 100000)
        original_collect = Collector.collect

        def measure(collector, target):
            self.navigator.discover.assert_called_once()
            self.assertEqual(set(collector.comparison_catalog), {INITIAL, NEIGHBOR, fresh})
            if target == INITIAL:
                return original_collect(collector, target)
            self.assertEqual(target, fresh)
            add_measurements(self.store, target, 100)
            return 2

        with patch.object(Collector, "collect", autospec=True, side_effect=measure) as collect:
            ranking = self.run_search()
        self.assertEqual([call.args[1] for call in collect.call_args_list], [INITIAL, fresh])
        self.assertEqual([call.args[0] for call in self.navigator.select.call_args_list], [fresh, NEIGHBOR])
        self.assertEqual(ranking[0].target, NEIGHBOR)
        self.assertEqual(len(self.store.records(INITIAL)), 1)
        self.window.capture.assert_not_called()

    def test_stale_cache_is_remeasured_and_bounded_by_new_stage_budget(self):
        self.config["search"]["max_stages"] = 1
        add_measurements(self.store, INITIAL, 10000, age_hours=8)

        def measure(target):
            add_measurements(self.store, target, 100)
            return 2

        with patch("farmbot.runner.Collector") as collector:
            collector.return_value.collect.side_effect = measure
            ranking = self.run_search()
        collector.return_value.collect.assert_called_once_with(INITIAL)
        self.assertEqual(len(ranking), 1)
        self.assertEqual(ranking[0].total_gold, 200)
        self.navigator.select.assert_called_once_with(INITIAL)

    def test_confirmed_failure_retreats_and_collects_lower_stage(self):
        def measure(collector, target):
            self.assertTrue(collector.stop_on_defeat)
            if target == INITIAL:
                raise StageDefeated(target, 0, NEIGHBOR, "4-8 falhou")
            add_measurements(self.store, target, 100)
            return 2

        with patch.object(Collector, "collect", autospec=True, side_effect=measure) as collect:
            ranking = self.run_search()
        self.assertEqual([call.args[1] for call in collect.call_args_list], [INITIAL, NEIGHBOR])
        self.assertEqual([call.args[0] for call in self.navigator.select.call_args_list], [NEIGHBOR, NEIGHBOR])
        self.assertEqual([item.target for item in ranking], [NEIGHBOR])

    def test_failures_descend_monotonically_and_do_not_exceed_collection_budget(self):
        lower = Stage("4-6", "Pesadelo")
        self.navigator.discover.return_value = [INITIAL, NEIGHBOR, lower]

        def measure(collector, target):
            observed = NEIGHBOR if target == INITIAL else lower
            raise StageDefeated(target, 0, observed, f"{target.stage} falhou")

        with patch.object(Collector, "collect", autospec=True, side_effect=measure) as collect:
            self.run_search()
        self.assertEqual([call.args[1] for call in collect.call_args_list], [INITIAL, NEIGHBOR])
        self.assertEqual([call.args[0] for call in self.navigator.select.call_args_list],
                         [NEIGHBOR, lower, lower])

    def test_failure_without_lower_stage_does_not_switch_difficulty(self):
        self.navigator.discover.return_value = [INITIAL, Stage("4-7", "Normal")]
        with patch.object(Collector, "collect", side_effect=StageDefeated(INITIAL, 0, INITIAL, "4-8 falhou")):
            with self.assertRaisesRegex(RuntimeError, "nenhuma fase inferior confirmada"):
                self.run_search()
        self.navigator.select.assert_not_called()

    def test_failed_stage_cannot_be_final_winner_even_if_old_history_was_profitable(self):
        self.config["auto_switch_best"] = True

        def measure(collector, target):
            if target == INITIAL:
                add_measurements(self.store, target, 10000)
                raise StageDefeated(target, 2, NEIGHBOR, "4-8 falhou")
            add_measurements(self.store, target, 100)
            return 2

        with patch.object(Collector, "collect", autospec=True, side_effect=measure):
            ranking = self.run_search()
        self.assertEqual([item.target for item in ranking], [NEIGHBOR])
        self.assertTrue(all(call.args[0] != INITIAL for call in self.navigator.select.call_args_list))
        self.assertEqual(len(self.store.records(INITIAL)), 2)

    def test_unexplained_navigation_error_still_aborts_without_retreat(self):
        from farmbot.navigation import NavigationError
        with patch.object(Collector, "collect", side_effect=NavigationError("unconfirmed change")):
            with self.assertRaisesRegex(NavigationError, "unconfirmed change"):
                self.run_search()
        self.navigator.select.assert_not_called()

    def test_recent_confirmed_failure_is_reused_to_retreat_without_retrying_failed_stage(self):
        self.config["search"]["max_stages"] = 1
        self.store.add(BattleRecord("failed", "previous", INITIAL.stage, INITIAL.difficulty,
                                   12, 0, 0, "interrupted", datetime.now(timezone.utc).isoformat(),
                                   "StageDefeated: Derrota confirmada sem duração no log: 4-8 falhou"))

        def measure(collector, target):
            self.assertEqual(target, NEIGHBOR)
            self.assertNotIn(INITIAL, collector.comparison_catalog)
            add_measurements(self.store, target, 100)
            return 2

        with patch.object(Collector, "collect", autospec=True, side_effect=measure) as collect:
            self.run_search()
        self.assertEqual(collect.call_count, 1)
        self.assertTrue(all(call.args[0] == NEIGHBOR for call in self.navigator.select.call_args_list))

    def test_no_reliable_winner_restores_initial_even_when_auto_switch_enabled(self):
        self.config["auto_switch_best"] = True

        def fail_measurement(target):
            add_measurements(self.store, target, 0, outcome="defeat")
            return 2

        with patch("farmbot.runner.Collector") as collector:
            collector.return_value.collect.side_effect = fail_measurement
            ranking = self.run_search()
        self.assertTrue(all(not item.reliable for item in ranking))
        self.assertEqual(self.navigator.select.call_args.args, (INITIAL,))
        self.navigator.ensure_farming.assert_not_called()

    def test_calibrated_repeat_is_confirmed_before_each_batch(self):
        self.config["navigation"]["repeat"] = {"active": {"text": "Repetir ativo"}}

        def measure(target):
            self.assertEqual(self.navigator.ensure_farming.call_args.args, (target,))
            add_measurements(self.store, target, 100)
            return 2

        with patch("farmbot.runner.Collector") as collector:
            collector.return_value.collect.side_effect = measure
            self.run_search()
        self.assertEqual(collector.return_value.collect.call_count, 2)
        self.assertEqual(self.navigator.ensure_farming.call_count, 2)
        self.assertEqual(self.navigator.select.call_args.args, (INITIAL,))


if __name__ == "__main__":
    unittest.main()
