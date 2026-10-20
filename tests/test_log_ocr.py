"""Regression tests for confirmed numerical OCR and occurrence-preserving reuse."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from farmbot.models import ParsedBattle, UntimedDefeat
from farmbot.results import parse_log_entry
from farmbot.vision import OCRLine, Vision, VisionError


@unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "Needs image dependencies")
class LogOCRTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.vision = Vision({"logs_linewise": True, "ocr_scale": 2, "ocr_timeout": 6}, Path("."))
        self.image = np.zeros((60, 200, 3), dtype=np.uint8)
        self.image[5:15, 10:130] = (0, 200, 0)
        self.image[35:45, 10:130] = (0, 200, 0)

    @staticmethod
    def line(text):
        return [OCRLine(text, .95, (0, 0, 120, 10))]

    def test_recovers_decimal_from_two_visual_reads_without_text_repair(self):
        wrong = "4-3 concluída · 0:4145 · +1.324 · +2.555 EXP"
        good = "4-3 concluída · 0:41.45 · +1.324 · +2.555 EXP"
        with patch.object(self.vision, "_read_processed", side_effect=[self.line(wrong), self.line(good), self.line(good)]) as reader:
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertEqual(reader.call_count, 3)
        self.assertEqual([parse_log_entry(row.text).duration for row in rows], [41.45, 41.45])
        self.assertEqual([row.box[1] for row in rows], [5, 35])

    def test_untimed_defeat_requires_two_matching_reads_and_preserves_occurrences(self):
        with patch.object(self.vision, "_read_processed", return_value=self.line("1-8 falhou")) as reader:
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(isinstance(parse_log_entry(row.text), UntimedDefeat) for row in rows))
        self.assertEqual([row.box[1] for row in rows], [5, 35])

    def test_unconfirmed_untimed_defeat_is_not_accepted_as_notification(self):
        outputs = [self.line("1-8 falhou"), *[self.line("1-8 falh") for _ in range(6)]]
        with patch.object(self.vision, "_read_processed", side_effect=outputs * 2):
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertTrue(all(parse_log_entry(row.text) is None for row in rows))

    def test_valid_but_conflicting_numbers_do_not_become_battles(self):
        outputs = [self.line(f"4-3 concluída · 0:41.{value} · +1.324 · +2.555 EXP") for value in (45, 46, 47, 48, 49, 50, 51)]
        with patch.object(self.vision, "_read_processed", side_effect=outputs * 2):
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(parse_log_entry(row.text) is None for row in rows))

    def test_two_agreeing_reads_outvote_one_different_gold_read(self):
        wrong = self.line("4-3 concluída · 0:41.45 · +1.824 · +2.555 EXP")
        good = self.line("4-3 concluída · 0:41.45 · +1.324 · +2.555 EXP")
        with patch.object(self.vision, "_read_processed", side_effect=[wrong, good, good]):
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertEqual([parse_log_entry(row.text).gold for row in rows], [1324, 1324])

    def test_final_variant_confirms_decimal_without_accepting_wrong_gold(self):
        invalid = self.line("4-7 concluída - 0:4246 - +1.645 - +2.965 EXP")
        good = self.line("4-7 concluída - 0:42.46 - +1.645 - +2.965 EXP")
        wrong = self.line("4-7 concluída - 0:42.46 - +1.646 - +2.965 EXP")
        with patch.object(self.vision, "_read_processed", side_effect=[invalid, invalid, good, wrong, invalid, good]) as reader:
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertEqual(reader.call_count, 6)
        self.assertEqual([(parse_log_entry(row.text).duration, parse_log_entry(row.text).gold)
                          for row in rows], [(42.46, 1645), (42.46, 1645)])
        self.assertEqual([row.box[1] for row in rows], [5, 35])

    def test_last_grayscale_variant_confirms_separator_without_parser_repair(self):
        invalid = self.line("2-7 concluida * 1:13.71 + +2,123 - +5.290 EXP")
        first_good = self.line("2-7 concluida «1:13.71: +2,123 : +5,290 EXP")
        second_good = self.line("2-7 concluida - 1:13.71 - +2.123 - +5.290 EXP")
        self.assertIsNone(parse_log_entry(invalid[0].text))
        with patch.object(self.vision, "_read_processed", side_effect=[invalid] * 5 + [first_good, second_good]) as reader:
            rows = self.vision.read_lines(self.image, mode="logs")
        self.assertEqual(reader.call_count, 7)
        self.assertEqual([parse_log_entry(row.text).fingerprint for row in rows],
                         [("2-7", 73.71, 2123, 5290, "victory")] * 2)
        self.assertEqual([row.box[1] for row in rows], [5, 35])

    def test_exact_pixels_reuse_text_without_removing_occurrences_or_positions(self):
        good = self.line("4-3 concluída · 0:41.45 · +1.324 · +2.555 EXP")
        with patch.object(self.vision, "_read_processed", return_value=good) as reader:
            first = self.vision.read_lines(self.image, mode="logs")
            shifted = self.vision.read_lines(self.np.roll(self.image, 2, axis=0), mode="logs")
            blank = self.vision.read_lines(self.np.zeros_like(self.image), mode="logs")
        self.assertEqual(reader.call_count, 2)
        self.assertEqual([row.text for row in first], [row.text for row in shifted])
        self.assertEqual([row.box[1] for row in shifted], [7, 37])
        self.assertEqual(blank, [])

    def test_changed_pixels_or_settings_are_recognized_again(self):
        good = self.line("4-3 concluída · 0:41.45 · +1.324 · +2.555 EXP")
        with patch.object(self.vision, "_read_processed", return_value=good) as reader:
            self.vision.read_lines(self.image, mode="logs")
            self.image[7, 12] = 0
            self.vision.read_lines(self.image, mode="logs")
            self.assertEqual(reader.call_count, 4)
            self.vision.config["language"] = "por"
            self.vision.read_lines(self.image, mode="logs")
            self.assertEqual(reader.call_count, 8)

    def test_failed_reads_are_not_cached(self):
        with patch.object(self.vision, "_read_processed", return_value=self.line("0:4145")):
            failed = self.vision.read_lines(self.image, mode="logs")
        good = self.line("4-3 concluída · 0:41.45 · +1.324 · +2.555 EXP")
        with patch.object(self.vision, "_read_processed", return_value=good) as reader:
            recovered = self.vision.read_lines(self.image, mode="logs")
        self.assertTrue(all(parse_log_entry(row.text) is None for row in failed))
        self.assertTrue(all(parse_log_entry(row.text) is not None for row in recovered))
        self.assertEqual(reader.call_count, 2)

    def test_retries_share_one_deadline(self):
        with patch("farmbot.vision.time.monotonic", side_effect=[9, 10]), patch.object(
            self.vision, "_read_processed", return_value=self.line("0:4145")
        ) as reader, self.assertRaises(VisionError):
            self.vision._recognize_log_row(self.np.zeros((15, 120), dtype=self.np.uint8), 0, 0, 2, 10)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(reader.call_args.args[-1], 1)

    def test_late_variants_cannot_extend_the_shared_deadline(self):
        for deadline in (6, 7):
            with self.subTest(deadline=deadline), patch("farmbot.vision.time.monotonic", side_effect=range(1, deadline + 1)), patch.object(
                self.vision, "_read_processed", return_value=self.line("0:4246")
            ) as reader, self.assertRaises(VisionError):
                self.vision._recognize_log_row(self.np.zeros((10, 120), dtype=self.np.uint8), 0, 0, 2, deadline)
            self.assertEqual(reader.call_count, deadline - 1)
            self.assertEqual([call.args[-1] for call in reader.call_args_list], list(range(deadline - 1, 0, -1)))

    def real_vision(self):
        if importlib.util.find_spec("pytesseract") is None:
            self.skipTest("Needs pytesseract")
        local = Path(__file__).resolve().parents[1] / ".tools/tesseract/tesseract.exe"
        command = os.environ.get("TESSERACT_CMD") or (str(local) if local.is_file() else shutil.which("tesseract"))
        if not command:
            self.skipTest("Needs Tesseract")
        self.vision.config.update(tesseract_cmd=command, language="por+eng", min_confidence=0)

    def test_real_capture_that_stopped_collection_keeps_decimal_and_loot(self):
        import cv2
        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_missing_decimal.png"))
        rows = self.vision.read_lines(image, mode="logs")
        entries = [parse_log_entry(row.text) for row in rows]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries), [row.text for row in rows])
        battles = [entry for entry in entries if isinstance(entry, ParsedBattle)]
        self.assertEqual([(b.stage, b.duration, b.gold, b.exp) for b in battles],
                         [("4-3", 41.45, 1324, 2555), ("4-3", 40.70, 1324, 2555)])
        self.assertEqual(entries[-1].raw, "Você obteve Cajado da Bruxa x1")

    def test_real_defeat_without_duration_keeps_complete_log_readable(self):
        import cv2
        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_untimed_defeat.png"))
        entries = [parse_log_entry(row.text) for row in self.vision.read_lines(image, mode="logs")]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries))
        self.assertEqual(sum(isinstance(entry, ParsedBattle) for entry in entries), 4)
        self.assertIsInstance(entries[-1], UntimedDefeat)
        self.assertEqual(entries[-1].stage, "1-8")

    def test_current_smaller_font_reads_minutes_gold_and_exp_exactly(self):
        import cv2
        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_2_6_scaled.png"))
        rows = self.vision.read_lines(image, mode="logs")
        entries = [parse_log_entry(row.text) for row in rows]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries), [row.text for row in rows])
        battles = [entry for entry in entries if isinstance(entry, ParsedBattle)]
        self.assertEqual([(b.stage, b.duration, b.gold, b.exp) for b in battles],
                         [("2-6", 67.87, 1849, 5050), ("2-6", 69.17, 1849, 5050),
                          ("2-6", 70.23, 1849, 5050)])

    def test_previous_stage_decimal_does_not_block_current_stage_log(self):
        import cv2
        from farmbot.runner import read_results

        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_4_6_previous_stage_decimal.png"))
        entries = read_results(self.vision, image, {"logs_roi": [0, 0, 1, 1]})
        self.assertIsNotNone(entries)
        self.assertEqual([(entry.stage, entry.duration, entry.gold, entry.exp) for entry in entries],
                         [("4-7", 42.46, 1645, 2965), ("4-7", 42.70, 1645, 2965),
                          ("4-6", 41.53, 1600, 2790), ("4-6", 41.42, 1600, 2790),
                          ("4-6", 41.56, 1600, 2790)])
        # All five confirmed rows remain separate when the same pixels recur.
        with patch.object(self.vision, "_read_processed") as reader:
            repeated = read_results(self.vision, image, {"logs_roi": [0, 0, 1, 1]})
        self.assertEqual(repeated, entries)
        reader.assert_not_called()

    def test_monitor_capture_reads_previous_stage_reward_separator_and_all_five_rows(self):
        import cv2
        from farmbot.runner import read_results

        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_2_8_separator_consensus.png"))
        entries = read_results(self.vision, image, {"logs_roi": [0, 0, 1, 1]})
        self.assertIsNotNone(entries)
        self.assertEqual([(entry.stage, entry.duration, entry.gold, entry.exp) for entry in entries],
                         [("2-1", 65.29, 2023, 4440), ("2-5", 82.85, 2073, 4945),
                          ("2-6", 68.70, 2073, 5050), ("2-7", 73.71, 2123, 5290),
                          ("2-8", 71.96, 2123, 5400)])

    def test_new_four_six_captures_keep_decimal_after_row_shift_and_duplicate_duration(self):
        import cv2

        self.real_vision()
        for name, durations in (
            ("logs_4_6_before_row_shift.png", [41.09, 41.35, 41.45, 41.67, 42.03]),
            ("logs_4_6_after_row_shift.png", [41.35, 41.45, 41.67, 42.03, 41.45]),
        ):
            with self.subTest(fixture=name):
                image = cv2.imread(str(Path(__file__).parent / "fixtures" / name))
                rows = self.vision.read_lines(image, mode="logs")
                entries = [parse_log_entry(row.text) for row in rows]
                self.assertTrue(all(entries), [row.text for row in rows])
                self.assertEqual([(entry.stage, entry.duration, entry.gold, entry.exp) for entry in entries],
                                 [("4-6", duration, 1600, 2790) for duration in durations])
                self.assertEqual(len({row.box[1] for row in rows}), 5)
                self.assertEqual(len({id(row) for row in rows}), 5)

    def test_actual_12_24_failure_preserves_small_decimal_12_07(self):
        import cv2
        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_2_6_decimal_failure.png"))
        rows = self.vision.read_lines(image, mode="logs")
        entries = [parse_log_entry(row.text) for row in rows]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries), [row.text for row in rows])
        battles = [entry for entry in entries if isinstance(entry, ParsedBattle)]
        self.assertEqual([(b.stage, b.duration, b.gold, b.exp) for b in battles],
                         [("2-6", 71.30, 1849, 5050), ("2-6", 72.07, 1849, 5050),
                          ("2-6", 69.28, 1849, 5050)])

    def test_live_terminal_failure_handles_separator_noise_without_changing_numbers(self):
        import cv2
        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_2_6_separator_failure.png"))
        rows = self.vision.read_lines(image, mode="logs")
        entries = [parse_log_entry(row.text) for row in rows]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries), [row.text for row in rows])
        battles = [entry for entry in entries if isinstance(entry, ParsedBattle)]
        self.assertEqual([(b.stage, b.duration, b.gold, b.exp) for b in battles],
                         [("2-6", 70.62, 1849, 5050), ("2-6", 71.44, 1849, 5050)])

    def test_same_battle_shifted_to_another_row_preserves_decimal(self):
        import cv2
        self.real_vision()
        image = cv2.imread(str(Path(__file__).parent / "fixtures/logs_2_6_subpixel_failure.png"))
        entries = [parse_log_entry(row.text) for row in self.vision.read_lines(image, mode="logs")]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries))
        battles = [entry for entry in entries if isinstance(entry, ParsedBattle)]
        self.assertEqual([(b.stage, b.duration, b.gold, b.exp) for b in battles],
                         [("2-6", 70.62, 1849, 5050), ("2-6", 71.44, 1849, 5050),
                          ("2-6", 69.48, 1849, 5050), ("2-6", 68.57, 1849, 5050)])

    def test_real_row_shift_reaches_collector_database_and_restart_adjusted_rate(self):
        import cv2
        from farmbot.models import Stage
        from farmbot.runner import Collector, recent_stats
        from farmbot.storage import Store

        self.real_vision()
        target = Stage("4-6", "Pesadelo")
        images = [cv2.imread(str(Path(__file__).parent / "fixtures" / name)) for name in (
            "logs_4_6_before_row_shift.png", "logs_4_6_after_row_shift.png")]
        self.assertTrue(all(image is not None for image in images))
        # 41.45 appears in BOTH frames; the new occurrence must be persisted,
        # while the five historical rows must not be credited on startup.
        window = Mock()
        window.capture.side_effect = [SimpleNamespace(image=image.copy())
                                      for image in images for _ in range(2)]
        navigator = Mock(current_stage=Mock(return_value=target))
        now = [0.0]
        stop = Mock()
        stop.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "results.db") as store:
            config = {"_base_dir": Path(directory), "navigation": {},
                      "search": {"cache_hours": 6}, "output": {"artifacts": "artifacts"},
                      "measurement": {"battles_per_stage": 1, "min_win_rate": .8,
                                      "battle_restart_seconds": 5, "log_order": "oldest_first",
                                      "max_log_gap": 120, "poll_interval": 1,
                                      "battle_timeout": 180, "stage_timeout": 600,
                                      "logs_roi": [0, 0, 1, 1]}}
            clock = SimpleNamespace(monotonic=lambda: now[0], time_ns=time.time_ns)
            with patch("farmbot.runner.time", clock):
                completed = Collector(window, self.vision, navigator, stop, store, config).collect(target)
            self.assertEqual(completed, 1)
            records = store.records()
            self.assertEqual([(r.stage, r.duration, r.gold, r.exp, r.outcome) for r in records],
                             [("4-6", 41.45, 1600, 2790, "victory")])
            ranking = recent_stats(store, config)
            self.assertEqual(len(ranking), 1)
            self.assertAlmostEqual(ranking[0].total_seconds, 46.45)
            self.assertAlmostEqual(ranking[0].gold_per_minute, 1600 / 46.45 * 60)
        self.assertEqual(window.capture.call_count, 4)
