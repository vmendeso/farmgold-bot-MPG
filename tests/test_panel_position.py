import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from farmbot.config import load_config
from farmbot.map_navigation import MapNavigator
from farmbot.navigation import NavigationError, CalibrationError
from farmbot.safety import StopController
from farmbot.vision import Vision


@unittest.skipUnless(importlib.util.find_spec("cv2"), "Requires OpenCV")
class PanelPositionTests(unittest.TestCase):
    def setUp(self):
        import cv2
        self.cv2 = cv2
        self.base = Path(__file__).resolve().parents[1]
        self.config = load_config(self.base / "config.json")
        self.vision = Vision(self.config["vision"], self.base)
        self.nav = MapNavigator(None, self.vision, None, StopController(key_check=lambda: False), self.config["navigation"])

    def page(self, name):
        return next(p for p in self.nav.config["pages"] if p["name"] == name)

    def frame(self, name):
        image = self.cv2.imread(str(self.base / "tests/fixtures" / name))
        menu = self.vision.locate(image, self.config["navigation"]["menu"])
        self.assertIsNotNone(menu)
        return SimpleNamespace(image=image), menu

    def test_actual_failed_inventory_screen_exposes_tab_above_battle(self):
        frame, menu = self.frame("inventory_above_battle.png")
        tab = self.nav._visible(frame, menu, self.nav.config["world_tab"])
        self.assertIsNotNone(tab)
        self.assertLess(tab.y, menu.y)
        self.assertIsNone(self.nav._visible(frame, menu, self.nav.config["world_ready"]))
        self.assertIsNone(self.nav._visible(frame, menu, self.nav.config["close_world"]))
        with self.assertRaises(NavigationError):
            self.nav._nodes(frame, menu)

    def test_native_scale_proves_only_the_selected_page(self):
        for fixture, selected in (("scale100_inferno2.png", "ato2_inferno"),
                                  ("scale100_nightmare4.png", "ato4_nightmare"),
                                  ("scale100_averno1.png", "ato1_averno")):
            frame, menu = self.frame(fixture)
            self.assertEqual(menu.width, 52)
            for page in self.nav._pages():
                with self.subTest(fixture=fixture, page=page["name"]):
                    ready = all(self.nav._visible(frame, menu, t) is not None for t in page["ready"])
                    self.assertEqual(ready, page["name"] == selected)
            self.assertIsNotNone(self.nav._visible(frame, menu, self.nav.config["close_world"]))
            self.assertTrue(self.nav._nodes(frame, menu))

    def test_native_scale_dropdown_is_distinct_from_selected_difficulty(self):
        for fixture in ("scale100_list_inferno2.png", "scale100_list_nightmare4.png"):
            frame, menu = self.frame(fixture)
            for page in self.nav._pages():
                with self.subTest(fixture=fixture, difficulty=page["difficulty"]):
                    self.assertIsNotNone(self.nav._visible(frame, menu, page["enter"][1]["click"]))
        for fixture, page_name in (("scale100_nightmare2.png", "ato4_nightmare"),
                                   ("scale100_averno4.png", "ato1_averno")):
            frame, menu = self.frame(fixture)
            page = self.page(page_name)
            self.assertIsNotNone(self.nav._visible(frame, menu, page["ready"][1]))
            self.assertIsNone(self.nav._visible(frame, menu, page["ready"][0]))

    def test_native_scale_battle_anchors_preserve_stage_gold_and_five_rows(self):
        from farmbot.models import Stage
        from farmbot.runner import read_results
        frame, menu = self.frame("scale100_battle.png")
        self.assertEqual(self.nav._read_stage_from_frame(frame, menu), Stage("2-8", "Inferno"))
        rows = read_results(self.vision, frame.image, self.config["measurement"])
        self.assertIsNotNone(rows)
        self.assertEqual([row.duration for row in rows], [70.75, 74.27, 71.82, 71.86, 72.79])
        self.assertEqual([(row.stage, row.gold, row.exp) for row in rows], [("2-8", 2282, 5800)] * 5)

    def test_world_header_anchors_controls_both_above_and_below(self):
        for name, above in (("world_above_battle.png", True), ("world_below_battle.png", False)):
            with self.subTest(name=name):
                frame, menu = self.frame(name)
                header = self.nav._visible(frame, menu, self.nav.config["world_ready"])
                close = self.nav._visible(frame, menu, self.nav.config["close_world"])
                self.assertIsNotNone(header)
                self.assertIsNotNone(close)
                self.assertEqual(header.y < menu.y, above)
                self.assertGreater(close.x, header.x)
                self.assertTrue(self.nav._nodes(frame, menu))

    def test_current_inferno_act_two_page_is_visually_proven(self):
        frame, menu = self.frame("world_above_battle.png")
        page = next(p for p in self.nav.config["pages"] if p["name"] == "ato2_inferno")
        self.assertTrue(all(self.nav._visible(frame, menu, target) is not None for target in page["ready"]))
        nodes = self.nav._nodes(frame, menu)
        # Eight unobscured gold markers; the occupied marker is hidden and the
        # red boss at the lower right must never become an eligible node.
        self.assertEqual(len(nodes), 8)
        self.assertTrue(all(node.x < 690 for node in nodes))
        nightmare = next(p for p in self.nav.config["pages"] if p["name"] == "ato4_nightmare")
        self.assertFalse(all(self.nav._visible(frame, menu, target) is not None for target in nightmare["ready"]))

    def test_invalid_panel_reference_fails_preflight(self):
        for reference in ([1], [float("nan"), 1], [True, 1], "bad"):
            with self.subTest(reference=reference), self.assertRaises(CalibrationError):
                self.nav.config["world_header_anchor"] = reference
                self.nav.preflight()

    def test_open_difficulty_list_and_destination_controls_at_reduced_scale(self):
        frame, menu = self.frame("difficulty_list_above_battle.png")
        for page in self.nav.config["pages"]:
            targets = [page["enter"][0]["expect"], page["enter"][1]["click"]]
            if page["act"] != 2:  # Act 2 is selected in this capture.
                targets.append(page["enter"][2]["click"])
            for target in targets:
                with self.subTest(target=target):
                    self.assertIsNotNone(self.nav._visible(frame, menu, target))
        closed, menu = self.frame("world_above_battle.png")
        target = self.page("ato4_nightmare")["enter"][0]["expect"]
        self.assertIsNone(self.nav._visible(closed, menu, target))

    def test_actual_averno_option_failure_and_other_menu_controls(self):
        frame, menu = self.frame("difficulty_list_20261019.png")
        averno = next(p for p in self.nav.config["pages"] if p["name"] == "ato1_averno")
        target = averno["enter"][1]["click"]
        match = self.nav._visible(frame, menu, target)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.confidence, .9)
        for page in self.nav.config["pages"]:
            targets = [page["enter"][0]["expect"], page["enter"][1]["click"]]
            if page["act"] != 2:
                targets.append(page["enter"][2]["click"])
            for control in targets:
                with self.subTest(control=control):
                    self.assertIsNotNone(self.nav._visible(frame, menu, control))
        closed, closed_menu = self.frame("world_above_battle.png")
        self.assertIsNone(self.nav._visible(closed, closed_menu, target))

    def test_actual_averno_selection_is_confirmed_without_assuming_act(self):
        frame, menu = self.frame("averno_selected_20261019.png")
        page = next(p for p in self.nav.config["pages"] if p["name"] == "ato1_averno")
        targets = [page["enter"][0]["skip_if"], page["enter"][1]["expect"],
                   page["enter"][1]["skip_if"], page["ready"][1]]
        for target in targets:
            self.assertIsNotNone(self.nav._visible(frame, menu, target))
        self.assertFalse(all(self.nav._visible(frame, menu, t) for t in page["ready"]))
        self.assertIsNotNone(self.nav._visible(frame, menu, page["enter"][2]["click"]))
        inferno, inferno_menu = self.frame("world_above_battle.png")
        self.assertIsNone(self.nav._visible(inferno, inferno_menu, targets[0]))

    def test_actual_act2_already_selected_skips_unselected_button(self):
        frame, menu = self.frame("act2_selected_20261019.png")
        page = next(p for p in self.nav.config["pages"] if p["name"] == "ato2_inferno")
        step = page["enter"][2]
        for target in (step["skip_if"], step["expect"], *page["ready"]):
            self.assertIsNotNone(self.nav._visible(frame, menu, target))
        # A text-label click target must not substitute for selected-state proof.
        other, other_menu = self.frame("nightmare_act4_above_battle.png")
        self.assertIsNotNone(self.nav._visible(other, other_menu, step["click"]))
        self.assertIsNone(self.nav._visible(other, other_menu, step["skip_if"]))

    def test_nightmare_act4_selected_and_return_act2(self):
        frame, menu = self.frame("nightmare_act4_above_battle.png")
        page = self.page("ato4_nightmare")
        for target in page["ready"]:
            self.assertIsNotNone(self.nav._visible(frame, menu, target))
        inferno = next(p for p in self.nav.config["pages"] if p["act"] == 2)
        self.assertIsNotNone(self.nav._visible(frame, menu, inferno["enter"][2]["click"]))

    def test_nightmare_transition_confirmed_before_switching_act(self):
        frame, menu = self.frame("nightmare_act2_selected_20261019.png")
        page = self.page("ato4_nightmare")
        targets = [page["enter"][0]["skip_if"], page["enter"][1]["expect"],
                   page["enter"][1]["skip_if"],
                   next(t for t in page["ready"] if t["template"] == "templates/nightmare_selected.png")]
        for target in targets:
            with self.subTest(target=target):
                self.assertEqual(target["template"], "templates/nightmare_selected.png")
                match = self.nav._visible(frame, menu, target)
                self.assertIsNotNone(match)
                self.assertGreaterEqual(match.confidence, .9)
        # Difficulty selection is not evidence that the requested act is ready.
        self.assertFalse(all(self.nav._visible(frame, menu, target) for target in page["ready"]))
        self.assertIsNotNone(self.nav._visible(frame, menu, page["enter"][2]["click"]))
        inferno, inferno_menu = self.frame("world_above_battle.png")
        self.assertIsNone(self.nav._visible(inferno, inferno_menu, targets[0]))

    def test_actual_tooltip_occlusion_uses_current_world_footer(self):
        frame, menu = self.frame("world_tooltip_covers_header.png")
        self.assertIsNone(self.nav._visible(frame, menu, self.nav.config["world_ready"]))
        origin = self.nav._panel_origin(frame.image, menu)
        self.assertIsNotNone(origin)
        self.assertEqual((origin.x, origin.y), (685, -38))
        self.assertTrue(self.nav._nodes(frame, menu))
        # Neither the old menu position nor the close icon alone is enough.
        frame.image[595:630, 335:585] = 0
        self.assertIsNone(self.nav._panel_origin(frame.image, menu))

    def test_gold_node_remains_recognizable_in_actual_failed_click_frame(self):
        before, menu = self.frame("world_above_battle.png")
        node = next(n for n in self.nav._nodes(before, menu) if abs(n.x - 371) < 3 and abs(n.y - 263) < 3)
        hint = self.nav._hint(node, menu)
        current, menu = self.frame("gold_node_with_tooltip.png")
        found = self.nav._node_at_hint(current, menu, hint)
        self.assertAlmostEqual(found.x, 371, delta=2)
        self.assertAlmostEqual(found.y, 263, delta=2)
        self.assertGreaterEqual(found.confidence, .8)
        # Red boss must remain ineligible despite the new resampling.
        self.assertTrue(all(n.x < 680 for n in self.nav._nodes(current, menu)))
