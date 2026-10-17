import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from farmbot.vision import Match, OCRLine, Vision, VisionError, _roi_box


class RecognitionContractsTests(unittest.TestCase):
    def test_preflight_checks_regex_and_schema_without_optional_libraries(self):
        vision = Vision({}, Path("."))
        vision.validate_target({"text": "^Mundo$", "roi": [0, 0, 1, .5]})
        for target in ({"text": "["}, {"text": "Mundo", "kind": "menu"}, {"kind": "menu", "threshold": 1.2}):
            with self.subTest(target=target), self.assertRaises(VisionError):
                vision.validate_target(target)

    def test_rejects_ambiguous_text_target(self):
        vision = Vision({}, Path("."))
        vision.read_lines = Mock(return_value=[OCRLine("Mundo", .95, (10, 10, 30, 10)), OCRLine("Mundo", .96, (10, 40, 30, 10))])
        self.assertIsNone(vision.locate(None, {"text": "^Mundo$"}))
        self.assertEqual(vision.locate(None, {"text": "^Mundo$", "strict": False}).y, 40)
        self.assertEqual(len(vision.locate_all(None, {"text": "^Mundo$"})), 2)

    def test_text_threshold_and_roi_forwarding(self):
        vision = Vision({}, Path("."))
        vision.read_lines = Mock(return_value=[OCRLine("Mundo", .5, (10, 10, 30, 10))])
        image = object()
        self.assertIsNone(vision.locate(image, {"text": "Mundo", "roi": [0, 0, 1, .5]}))
        vision.read_lines.assert_called_with(image, [0, 0, 1, .5])
        self.assertIsNotNone(vision.locate(image, {"text": "Mundo", "threshold": .4}))

    def test_roi_validation_and_pixels(self):
        image = types.SimpleNamespace(shape=(200, 400, 3))
        self.assertEqual(_roi_box(image, [.25, .25, .5, .5]), (100, 50, 200, 100))
        for roi in ([0, 0, 0, 1], [-.1, 0, 1, 1], [.9, .1, .2, .2], [0, 0, float("nan"), 1]):
            with self.subTest(roi=roi), self.assertRaises(VisionError):
                _roi_box(image, roi)


@unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "OpenCV/NumPy optional until requirements installed")
class PixelRecognitionTests(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.vision = Vision({"ocr_scale": 2, "min_confidence": .4}, self.base)

    def _real_tesseract(self):
        if importlib.util.find_spec("pytesseract") is None:
            self.skipTest("pytesseract unavailable")
        local = Path(__file__).resolve().parents[1] / ".tools/tesseract/tesseract.exe"
        command = os.environ.get("TESSERACT_CMD") or (str(local) if local.is_file() else shutil.which("tesseract"))
        if not command:
            self.skipTest("Tesseract executable unavailable")
        return command

    def test_multiscale_matching_and_constant_protection(self):
        np, cv2 = self.np, self.cv2
        template = np.random.default_rng(7).integers(0, 255, (20, 20), dtype=np.uint8)
        cv2.imwrite(str(self.base / "icon.png"), template)
        frame = np.zeros((150, 200), dtype=np.uint8)
        frame[40:70, 90:120] = cv2.resize(template, (30, 30), interpolation=cv2.INTER_CUBIC)
        match = self.vision.locate(frame, {"template": "icon.png", "scales": [1, 1.5], "threshold": .99})
        self.assertEqual((match.x, match.y, match.width, match.height), (90, 40, 30, 30))
        cv2.imwrite(str(self.base / "blank.png"), np.zeros((20, 20), dtype=np.uint8))
        with self.assertRaises(VisionError):
            self.vision.locate(frame, {"template": "blank.png"})

    def test_preflight_rejects_missing_empty_and_corrupt_templates(self):
        (self.base / "empty.png").write_bytes(b"")
        (self.base / "corrupt.png").write_bytes(b"not a png")
        for filename in ("missing.png", "empty.png", "corrupt.png"):
            with self.subTest(filename=filename), self.assertRaises(VisionError):
                self.vision.validate_target({"template": filename})

    def test_two_identical_templates_are_ambiguous(self):
        np, cv2 = self.np, self.cv2
        template = np.random.default_rng(10).integers(0, 255, (15, 15), dtype=np.uint8)
        cv2.imwrite(str(self.base / "icon.png"), template)
        frame = np.zeros((100, 100), dtype=np.uint8)
        frame[10:25, 10:25] = template
        frame[60:75, 60:75] = template
        target = {"template": "icon.png", "scales": [1], "threshold": .999}
        self.assertEqual(len(self.vision.locate_all(frame, target)), 2)
        self.assertIsNone(self.vision.locate(frame, target))

    def test_menu_requires_three_horizontal_bars(self):
        np, cv2 = self.np, self.cv2
        image = np.zeros((400, 1000, 3), dtype=np.uint8)
        cv2.rectangle(image, (930, 330), (980, 380), (30, 60, 100), -1)
        for yy in (343, 353, 363):
            cv2.rectangle(image, (943, yy), (967, yy + 4), (180, 200, 220), -1)
        match = self.vision.locate(image, {"kind": "menu"})
        self.assertIsNotNone(match)
        self.assertEqual(match.center, (955, 355))
        cv2.rectangle(image, (943, 363), (967, 367), (30, 60, 100), -1)
        self.assertIsNone(self.vision.locate(image, {"kind": "menu"}))

    def test_ocr_keeps_duplicate_rows_offsets_and_red_defeats(self):
        np = self.np
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        image[12:17, 52:70] = (0, 200, 0)
        image[22:27, 52:70] = (0, 0, 200)
        data = {"text": ["4-8", "4-8", "derrota"], "conf": [95, 94, 96], "block_num": [1, 1, 1], "par_num": [1, 1, 1], "line_num": [1, 2, 3], "left": [4, 4, 4], "top": [4, 24, 44], "width": [36, 36, 36], "height": [10, 10, 10]}
        engine = types.SimpleNamespace(Output=types.SimpleNamespace(DICT="dict"), image_to_data=Mock(return_value=data))
        with patch.object(self.vision, "_ocr", return_value=engine):
            rows = self.vision.read_lines(image, [.25, .1, .5, .8], mode="logs")
        self.assertEqual([row.text for row in rows], ["4-8", "4-8", "derrota"])
        self.assertEqual(rows[0].box, (52, 12, 18, 5))
        processed = engine.image_to_data.call_args.args[0]
        self.assertLess(processed[8, 10], 127)  # green foreground remains black
        self.assertLess(processed[28, 10], 127)  # red foreground also remains black
        self.assertEqual(engine.image_to_data.call_args.kwargs["timeout"], 5)

    def test_ocr_timeout_is_reported(self):
        engine = types.SimpleNamespace(Output=types.SimpleNamespace(DICT="dict"), image_to_data=Mock(side_effect=RuntimeError("Tesseract process timeout")))
        with patch.object(self.vision, "_ocr", return_value=engine), self.assertRaises(VisionError):
            self.vision.read_lines(self.np.zeros((20, 50, 3), dtype=self.np.uint8))

    def test_white_text_excludes_colored_and_dark_background(self):
        np = self.np
        image = np.zeros((30, 100, 3), dtype=np.uint8)
        image[5:10, 10:20] = (235, 235, 235)
        image[5:10, 30:40] = (255, 100, 0)
        image[5:10, 50:60] = (150, 150, 150)
        engine = types.SimpleNamespace(Output=types.SimpleNamespace(DICT="dict"), image_to_data=Mock(return_value={"text": []}))
        self.vision.config["white_text"] = True
        with patch.object(self.vision, "_ocr", return_value=engine):
            self.vision.read_lines(image)
        binary = engine.image_to_data.call_args.args[0]
        self.assertLess(binary[14, 28], 127)
        self.assertGreater(binary[14, 68], 127)
        self.assertGreater(binary[14, 108], 127)

    def test_real_white_footer_reads_4_7_without_digit_repair(self):
        command = self._real_tesseract()
        fixture = Path(__file__).parent / "fixtures/stage_4_7_animated_footer.png"
        image = self.cv2.imread(str(fixture))
        vision = Vision({"tesseract_cmd": command, "language": "eng", "ocr_scale": 2, "text_psm": 7, "min_confidence": 0, "white_text": True}, self.base)
        rows = vision.read_lines(image)
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0].text, r"\b4-7\b")
        self.assertIn("Pesadelo", rows[0].text)

    def test_real_linewise_logs_preserve_all_five_exact_battles(self):
        from farmbot.results import parse_battle
        command = self._real_tesseract()
        vision = Vision({"tesseract_cmd": command, "language": "por+eng", "ocr_scale": 2, "min_confidence": 0, "logs_linewise": True, "ocr_timeout": 6}, self.base)
        fixtures = {"logs_bad_digit_a.png": [43.09, 43.33, 43.69, 42.46, 43.48], "logs_bad_digit_b.png": [43.33, 43.69, 42.46, 43.48, 43.39]}
        for name, expected_times in fixtures.items():
            with self.subTest(fixture=name):
                image = self.cv2.imread(str(Path(__file__).parent / "fixtures" / name))
                lines = vision.read_lines(image, mode="logs")
                self.assertEqual(len(lines), 5)
                battles = [parse_battle(line.text) for line in lines]
                self.assertTrue(all(battles), [line.text for line in lines])
                self.assertEqual([battle.duration for battle in battles], expected_times)
                self.assertEqual([(battle.stage, battle.gold, battle.exp) for battle in battles], [("4-7", 1362, 2965)] * 5)
                self.assertEqual([line.box[1] for line in lines], sorted(line.box[1] for line in lines))

    def test_real_linewise_logs_keep_loot_rows_between_battles(self):
        from farmbot.models import ParsedBattle
        from farmbot.results import parse_log_entry
        command = self._real_tesseract()
        vision = Vision({"tesseract_cmd": command, "language": "por+eng", "ocr_scale": 2, "min_confidence": 0, "logs_linewise": True, "ocr_timeout": 6}, self.base)
        image = self.cv2.imread(str(Path(__file__).parent / "fixtures/logs_mixed_loot.png"))
        rows = vision.read_lines(image, mode="logs")
        entries = [parse_log_entry(row.text) for row in rows]
        self.assertEqual(len(entries), 5)
        self.assertTrue(all(entries), [row.text for row in rows])
        self.assertEqual([isinstance(entry, ParsedBattle) for entry in entries], [False, False, True, False, True])
        self.assertEqual([entry.duration for entry in entries if isinstance(entry, ParsedBattle)], [43.34, 43.56])

    def test_linewise_preserves_duplicate_and_unreadable_rows(self):
        np = self.np
        image = np.zeros((60, 100, 3), dtype=np.uint8)
        image[5:10, 10:30] = (0, 200, 0)
        image[25:30, 10:30] = (0, 0, 200)  # A red defeat must also remain.
        self.vision.config["logs_linewise"] = True
        duplicate = OCRLine("identical row", .9, (0, 0, 20, 5))
        with patch.object(self.vision, "_read_processed", return_value=[duplicate]):
            rows = self.vision.read_lines(image, mode="logs")
        self.assertEqual([row.text for row in rows], ["identical row", "identical row"])
        self.assertEqual([row.box[1] for row in rows], [5, 25])
        with patch.object(self.vision, "_read_processed", return_value=[]):
            rows = self.vision.read_lines(image, mode="logs")
        self.assertEqual([row.text for row in rows], ["", ""])


if __name__ == "__main__":
    unittest.main()
