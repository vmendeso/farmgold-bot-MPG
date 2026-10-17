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
        self.nav = MapNavigator(None, self.vision, None, StopController(), self.config["navigation"])

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
        target = self.nav.config["pages"][0]["enter"][0]["expect"]
        self.assertIsNone(self.nav._visible(closed, menu, target))

    def test_nightmare_act4_selected_and_return_act2(self):
        frame, menu = self.frame("nightmare_act4_above_battle.png")
        page = self.nav.config["pages"][0]
        for target in page["ready"]:
            self.assertIsNotNone(self.nav._visible(frame, menu, target))
        inferno = next(p for p in self.nav.config["pages"] if p["act"] == 2)
        self.assertIsNotNone(self.nav._visible(frame, menu, inferno["enter"][2]["click"]))

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
