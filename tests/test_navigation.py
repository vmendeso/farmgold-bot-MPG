from __future__ import annotations

import copy
import re
import unittest
from types import SimpleNamespace

from farmbot.models import Stage
from farmbot.navigation import CalibrationError, DryRunComplete, NavigationError, Navigator
from farmbot.vision import Match, OCRLine


class FakeStop:
    def check(self):
        pass

    def wait(self, seconds):
        pass


class FakeWindow:
    def __init__(self):
        self.state = "battle8"
        self.captures = 0

    def capture(self):
        self.captures += 1
        return SimpleNamespace(image=SimpleNamespace(shape=(1000, 1000, 3), state=self.state))


class FakeVision:
    def __init__(self):
        self.elements = {
            "battle8": [("menu", (900, 900, 40, 40))],
            "menu": [("menu_screen", (0, 0, 10, 10)), ("world_link", (700, 700, 80, 30))],
            "map": [
                ("map_ready", (0, 0, 10, 10)), ("return", (900, 900, 40, 40)),
                ("open", (180, 100, 20, 20)), ("open", (180, 200, 20, 20)),
                ("locked", (200, 200, 20, 20)),
            ],
            "detail": [("detail_ready", (0, 0, 10, 10)), ("start", (500, 500, 40, 40))],
            "battle7": [("battle_ready", (0, 0, 10, 10)), ("repeat_off", (900, 800, 20, 20))],
            "farming7": [("repeat_on", (900, 800, 20, 20))],
        }
        self.lines = {
            "battle8": [OCRLine("4-8 Túmulo dos Heróis · Pesadelo", 0.99, (100, 800, 400, 30))],
            "battle7": [OCRLine("4-7 Túmulo dos Heróis · Pesadelo", 0.99, (100, 800, 400, 30))],
            "farming7": [OCRLine("4-7 Túmulo dos Heróis · Pesadelo", 0.99, (100, 800, 400, 30))],
            "map": [
                OCRLine("4-7", 0.99, (100, 100, 40, 20)),
                OCRLine("4-9", 0.99, (100, 200, 40, 20)),
                OCRLine("4-10", 0.99, (100, 300, 40, 20)),
            ],
        }
        self.calls = []

    def locate(self, image, target):
        self.calls.append((image.state, target))
        for label, box in self.elements.get(image.state, []):
            if not re.search(target.get("text", "(?!)"), label):
                continue
            x, y, w, h = box
            if "roi" in target:
                rx, ry, rw, rh = target["roi"]
                if not (rx <= (x + w / 2) / 1000 <= rx + rw and ry <= (y + h / 2) / 1000 <= ry + rh):
                    continue
            return Match(x, y, w, h, 1.0, label)
        return None

    def read_lines(self, image, roi=None, mode="text"):
        return self.lines.get(image.state, [])


class FakeInputs:
    def __init__(self, window):
        self.window = window
        self.dry_run = False
        self.clicks = []
        self.transitions = {
            ("battle8", "menu"): "menu", ("menu", "world_link"): "map",
            ("map", "return"): "battle8", ("map", "4-7"): "detail",
            ("detail", "start"): "battle7", ("battle7", "repeat_off"): "farming7",
        }

    def click(self, frame, match):
        self.clicks.append((self.window.state, match.label))
        if self.dry_run:
            return False
        self.window.state = self.transitions.get((self.window.state, match.label), self.window.state)
        return True


def config():
    return {
        "calibrated": True,
        "timeout": 0,
        "poll_interval": 0.01,
        "retries": 2,
        "difficulty_names": ["Normal", "Pesadelo"],
        "current_stage_roi": [0, 0.75, 1, 0.25],
        "open_world": [
            {"click": {"text": "^menu$"}, "expect": {"text": "^menu_screen$"}},
            {"click": {"text": "^world_link$"}, "expect": {"text": "^map_ready$"}},
        ],
        "return_to_battle": [{"click": {"text": "^return$"}, "expect": {"text": "^menu$"}}],
        "pages": [{
            "name": "act4-nightmare", "difficulty": "Pesadelo", "act": 4,
            "enter": [], "ready": {"text": "^map_ready$"},
            "stages_roi": [0, 0.05, 0.9, 0.6], "card_padding": [0.02, 0.02, 0.12, 0.02],
            "unlocked": {"text": "^open$"}, "locked": {"text": "^locked$"},
            "select_expect": {"text": "^detail_ready$"},
            "select_confirm": [{"click": {"text": "^start$"}, "expect": {"text": "^battle_ready$"}}],
        }],
    }


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.window = FakeWindow()
        self.vision = FakeVision()
        self.inputs = FakeInputs(self.window)
        self.config = config()
        self.navigator = Navigator(self.window, self.vision, self.inputs, FakeStop(), self.config)

    def test_current_stage_requires_both_id_and_known_difficulty(self):
        self.assertEqual(self.navigator.current_stage(), Stage("4-8", "Pesadelo"))
        self.vision.lines["battle8"] = [OCRLine("4-8 Túmulo dos Heróis", 0.99, (100, 800, 400, 30))]
        self.assertIsNone(self.navigator.current_stage())

    def test_current_stage_accepts_case_accents_but_rejects_ambiguity(self):
        self.config["difficulty_names"] = ["Difícil", "Pesadelo"]
        self.vision.lines["battle8"] = [OCRLine("4 – 8 DIFICIL", 0.99, (100, 800, 400, 30))]
        self.assertEqual(self.navigator.current_stage(), Stage("4-8", "Difícil"))
        self.vision.lines["battle8"][0] = OCRLine("4-8 Difícil 4-9", 0.99, (100, 800, 400, 30))
        self.assertIsNone(self.navigator.current_stage())

    def test_discovery_requires_positive_unlock_and_lock_overrides(self):
        self.assertEqual(self.navigator.discover(), [Stage("4-7", "Pesadelo")])
        self.assertEqual(self.window.state, "battle8")
        self.assertEqual(self.inputs.clicks, [("battle8", "menu"), ("menu", "world_link"), ("map", "return")])

    def test_discovery_rejects_unconfigured_navigation_before_clicking(self):
        self.config["calibrated"] = False
        with self.assertRaises(CalibrationError):
            self.navigator.discover()
        self.assertEqual(self.inputs.clicks, [])

    def test_selection_confirms_details_and_exact_battle_label(self):
        self.navigator.select(Stage("4-7", "Pesadelo"))
        self.assertEqual(self.window.state, "battle7")
        self.assertEqual(self.inputs.clicks[-2:], [("map", "4-7"), ("detail", "start")])

    def test_selection_rechecks_unlock_after_discovery(self):
        self.navigator.discover()
        self.vision.elements["map"].append(("locked", (200, 100, 20, 20)))
        with self.assertRaisesRegex(NavigationError, "not positively unlocked"):
            self.navigator.select(Stage("4-7", "Pesadelo"))
        self.assertNotIn(("map", "4-7"), self.inputs.clicks)

    def test_selection_fails_when_actual_difficulty_differs(self):
        self.vision.lines["battle7"] = [OCRLine("4-7 Normal", 0.99, (100, 800, 400, 30))]
        with self.assertRaisesRegex(NavigationError, "Battlefield label"):
            self.navigator.select(Stage("4-7", "Pesadelo"))

    def test_dry_run_stops_at_first_identified_click(self):
        self.inputs.dry_run = True
        with self.assertRaises(DryRunComplete):
            self.navigator.discover()
        self.assertEqual(self.window.state, "battle8")
        self.assertEqual(self.inputs.clicks, [("battle8", "menu")])
        self.assertFalse(any(target.get("text") == "^menu_screen$" for _, target in self.vision.calls))

    def test_missing_expected_screen_never_reclicks(self):
        self.inputs.transitions[("battle8", "menu")] = "unknown"
        with self.assertRaises(NavigationError):
            self.navigator.execute_steps(self.config["open_world"])
        self.assertEqual(self.inputs.clicks, [("battle8", "menu")])

    def test_preclick_retries_are_bounded_and_never_click_missing_target(self):
        with self.assertRaises(NavigationError):
            self.navigator.execute_steps([{"click": {"text": "missing"}, "expect": {"text": "screen"}}])
        self.assertEqual(len(self.vision.calls), 3)
        self.assertEqual(self.inputs.clicks, [])

    def test_overlapping_card_regions_rejected_before_any_stage_click(self):
        self.config["pages"][0]["card_padding"] = [0.02, 0.02, 0.12, 0.25]
        with self.assertRaisesRegex(CalibrationError, "tighten"):
            self.navigator.discover()
        self.assertNotIn(("map", "4-7"), self.inputs.clicks)

    def test_low_confidence_stage_never_considered_unlocked(self):
        self.vision.lines["map"][0] = OCRLine("4-7", 0.1, (100, 100, 40, 20))
        self.assertEqual(self.navigator.discover(), [])

    def test_unavailable_page_skips_scan_and_returns_to_battle(self):
        self.config["pages"][0]["available"] = {"text": "missing_difficulty"}
        self.assertEqual(self.navigator.discover(), [])
        self.assertEqual(self.window.state, "battle8")

    def test_stage_selection_needs_confirmation_reference_before_navigation(self):
        del self.config["pages"][0]["select_expect"]
        with self.assertRaises(CalibrationError):
            self.navigator.select(Stage("4-7", "Pesadelo"))
        self.assertEqual(self.inputs.clicks, [])

    def test_empty_visual_reference_is_rejected_before_clicking(self):
        self.config["open_world"][0]["click"] = {"text": ".*"}
        with self.assertRaisesRegex(CalibrationError, "empty"):
            self.navigator.discover()
        self.assertEqual(self.inputs.clicks, [])

    def test_selection_only_checks_unlock_markers_for_requested_card(self):
        self.navigator.select(Stage("4-7", "Pesadelo"))
        marker_checks = [
            target for _, target in self.vision.calls
            if target.get("text") in ("^open$", "^locked$")
        ]
        self.assertEqual(len(marker_checks), 2)
        self.assertFalse(marker_checks[0]["strict"])

    def test_full_preflight_requires_selection_references_on_later_pages(self):
        page = copy.deepcopy(self.config["pages"][0])
        page["name"] = "act5-nightmare"
        page["act"] = 5
        del page["select_expect"]
        self.config["pages"].append(page)
        with self.assertRaises(CalibrationError):
            self.navigator.preflight()
        self.assertEqual(self.inputs.clicks, [])
        self.assertEqual(self.window.captures, 0)

    def test_discovery_preflight_allows_missing_selection_references(self):
        del self.config["pages"][0]["select_expect"]
        self.navigator.preflight(selection=False)
        self.assertEqual(self.window.captures, 0)

    def test_missing_template_in_later_step_prevents_first_click(self):
        self.config["open_world"][1]["expect"] = {"template": "nonexistent-reference-image-for-navigation-test.png"}
        with self.assertRaisesRegex(CalibrationError, "Template file not found"):
            self.navigator.execute_steps(self.config["open_world"])
        self.assertEqual(self.inputs.clicks, [])
        self.assertEqual(self.window.captures, 0)

    def test_repeat_uses_distinct_off_marker_and_confirms_on(self):
        self.window.state = "battle7"
        self.config["repeat"] = {
            "active": {"text": "^repeat_on$"},
            "enable": {"click": {"text": "^repeat_off$"}, "expect": {"text": "^repeat_on$"}},
        }
        self.assertTrue(self.navigator.ensure_farming(Stage("4-7", "Pesadelo")))
        self.assertTrue(self.navigator.ensure_farming(Stage("4-7", "Pesadelo")))
        self.assertEqual(self.inputs.clicks, [("battle7", "repeat_off")])

    def test_repeat_does_not_toggle_when_state_cannot_be_proven(self):
        self.window.state = "battle7"
        self.config["repeat"] = {
            "active": {"text": "^repeat_on$"},
            "enable": {"click": {"text": "^explicit_off_marker$"}, "expect": {"text": "^repeat_on$"}},
        }
        with self.assertRaises(NavigationError):
            self.navigator.ensure_farming(Stage("4-7", "Pesadelo"))
        self.assertEqual(self.inputs.clicks, [])

    def test_detector_roi_is_intersected_with_each_card(self):
        target = {"text": "open", "roi": [0.1, 0.1, 0.2, 0.2]}
        original = copy.deepcopy(target)
        result = self.navigator._card_target(target, (0.2, 0.2, 0.2, 0.2))
        self.assertEqual(target, original)
        for actual, expected in zip(result["roi"], [0.2, 0.2, 0.1, 0.1]):
            self.assertAlmostEqual(actual, expected)
        self.assertIsNone(self.navigator._card_target(target, (0.8, 0.8, 0.1, 0.1)))


if __name__ == "__main__":
    unittest.main()
