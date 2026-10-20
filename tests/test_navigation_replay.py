"""Replay observed screens through real routes and OpenCV, without desktop input.

The fake desktop changes state only after the expected button's recognized box
is clicked. It never supplies recognition results. Frames are from separate
captures, not a recording of every intermediate animation.
"""
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from farmbot.config import load_config
from farmbot.desktop import InputSafetyError
from farmbot.map_navigation import MapNavigator
from farmbot.navigation import NavigationError
from farmbot.safety import StopRequested
from farmbot.vision import Vision


INFERNO = "act2_selected_20261019.png"
LIST = "difficulty_list_20261019.png"
NIGHTMARE2 = "nightmare_act2_selected_20261019.png"
NIGHTMARE4 = "nightmare_act4_above_battle.png"
AVERNO2 = "averno_selected_20261019.png"


class ReplayDesktop:
    dry_run = False

    def __init__(self, case, initial, transitions=(), *, padding=(0, 0)):
        self.case = case
        self.state = initial
        # (source, independently specified button name, destination, stale frames)
        self.transitions = deque(transitions)
        self.pending = None
        self.clicks = []
        self.now = 100.0
        self.frames = {}
        self.padding = padding
        self.wait_error = None
        self.capture_error = None

    def check(self):
        pass

    def wait(self, seconds):
        if self.wait_error:
            raise self.wait_error
        self.now += seconds

    def capture(self):
        if self.capture_error:
            raise self.capture_error
        if self.pending:
            destination, left = self.pending
            if left == 0:
                self.state, self.pending = destination, None
            else:
                self.pending = (destination, left - 1)
        if self.state not in self.frames:
            image = cv2.imread(str(Path(__file__).parent / "fixtures" / self.state))
            self.case.assertIsNotNone(image, self.state)
            x, y = self.padding
            self.frames[self.state] = cv2.copyMakeBorder(image, y, 17, x, 23, cv2.BORDER_CONSTANT)
        self.last_frame = SimpleNamespace(image=self.frames[self.state].copy())
        return self.last_frame

    def hover(self, frame, match, **kwargs):
        self.case.assertIs(frame, self.last_frame)
        return True

    def click(self, frame, match):
        self.case.assertIs(frame, self.last_frame)
        self.case.assertTrue(self.transitions, f"Unexpected click: {match.label}")
        source, label, destination, delay = self.transitions.popleft()
        self.case.assertEqual((self.state, match.label), (source, label))
        self.case.assertFalse(self.pending, "Repeated click during an unconfirmed transition")
        h, w = frame.image.shape[:2]
        self.case.assertTrue(0 <= match.x < match.x + match.width <= w)
        self.case.assertTrue(0 <= match.y < match.y + match.height <= h)
        self.clicks.append(label)
        self.pending = (destination, delay)
        return True


class NavigationReplayTests(unittest.TestCase):
    def make_nav(self, initial, transitions=(), **kwargs):
        base = Path(__file__).resolve().parents[1]
        config = load_config(base / "config.json")
        config["navigation"].update(timeout=1, poll_interval=.1)
        desktop = ReplayDesktop(self, initial, transitions, **kwargs)
        nav = MapNavigator(desktop, Vision(config["vision"], base), desktop, desktop, config["navigation"])
        clock = patch("farmbot.map_navigation.time", SimpleNamespace(
            monotonic=lambda: desktop.now, time_ns=time.time_ns))
        clock.start()
        self.addCleanup(clock.stop)
        nav.preflight()
        return nav, desktop

    @staticmethod
    def page(nav, name):
        return next(p for p in nav.config["pages"] if p["name"] == name)

    def test_inventory_to_nightmare_act_four_with_real_templates_and_delays(self):
        transitions = [
            ("inventory_above_battle.png", "world_tab", INFERNO, 2),
            (INFERNO, "difficulty_arrow", LIST, 2),
            (LIST, "nightmare_option", NIGHTMARE2, 2),
            (NIGHTMARE2, "act4_label", NIGHTMARE4, 2),
        ]
        nav, desktop = self.make_nav("inventory_above_battle.png", transitions)
        nav._open_page(self.page(nav, "ato4_nightmare"))
        self.assertEqual(desktop.state, NIGHTMARE4)
        self.assertEqual(desktop.clicks, [step[1] for step in transitions])
        self.assertFalse(desktop.transitions)

    def test_nightmare_route_follows_translated_pixels_without_screen_coordinates(self):
        transitions = [(INFERNO, "difficulty_arrow", LIST, 0),
                       (LIST, "nightmare_option", NIGHTMARE2, 0),
                       (NIGHTMARE2, "act4_label", NIGHTMARE4, 0)]
        nav, desktop = self.make_nav(INFERNO, transitions, padding=(117, 83))
        nav._open_page(self.page(nav, "ato4_nightmare"))
        self.assertEqual(desktop.clicks, [step[1] for step in transitions])

    def test_native_scale_transitions_through_nightmare_and_averno(self):
        inferno, menu, nightmare2, nightmare4, menu4, averno4, averno1 = (
            "scale100_inferno2.png", "scale100_list_inferno2.png",
            "scale100_nightmare2.png", "scale100_nightmare4.png",
            "scale100_list_nightmare4.png", "scale100_averno4.png", "scale100_averno1.png")
        transitions = [(inferno, "difficulty_arrow", menu, 1),
                       (menu, "nightmare_option", nightmare2, 1),
                       (nightmare2, "act4_label", nightmare4, 1),
                       (nightmare4, "difficulty_arrow", menu4, 1),
                       (menu4, "averno_option", averno4, 1),
                       (averno4, "act1_label", averno1, 1)]
        nav, desktop = self.make_nav(inferno, transitions, padding=(43, 61))
        nav._open_page(self.page(nav, "ato2_inferno"))
        self.assertEqual(desktop.clicks, [])
        nav._open_page(self.page(nav, "ato4_nightmare"))
        nav._open_page(self.page(nav, "ato1_averno"))
        self.assertEqual(desktop.state, averno1)
        self.assertEqual(desktop.clicks, [step[1] for step in transitions])
        self.assertFalse(desktop.transitions)

    def test_ready_pages_and_selected_difficulty_skip_unnecessary_clicks(self):
        for screen, name in ((INFERNO, "ato2_inferno"), (NIGHTMARE4, "ato4_nightmare")):
            with self.subTest(screen=screen):
                nav, desktop = self.make_nav(screen)
                nav._open_page(self.page(nav, name))
                self.assertEqual(desktop.clicks, [])
        nav, desktop = self.make_nav(NIGHTMARE2, [
            (NIGHTMARE2, "act4_label", NIGHTMARE4, 1)])
        nav._open_page(self.page(nav, "ato4_nightmare"))
        self.assertEqual(desktop.clicks, ["act4_label"])

    def test_averno_difficulty_does_not_prove_act_one_or_retry_ineffective_click(self):
        transitions = [(INFERNO, "difficulty_arrow", LIST, 0),
                       (LIST, "averno_option", AVERNO2, 2),
                       (AVERNO2, "act1_label", AVERNO2, 0)]
        nav, desktop = self.make_nav(INFERNO, transitions)
        with self.assertRaisesRegex(NavigationError, "transition"):
            nav._open_page(self.page(nav, "ato1_averno"))
        self.assertEqual(desktop.clicks, [step[1] for step in transitions])
        self.assertLessEqual(desktop.now, 101.5)

    def test_dropdown_click_without_effect_expires_without_reclick_or_option_click(self):
        nav, desktop = self.make_nav(INFERNO, [(INFERNO, "difficulty_arrow", INFERNO, 0)])
        with self.assertRaisesRegex(NavigationError, "transition"):
            nav._open_page(self.page(nav, "ato4_nightmare"))
        self.assertEqual(desktop.clicks, ["difficulty_arrow"])
        self.assertAlmostEqual(desktop.now, 101)

    def test_wrong_difficulty_after_option_click_prevents_act_click(self):
        nav, desktop = self.make_nav(INFERNO, [
            (INFERNO, "difficulty_arrow", LIST, 0),
            (LIST, "nightmare_option", AVERNO2, 0)])
        with self.assertRaisesRegex(NavigationError, "transition"):
            nav._open_page(self.page(nav, "ato4_nightmare"))
        self.assertEqual(desktop.clicks, ["difficulty_arrow", "nightmare_option"])

    def test_cancel_or_focus_loss_during_transition_never_causes_another_click(self):
        for error in (StopRequested("ESC"), KeyboardInterrupt(), InputSafetyError("focus lost")):
            with self.subTest(error=type(error).__name__):
                nav, desktop = self.make_nav(INFERNO, [(INFERNO, "difficulty_arrow", INFERNO, 0)])
                desktop.wait_error = error
                with self.assertRaises(type(error)):
                    nav._open_page(self.page(nav, "ato4_nightmare"))
                self.assertEqual(desktop.clicks, ["difficulty_arrow"])

    def test_blank_frame_never_authorizes_input(self):
        nav, desktop = self.make_nav(INFERNO)
        desktop.frames[INFERNO] = np.zeros((830, 780, 3), dtype=np.uint8)
        with self.assertRaisesRegex(NavigationError, "menu anchor"):
            nav._open_page(self.page(nav, "ato4_nightmare"))
        self.assertEqual(desktop.clicks, [])

    def test_page_readiness_matrix_rejects_wrong_act_and_wrong_difficulty(self):
        states = [(INFERNO, "ato2_inferno"), ("world_above_battle.png", "ato2_inferno"),
                  (NIGHTMARE4, "ato4_nightmare"), (NIGHTMARE2, None), (AVERNO2, None),
                  ("inventory_above_battle.png", None)]
        nav, desktop = self.make_nav(INFERNO)
        for screen, expected in states:
            desktop.state = screen
            frame, menu = nav._capture_menu()
            for page in nav.config["pages"]:
                with self.subTest(screen=screen, page=page["name"]):
                    ready = all(nav._visible(frame, menu, t) is not None for t in page["ready"])
                    self.assertEqual(ready, page["name"] == expected)


if __name__ == "__main__":
    unittest.main()
