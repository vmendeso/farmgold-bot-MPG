from __future__ import annotations

import unittest
import json
import os
import shutil
import tempfile
import time
from itertools import cycle
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from farmbot.desktop import HoverUnavailable, InputSafetyError
from farmbot.map_navigation import MapNavigator, UnrecognizedTooltip, WorldAnchorUnavailable, NodeUnavailable
from farmbot.models import Stage
from farmbot.navigation import CalibrationError, DryRunComplete, NavigationError
from farmbot.vision import Match, OCRLine, Vision


def target(name):
    return {"template": name, "scales": [1]}


def configuration():
    return {
        "calibrated": True, "timeout": 0.03, "poll_interval": 0.001, "hover_delay": 0.001,
        "label_timeout": 0.03, "label_poll_interval": 0.001, "label_hover_delay": 0.001,
        "menu": target("menu"), "world_ready": target("world"), "world_tab": target("tab"),
        "close_world": target("close"), "node_gold": target("gold"),
        "world_offset": [-500, 60, 800, 800], "world_tab_offset": [-500, 50, 800, 60],
        "nodes_offset": [-400, 300, 500, 300], "current_stage_offset": [-400, 0, 400, 40],
        "battle_hover_offset": [-300, -50, 20, 20],
        "difficulty_names": ["Pesadelo", "Normal"],
        "pages": [{"name": "act4", "act": 4, "difficulty": "Pesadelo", "enter": [],
                   "ready": [target("act4"), target("nightmare")]}],
    }


class Stop:
    def __init__(self):
        self.now = 0.0

    def check(self):
        pass

    def wait(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


class Window:
    def __init__(self):
        self.battle = "4-8"
        self.world = False
        self.tabs = False
        self.tooltip = None
        self.hidden = set()
        self.captures = 0

    def capture(self):
        self.captures += 1
        return SimpleNamespace(image=SimpleNamespace(shape=(1000, 1000, 3), battle=self.battle,
            world=self.world, tabs=self.tabs, tooltip=self.tooltip, hidden=set(self.hidden)))


class VisionFake:
    def __init__(self):
        self.missing = set()

    def validate_target(self, definition):
        if definition["template"] == "missing-file":
            raise CalibrationError("missing reference")

    def locate(self, image, definition):
        name = definition["template"]
        if name in self.missing:
            return None
        if name == "menu":
            return Match(600, 100, 52, 52, 1.0, "menu")
        if name == "tab" and image.tabs:
            return Match(500, 160, 70, 30, 1.0, "tab")
        if image.world and name in ("world", "act4", "nightmare", "close"):
            return Match(500, 200, 70, 30, 1.0, name)
        return None

    def locate_all(self, image, definition):
        if not image.world:
            return []
        return [Match(x, 450, 50, 50, 1.0, "node:" + stage)
                for stage, x in (("4-7", 300), ("4-8", 400), ("4-9", 500))
                if stage != image.battle and stage not in image.hidden]

    def read_lines(self, image, roi=None, mode="text"):
        if image.world:
            return []  # Actual battlefield footer disappears while World is open.
        return [OCRLine(image.battle + " Marcha da Morte · Pesadelo", 0.95, (100, 100, 400, 30))]


class Inputs:
    def __init__(self, window):
        self.window = window
        self.dry_run = False
        self.clicks = []
        self.hovers = []
        self.hover_matches = []

    def hover(self, frame, match, **kwargs):
        self.hovers.append(match.label)
        self.hover_matches.append(match)
        if self.dry_run:
            return False
        self.window.tooltip = match.label.split(":", 1)[1] if match.label.startswith("node:") else None
        return True

    def click(self, frame, match):
        if self.dry_run:
            return False
        self.clicks.append(match.label)
        if match.label == "menu":
            self.window.tabs = True
        elif match.label == "tab":
            self.window.world = True
        elif match.label == "close":
            self.window.world = False
        elif match.label.startswith("node:"):
            self.window.battle = match.label.split(":", 1)[1]
        return True


class NavigationFake(MapNavigator):
    def _read_stage_from_frame(self, frame, menu, *, deadline=None):
        # Control-flow tests supply a stage reading, not simulated image
        # preprocessing. Consensus itself is tested with four readers below.
        if self.label_vision is self.vision:
            return self._parse_stage_lines(self.vision.read_lines(frame.image, mode="text"))[0]
        return super()._read_stage_from_frame(frame, menu, deadline=deadline)

    @staticmethod
    def _golden(image, match):
        return True

    def _read_tooltip(self, frame, menu, act=None):
        result = frame.image.tooltip
        return result if result and (act is None or int(result.split("-")[0]) == act) else None


class MapNavigationTests(unittest.TestCase):
    def setUp(self):
        self.window = Window()
        self.vision = VisionFake()
        self.inputs = Inputs(self.window)
        self.config = configuration()
        self.nav = NavigationFake(self.window, self.vision, self.inputs, Stop(), self.config)
        clock = patch("farmbot.map_navigation.time", SimpleNamespace(
            monotonic=self.nav.stop.monotonic, time_ns=time.time_ns))
        clock.start()
        self.addCleanup(clock.stop)

    def test_missing_world_anchor_retries_fresh_frames_without_clicks(self):
        self.nav._nodes = Mock(side_effect=[WorldAnchorUnavailable("hidden"), ["node"]])
        frame, menu, nodes = self.nav._capture_nodes()
        self.assertEqual(nodes, ["node"])
        first, second = self.nav._nodes.call_args_list
        self.assertIsNot(first.args[0], second.args[0])
        self.assertIs(frame, second.args[0])
        self.assertEqual(self.inputs.clicks, [])

    def test_anchor_retry_expires_without_clicking(self):
        self.nav._nodes = Mock(side_effect=WorldAnchorUnavailable("hidden"))
        with patch("farmbot.map_navigation.time.monotonic", side_effect=[0, .01, 1]):
            with self.assertRaises(WorldAnchorUnavailable):
                self.nav._capture_nodes()
        self.assertEqual(self.nav._nodes.call_count, 2)
        self.assertEqual(self.inputs.clicks, [])

    def test_anchor_retry_honors_stop_and_does_not_retry_other_errors(self):
        self.nav._nodes = Mock(side_effect=WorldAnchorUnavailable("hidden"))
        self.nav.stop.wait = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.nav._capture_nodes()
        self.assertEqual(self.nav._nodes.call_count, 1)
        self.nav._nodes = Mock(side_effect=NavigationError("ambiguous nodes"))
        with self.assertRaisesRegex(NavigationError, "ambiguous nodes"):
            self.nav._capture_nodes()
        self.assertEqual(self.nav._nodes.call_count, 1)
        self.assertEqual(self.inputs.clicks, [])

    def test_current_stage_reveals_label_with_guarded_hover(self):
        self.assertEqual(self.nav.current_stage(), Stage("4-8", "Pesadelo"))
        self.assertEqual(self.inputs.hovers, ["battlefield label hover"])
        self.assertEqual(self.inputs.clicks, [])
        self.assertEqual(self.window.captures, 3)

    def test_current_stage_retries_only_unavailable_hover_with_fresh_target(self):
        self.inputs.hover = Mock(side_effect=[HoverUnavailable("ownership"), True])
        with self.assertLogs("farmbot.map_navigation", level="WARNING") as messages:
            self.assertEqual(self.nav.current_stage(), Stage("4-8", "Pesadelo"))
        first, second = self.inputs.hover.call_args_list
        self.assertIsNot(first.args[0], second.args[0])
        self.assertIsNot(first.args[1], second.args[1])
        self.assertEqual(first.kwargs["deadline"], second.kwargs["deadline"])
        self.assertEqual(len(messages.output), 1)
        self.assertEqual(self.window.captures, 4)
        self.assertEqual(self.inputs.clicks, [])

    def test_current_stage_hover_retry_expires_without_accepting_label(self):
        now = [0.0]
        self.nav.stop.wait = lambda seconds: now.__setitem__(0, now[0] + seconds)
        self.inputs.hover = Mock(side_effect=HoverUnavailable("ownership"))
        self.nav._read_stage_from_frame = Mock()
        with patch("farmbot.map_navigation.time.monotonic", side_effect=lambda: now[0]):
            self.assertIsNone(self.nav.current_stage())
        self.assertAlmostEqual(now[0], self.nav.label_timeout)
        self.assertGreater(self.inputs.hover.call_count, 1)
        self.assertEqual(self.inputs.hover.call_count, self.window.captures)
        self.nav._read_stage_from_frame.assert_not_called()
        self.assertEqual(self.inputs.clicks, [])

    def test_current_stage_does_not_retry_other_input_safety_failures(self):
        from farmbot.safety import StopRequested

        for error in (InputSafetyError("focus"), InputSafetyError("geometry"),
                      InputSafetyError("stale frame"), StopRequested("ESC")):
            with self.subTest(error=error):
                self.inputs.hover = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    self.nav.current_stage()
                self.inputs.hover.assert_called_once()
        self.assertEqual(self.inputs.clicks, [])

    def test_tooltip_clear_retries_hover_with_new_frame_and_menu_anchor(self):
        self.inputs.hover = Mock(side_effect=[HoverUnavailable("ownership"), True])
        self.nav._clear_tooltip()
        first, second = self.inputs.hover.call_args_list
        self.assertIsNot(first.args[0], second.args[0])
        self.assertIsNot(first.args[1], second.args[1])
        self.assertEqual(first.kwargs["deadline"], second.kwargs["deadline"])
        self.assertEqual(self.inputs.clicks, [])

    def test_node_hover_retry_recaptures_gold_and_keeps_same_deadline(self):
        self.window.world = self.window.tabs = True
        original_hover = self.inputs.hover
        self.nav._node_at_hint = Mock(wraps=self.nav._node_at_hint)
        failed = False

        def hover(frame, match, **kwargs):
            nonlocal failed
            if match.label.startswith("node:") and not failed:
                failed = True
                raise HoverUnavailable("ownership")
            return original_hover(frame, match, **kwargs)

        self.inputs.hover = Mock(side_effect=hover)
        self.assertEqual(self.nav._hover_node(self.config["pages"][0], (-275, 375)), Stage("4-7", "Pesadelo"))
        first, second = self.nav._node_at_hint.call_args_list
        self.assertIsNot(first.args[0], second.args[0])
        self.assertIsNot(first.args[1], second.args[1])
        clear, node_first, node_second = self.inputs.hover.call_args_list
        self.assertIsNot(node_first.args[1], node_second.args[1])
        self.assertEqual({call.kwargs["deadline"] for call in (clear, node_first, node_second)},
                         {clear.kwargs["deadline"]})
        self.assertEqual(self.inputs.clicks, [])

    def test_node_hover_uses_one_deadline_including_tooltip_clear_and_retries(self):
        self.window.world = self.window.tabs = True
        now = [0.0]
        self.nav.stop.wait = lambda seconds: now.__setitem__(0, now[0] + seconds)
        original_hover = self.inputs.hover

        def hover(frame, match, **kwargs):
            if match.label == "menu":
                now[0] += .012
                return original_hover(frame, match, **kwargs)
            now[0] = min(kwargs["deadline"], now[0] + .007)
            raise HoverUnavailable("ownership")

        self.inputs.hover = Mock(side_effect=hover)
        self.nav._read_tooltip = Mock(wraps=self.nav._read_tooltip)
        with patch("farmbot.map_navigation.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(HoverUnavailable):
                self.nav._hover_node(self.config["pages"][0], (-275, 375))
        self.assertAlmostEqual(now[0], self.nav.timeout)
        self.assertEqual({call.kwargs["deadline"] for call in self.inputs.hover.call_args_list}, {self.nav.timeout})
        self.assertEqual(self.nav._read_tooltip.call_count, 1)  # Clearing only; no unowned tooltip is accepted.
        self.assertEqual(self.inputs.clicks, [])

    def test_node_recognition_does_not_restart_hover_deadline(self):
        self.window.world = self.window.tabs = True
        now = [0.0]
        self.nav.stop.wait = lambda seconds: now.__setitem__(0, now[0] + seconds)
        self.nav._node_at_hint = Mock(side_effect=NodeUnavailable("hidden"))
        with patch("farmbot.map_navigation.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(NodeUnavailable):
                self.nav._hover_node(self.config["pages"][0], (-275, 375))
        self.assertAlmostEqual(now[0], self.nav.timeout)
        self.assertEqual(self.inputs.hovers, ["menu"])
        self.assertEqual(self.inputs.clicks, [])

    def test_map_hover_never_retries_other_input_safety_errors_or_stop(self):
        from farmbot.safety import StopRequested

        for error in (InputSafetyError("focus"), InputSafetyError("HWND"),
                      InputSafetyError("geometry"), InputSafetyError("stale frame"),
                      StopRequested("ESC"), KeyboardInterrupt()):
            with self.subTest(error=error):
                self.inputs.hover = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    self.nav._clear_tooltip()
                self.inputs.hover.assert_called_once()
        self.assertEqual(self.inputs.clicks, [])

    def test_click_target_retries_only_hover_then_rechecks_before_one_click(self):
        self.window.world = self.window.tabs = True
        self.inputs.hover = Mock(side_effect=[HoverUnavailable("ownership"), True])
        self.inputs.click = Mock(wraps=self.inputs.click)
        self.nav._click_target(target("world"))
        self.inputs.click.assert_called_once()
        first, second = self.inputs.hover.call_args_list
        clicked = self.inputs.click.call_args
        self.assertIsNot(first.args[0], second.args[0])
        self.assertIsNot(second.args[0], clicked.args[0])
        self.assertEqual(self.inputs.clicks, ["world"])

    def test_world_open_and_close_retry_hover_without_repeating_clicks(self):
        original_hover = self.inputs.hover
        unavailable = {"menu", "tab", "close"}

        def hover(frame, match, **kwargs):
            if match.label in unavailable:
                unavailable.remove(match.label)
                raise HoverUnavailable("ownership")
            return original_hover(frame, match, **kwargs)

        self.inputs.hover = Mock(side_effect=hover)
        self.nav._open_world()
        unavailable.add("menu")  # Closing has its own menu hover acquisition.
        self.nav._close_world()
        self.assertEqual(self.inputs.clicks, ["menu", "tab", "close"])
        self.assertFalse(self.window.world)
        self.assertEqual(self.window.battle, "4-8")

    def test_map_hover_retry_stops_on_lost_focus_during_recapture(self):
        frame = self.window.capture()
        self.window.capture = Mock(side_effect=[frame, InputSafetyError("focus")])
        self.inputs.hover = Mock(side_effect=HoverUnavailable("ownership"))
        with self.assertRaisesRegex(InputSafetyError, "focus"):
            self.nav._clear_tooltip()
        self.inputs.hover.assert_called_once()
        self.assertEqual(self.inputs.clicks, [])

    def test_map_hover_retry_honors_stop_during_wait(self):
        from farmbot.safety import StopRequested

        self.inputs.hover = Mock(side_effect=HoverUnavailable("ownership"))
        self.nav.stop.wait = Mock(side_effect=StopRequested("ESC"))
        with self.assertRaises(StopRequested):
            self.nav._clear_tooltip()
        self.inputs.hover.assert_called_once()
        self.assertEqual(self.inputs.clicks, [])

    def test_map_hover_does_not_move_after_slow_capture_exhausts_deadline(self):
        now = [0.0]
        original_capture = self.window.capture

        def capture():
            now[0] += self.nav.timeout + .001
            return original_capture()

        self.window.capture = capture
        self.inputs.hover = Mock()
        with patch("farmbot.map_navigation.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(HoverUnavailable):
                self.nav._clear_tooltip()
        self.inputs.hover.assert_not_called()
        self.assertEqual(self.inputs.clicks, [])

    def test_map_click_failure_is_not_retried(self):
        self.window.world = self.window.tabs = True
        self.inputs.click = Mock(side_effect=InputSafetyError("covered point"))
        with self.assertRaisesRegex(InputSafetyError, "covered point"):
            self.nav._click_target(target("world"))
        self.inputs.click.assert_called_once()

    def test_node_retry_uses_new_frame_and_does_not_click(self):
        node = Match(300, 450, 50, 50, 1, "node:4-7")
        self.nav._node_at_hint = Mock(side_effect=[NodeUnavailable("hidden"), node])
        frame, _, actual = self.nav._capture_node_at_hint((-275, 375))
        calls = self.nav._node_at_hint.call_args_list
        self.assertIsNot(calls[0].args[0], calls[1].args[0])
        self.assertIs(frame, calls[1].args[0])
        self.assertIs(actual, node)
        self.assertEqual(self.inputs.clicks, [])

    def test_node_retry_timeout_and_cancel_do_not_click(self):
        self.nav._node_at_hint = Mock(side_effect=NodeUnavailable("hidden"))
        with patch("farmbot.map_navigation.time.monotonic", side_effect=[0, .01, 1]):
            with self.assertRaises(NodeUnavailable):
                self.nav._capture_node_at_hint((-275, 375))
        self.assertEqual(self.nav._node_at_hint.call_count, 2)
        self.nav.stop.wait = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.nav._capture_node_at_hint((-275, 375))
        self.assertEqual(self.inputs.clicks, [])

    def test_selection_recovery_still_checks_tooltip_on_click_frame(self):
        original = self.nav._node_at_hint
        failed = False

        def intermittent(frame, menu, hint):
            nonlocal failed
            if frame.image.tooltip == "4-7" and not failed:
                failed = True
                self.window.tooltip = "4-9"
                raise NodeUnavailable("transient")
            return original(frame, menu, hint)

        self.nav._node_at_hint = intermittent
        with self.assertRaisesRegex(NavigationError, "Tooltip changed"):
            self.nav.select(Stage("4-7", "Pesadelo"))
        self.assertTrue(failed)
        self.assertFalse(any(click.startswith("node:") for click in self.inputs.clicks))

    def test_current_stage_rejects_transient_initial_misread(self):
        readings = iter(("4-1", "4-7", "4-7"))
        observed_frames = []

        def read(image, **kwargs):
            observed_frames.append(image)
            return [OCRLine(next(readings) + " Pesadelo", 0.95, (100, 100, 400, 30))]

        self.vision.read_lines = read
        self.assertEqual(self.nav.current_stage(), Stage("4-7", "Pesadelo"))
        self.assertEqual(len(observed_frames), 3)
        self.assertEqual(len({id(frame) for frame in observed_frames}), 3)
        self.assertEqual(self.inputs.hovers, ["battlefield label hover"])

    def test_current_stage_never_accepts_persistently_alternating_readings(self):
        readings = cycle(("4-1", "4-7"))
        self.vision.read_lines = lambda image, **kwargs: [
            OCRLine(next(readings) + " Pesadelo", 0.95, (100, 100, 400, 30))]
        self.assertIsNone(self.nav.current_stage())
        self.assertEqual(self.inputs.hovers, ["battlefield label hover"])

    def test_current_stage_ambiguity_breaks_consecutive_confirmation(self):
        readings = iter(("4-7 Pesadelo", "4-7 4-1 Pesadelo", "4-7 Pesadelo", "4-7 Pesadelo"))
        count = []

        def read(image, **kwargs):
            count.append(1)
            return [OCRLine(next(readings), 0.95, (100, 100, 400, 30))]

        self.vision.read_lines = read
        self.assertEqual(self.nav.current_stage(), Stage("4-7", "Pesadelo"))
        self.assertEqual(len(count), 4)

    def test_current_stage_does_not_return_prior_confirmed_stage_on_ambiguity(self):
        self.assertEqual(self.nav.current_stage(), Stage("4-8", "Pesadelo"))
        self.vision.read_lines = lambda image, **kwargs: []
        self.assertIsNone(self.nav.current_stage())

    def test_discovery_reads_tooltips_and_returns_without_switching_stage(self):
        found = self.nav.discover()
        self.assertEqual(found, [Stage("4-7", "Pesadelo"), Stage("4-9", "Pesadelo")])
        self.assertEqual(self.window.battle, "4-8")
        self.assertFalse(self.window.world)
        self.assertEqual(self.inputs.clicks, ["menu", "tab", "close"])
        self.assertIn("node:4-7", self.inputs.hovers)

    def test_select_clicks_only_after_tooltip_confirmation(self):
        self.nav.select(Stage("4-7", "Pesadelo"))
        self.assertEqual(self.window.battle, "4-7")
        self.assertEqual(self.inputs.clicks, ["menu", "tab", "node:4-7", "close"])

    def test_restore_initial_rescans_newly_unoccluded_marker(self):
        self.nav.discover()
        self.assertNotIn("4-8|Pesadelo", self.nav._hints)
        self.nav.select(Stage("4-7", "Pesadelo"))
        self.nav.select(Stage("4-8", "Pesadelo"))
        self.assertEqual(self.window.battle, "4-8")
        self.assertIn("node:4-8", self.inputs.clicks)

    def test_unseen_or_locked_stage_is_not_clicked(self):
        self.window.hidden.add("4-9")
        with self.assertRaisesRegex(NavigationError, "No gold marker"):
            self.nav.select(Stage("4-9", "Pesadelo"))
        self.assertFalse(any(label.startswith("node:") for label in self.inputs.clicks))

    def test_old_hint_alone_cannot_authorize_input(self):
        self.nav.discover()
        self.window.hidden.add("4-7")
        with self.assertRaisesRegex(NavigationError, "No gold marker"):
            self.nav.select(Stage("4-7", "Pesadelo"))
        self.assertNotIn("node:4-7", self.inputs.clicks)

    def test_missing_selected_difficulty_prevents_hovering_nodes(self):
        self.vision.missing.add("nightmare")
        with self.assertRaisesRegex(NavigationError, "Selected act/difficulty"):
            self.nav.discover()
        self.assertFalse(any(label.startswith("node:") for label in self.inputs.hovers))

    def test_dry_run_does_not_advance_menu_or_move_cursor(self):
        self.inputs.dry_run = True
        with self.assertRaises(DryRunComplete):
            self.nav.discover()
        self.assertFalse(self.window.tabs)
        self.assertFalse(self.window.world)
        self.assertEqual(self.inputs.clicks, [])

    def test_already_open_world_does_not_toggle_menu(self):
        self.window.world = self.window.tabs = True
        self.nav._open_world()
        self.assertEqual(self.inputs.clicks, [])

    def test_discovery_closes_preexisting_world_before_initial_stage_read(self):
        self.window.world = self.window.tabs = True
        self.assertEqual(self.nav.discover(), [Stage("4-7", "Pesadelo"), Stage("4-9", "Pesadelo")])
        self.assertEqual(self.inputs.clicks, ["close", "tab", "close"])
        self.assertEqual(self.window.battle, "4-8")

    def test_prepare_battle_clears_tooltip_before_recognizing_world_header(self):
        self.window.world = self.window.tabs = True
        self.window.tooltip = "4-2"
        original = self.vision.locate

        def locate(image, definition):
            if image.tooltip and definition["template"] in ("world", "close"):
                return None
            return original(image, definition)

        self.vision.locate = locate
        self.assertEqual(self.nav.prepare_battle(), Stage("4-8", "Pesadelo"))
        self.assertEqual(self.inputs.hovers[0], "menu")
        self.assertEqual(self.inputs.clicks, ["close"])

    def test_prepare_battle_recognizes_close_button_without_header(self):
        self.window.world = self.window.tabs = True
        self.vision.missing.add("world")
        self.assertEqual(self.nav.prepare_battle(), Stage("4-8", "Pesadelo"))
        self.assertEqual(self.inputs.clicks, ["close"])

    def test_prepare_battle_never_guesses_missing_close_button(self):
        self.window.world = self.window.tabs = True
        self.vision.missing.add("close")
        with self.assertRaises(NavigationError):
            self.nav.prepare_battle()
        self.assertEqual(self.inputs.clicks, [])

    def test_prepare_battle_dry_run_does_not_close_world(self):
        self.window.world = self.window.tabs = True
        self.inputs.dry_run = True
        with self.assertRaises(DryRunComplete):
            self.nav.prepare_battle()
        self.assertTrue(self.window.world)
        self.assertEqual(self.inputs.clicks, [])

    def test_current_stage_observation_never_closes_open_world(self):
        self.window.world = self.window.tabs = True
        self.assertIsNone(self.nav.current_stage())
        self.assertTrue(self.window.world)
        self.assertEqual(self.inputs.clicks, [])

    def test_label_vision_preserves_white_text_preprocessing(self):
        vision = Vision({"ocr_scale": 2}, ".")
        nav = MapNavigator(self.window, vision, self.inputs, Stop(), self.config)
        self.assertTrue(nav.label_vision.config["white_text"])
        self.assertEqual(nav.label_vision.config["ocr_scale"], 2)
        self.assertTrue(nav.label_fallback_vision.config["white_text"])
        self.assertEqual(nav.label_fallback_vision.config["ocr_scale"], 3)
        self.assertTrue(nav.label_edge_vision.config["white_text"])
        self.assertEqual(nav.label_edge_vision.config["white_text_value_min"], 150)
        self.assertEqual(nav.label_edge_vision.config["ocr_scale"], 2)
        self.assertEqual(nav.label_edge_confirm_vision.config["ocr_scale"], 3)

    def label_readers(self, texts):
        readers = [Mock() for _ in range(4)]
        for reader, text in zip(readers, texts):
            reader.read_lines.return_value = [OCRLine(text, .95, (200, 100, 400, 40))]
        (self.nav.label_vision, self.nav.label_fallback_vision,
         self.nav.label_edge_vision, self.nav.label_edge_confirm_vision) = readers
        return readers

    def test_label_consensus_requires_two_matching_variants_and_two_frames(self):
        readers = self.label_readers(['4-58 Pesadelo', '4-8 Pesadelo', '4-8 Pesadelo', '4-B Pesadelo'])
        self.assertEqual(self.nav.current_stage(), Stage('4-8', 'Pesadelo'))
        self.assertEqual(self.window.captures, 3)
        self.assertTrue(all(r.read_lines.call_count == 2 for r in readers))
        for calls in zip(*(r.read_lines.call_args_list for r in readers)):
            self.assertEqual(len({id(c.args[0]) for c in calls}), 1)
            self.assertEqual(len({c.kwargs['deadline'] for c in calls}), 1)
        self.assertEqual(self.inputs.clicks, [])

    def test_label_one_valid_variant_cannot_confirm_a_stage(self):
        self.label_readers(['4-8 Pesadelo', '4-B Pesadelo', '4-B Pesadelo', ''])
        frame, menu = self.nav._capture_menu()
        self.assertIsNone(self.nav._read_stage_from_frame(frame, menu))

    def test_reported_2_58_misread_is_outvoted_by_complete_2_8_inferno_reads(self):
        self.nav.config["difficulty_names"].append("Inferno")
        self.label_readers(["2-58 Inferno", "2-8 Inferno", "2-8 Inferno", "2-B Inferno"])
        frame, menu = self.nav._capture_menu()
        self.assertEqual(self.nav._read_stage_from_frame(frame, menu), Stage("2-8", "Inferno"))

    def test_stop_between_variants_prevents_further_ocr(self):
        from farmbot.safety import StopRequested
        readers = self.label_readers(["4-8 Pesadelo"] * 4)
        frame, menu = self.nav._capture_menu()
        self.nav.stop.check = Mock(side_effect=[None, StopRequested("ESC")])
        with self.assertRaises(StopRequested):
            self.nav._read_stage_from_frame(frame, menu)
        readers[0].read_lines.assert_called_once()
        for reader in readers[1:]:
            reader.read_lines.assert_not_called()

    def test_label_consensus_recovers_thin_digit_from_two_edge_masks(self):
        self.label_readers(['|-1 Pesadelo', '|-1 Pesadelo', '1-1 Pesadelo', '1-1 Pesadelo'])
        frame, menu = self.nav._capture_menu()
        self.assertEqual(self.nav._read_stage_from_frame(frame, menu), Stage('1-1', 'Pesadelo'))

    def test_label_two_against_two_is_rejected_even_if_first_pair_agrees(self):
        self.label_readers(['4-58 Pesadelo', '4-58 Pesadelo', '4-8 Pesadelo', '4-8 Pesadelo'])
        frame, menu = self.nav._capture_menu()
        self.assertIsNone(self.nav._read_stage_from_frame(frame, menu))

    def test_label_majority_cannot_hide_multiple_ids_or_difficulties(self):
        for ambiguous in ('4-8 4-9 Pesadelo', '4-8 Pesadelo Normal'):
            for position in range(4):
                with self.subTest(ambiguous=ambiguous, position=position):
                    texts = ['4-8 Pesadelo'] * 4
                    texts[position] = ambiguous
                    self.label_readers(texts)
                    frame, menu = self.nav._capture_menu()
                    self.assertIsNone(self.nav._read_stage_from_frame(frame, menu))

    def test_label_never_combines_partial_fields_or_repairs_characters(self):
        for texts in (['4-8', 'Pesadelo', '4-8', 'Pesadelo'], ['4-B Pesadelo'] * 4):
            self.label_readers(texts)
            frame, menu = self.nav._capture_menu()
            self.assertIsNone(self.nav._read_stage_from_frame(frame, menu))

    def test_label_consensus_cannot_continue_after_shared_deadline(self):
        readers = self.label_readers(['4-8 Pesadelo'] * 4)
        now = [0.0]
        def slow_read(*args, **kwargs):
            now[0] = 1
            return [OCRLine('4-8 Pesadelo', .95, (200, 100, 400, 40))]
        readers[0].read_lines.side_effect = slow_read
        frame, menu = self.nav._capture_menu()
        with patch('farmbot.map_navigation.time.monotonic', side_effect=lambda: now[0]):
            self.assertIsNone(self.nav._read_stage_from_frame(frame, menu, deadline=.5))
        for reader in readers[1:]:
            reader.read_lines.assert_not_called()

    def test_label_diagnostics_retain_exact_frame_and_all_variant_readings(self):
        self.label_readers(['4-58 Pesadelo', '4-8 Pesadelo', '4-8 Pesadelo', '4-B Pesadelo'])
        frame, menu = self.nav._capture_menu()
        self.nav._read_stage_from_frame(frame, menu)
        evidence = self.nav.last_label_evidence
        self.assertIs(evidence['frame'], frame)
        self.assertEqual(evidence['selected'], '4-8|Pesadelo')
        self.assertEqual([r['stage'] for r in evidence['reads']],
                         ['4-58|Pesadelo', '4-8|Pesadelo', '4-8|Pesadelo', None])

    def test_missing_later_reference_preflight_prevents_any_input(self):
        self.config["pages"][0]["ready"][1] = target("missing-file")
        with self.assertRaises(CalibrationError):
            self.nav.discover()
        self.assertEqual(self.inputs.hovers, [])
        self.assertEqual(self.inputs.clicks, [])

    def test_visible_skip_guard_prevents_route_click(self):
        self.window.world = self.window.tabs = True
        self.nav.execute_steps([{
            "click": target("unavailable-act-button"), "expect": target("world"),
            "skip_if": target("nightmare"),
        }])
        self.assertEqual(self.inputs.hovers, [])
        self.assertEqual(self.inputs.clicks, [])
        self.assertEqual(self.window.captures, 1)

    def test_absent_skip_guard_runs_normal_checked_step(self):
        self.window.world = self.window.tabs = True
        self.nav.execute_steps([{
            "click": target("world"), "expect": target("world"),
            "skip_if": target("unselected-difficulty"),
        }])
        self.assertEqual(self.inputs.clicks, ["world"])

    def test_missing_skip_reference_preflight_prevents_any_input(self):
        self.config["pages"][0]["enter"] = [{
            "click": target("act4"), "expect": target("world"),
            "skip_if": target("missing-file"),
        }]
        with self.assertRaisesRegex(CalibrationError, "missing reference"):
            self.nav.discover()
        self.assertEqual(self.inputs.hovers, [])
        self.assertEqual(self.inputs.clicks, [])
        self.assertEqual(self.window.captures, 0)

    def test_invalid_skip_guard_schema_prevents_any_input(self):
        with self.assertRaises(CalibrationError):
            self.nav.execute_steps([{"click": target("act4"), "expect": target("world"), "skip_if": {}}])
        self.assertEqual(self.inputs.clicks, [])
        self.assertEqual(self.window.captures, 0)

    def test_ready_page_does_not_execute_enter_route(self):
        self.window.world = self.window.tabs = True
        page = self.config["pages"][0]
        page["enter"] = [{"click": target("unavailable-act-button"), "expect": target("act4")}]
        self.nav._open_page(page)
        self.assertEqual(self.inputs.hovers, [])
        self.assertEqual(self.inputs.clicks, [])

    def test_marker_hint_scales_and_moves_with_recognized_anchor(self):
        menu = Match(200, 300, 104, 104, 1, "menu")
        node = Match(550, 850, 100, 100, 1, "gold")
        self.assertEqual(self.nav._hint(node, menu), (200, 300))
        box = self.nav._box(SimpleNamespace(shape=(2000, 2000, 3)), menu, [-50, 80, 100, 120])
        self.assertEqual(box, (100, 460, 200, 240))

    def test_stale_tooltip_must_disappear_before_next_node(self):
        self.window.world = self.window.tabs = True
        self.window.tooltip = "4-9"
        original_hover = self.inputs.hover
        self.inputs.hover = lambda frame, match, **kwargs: True if match.label == "menu" else original_hover(frame, match, **kwargs)
        with self.assertRaisesRegex(NavigationError, "did not disappear"):
            self.nav._hover_node(self.config["pages"][0], (-275, 375))
        self.assertEqual(self.inputs.clicks, [])

    def test_node_hover_uses_lower_interior_of_recognized_marker(self):
        self.window.world = self.window.tabs = True
        self.assertEqual(self.nav._hover_node(self.config["pages"][0], (-275, 375)), Stage("4-7", "Pesadelo"))
        match = next(item for item in self.inputs.hover_matches if item.label == "node:4-7")
        self.assertEqual((match.x, match.y, match.width, match.height), (325, 492, 1, 1))

    def test_node_hover_fraction_can_be_calibrated_but_never_outside_marker(self):
        self.config["node_hover_fraction"] = [0.2, 0.8]
        nav = NavigationFake(self.window, self.vision, self.inputs, Stop(), self.config)
        match = nav._node_hover_match(Match(100, 200, 50, 40, 0.95, "gold"))
        self.assertEqual(match.center, (110, 232))
        self.config["node_hover_fraction"] = [0.5, 1.0]
        with self.assertRaises(CalibrationError):
            NavigationFake(self.window, self.vision, self.inputs, Stop(), self.config)

    def test_discovery_skips_and_records_only_unreadable_tooltip(self):
        original = self.nav._hover_node

        def hover(page, hint):
            if hint[0] < -200:
                raise UnrecognizedTooltip("occluded title")
            return original(page, hint)

        self.nav._hover_node = hover
        self.assertEqual(self.nav.discover(), [Stage("4-9", "Pesadelo")])
        self.assertEqual(len(self.nav.excluded_nodes), 1)
        self.assertEqual(self.nav.excluded_nodes[0]["reason"], "occluded title")
        self.assertFalse(self.window.world)
        self.assertFalse(any(label.startswith("node:") for label in self.inputs.clicks))

    def test_selection_scans_past_unreadable_tooltip_without_guessing(self):
        original = self.nav._hover_node

        def hover(page, hint):
            if hint[0] < -200:
                raise UnrecognizedTooltip("occluded title")
            return original(page, hint)

        self.nav._hover_node = hover
        self.nav.select(Stage("4-9", "Pesadelo"))
        self.assertEqual(self.window.battle, "4-9")
        self.assertNotIn("node:4-7", self.inputs.clicks)
        self.assertEqual(len(self.nav.excluded_nodes), 1)

    def test_unreadable_target_is_not_selected_by_position(self):
        self.nav._hover_node = lambda page, hint: (_ for _ in ()).throw(UnrecognizedTooltip("unreadable"))
        with self.assertRaisesRegex(NavigationError, "No gold marker"):
            self.nav.select(Stage("4-7", "Pesadelo"))
        self.assertFalse(any(label.startswith("node:") for label in self.inputs.clicks))

    def test_discovery_does_not_swallow_other_navigation_failures(self):
        self.nav._hover_node = lambda page, hint: (_ for _ in ()).throw(NavigationError("unsafe marker"))
        with self.assertRaisesRegex(NavigationError, "unsafe marker"):
            self.nav.discover()
        self.assertEqual(self.nav.excluded_nodes, [])


class GoldColorTests(unittest.TestCase):
    def test_tooltip_region_ignores_darker_gold_border_behind_title(self):
        try:
            import numpy as np
            import cv2
        except ImportError:
            self.skipTest("OpenCV/NumPy not installed")
        image = np.zeros((300, 600, 3), dtype=np.uint8)
        image[60:140, 40:400] = (2, 173, 225)
        cv2.putText(image, "[4-7] Marcha", (70, 110), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (49, 201, 255), 2, cv2.LINE_8)
        config = configuration()
        config["tooltip_offset"] = [0, 0, 600, 300]
        window = Window()
        nav = NavigationFake(window, VisionFake(), Inputs(window), Stop(), config)
        regions = nav._tooltip_rois(image, Match(0, 0, 52, 52, 1.0, "menu"))
        self.assertTrue(any(x <= 180 / 600 <= x + w and y <= 100 / 300 <= y + h
                            for x, y, w, h in regions))
        self.assertTrue(all(h < 70 / 300 for _, _, _, h in regions))

    def test_unreadable_tooltip_diagnostic_uses_supplied_frame_without_capture(self):
        try:
            import numpy as np
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV/NumPy not installed")
        window = Window()
        vision = VisionFake()
        inputs = Inputs(window)
        config = configuration()
        nav = NavigationFake(window, vision, inputs, Stop(), config)
        frame = SimpleNamespace(image=np.full((1000, 1000, 3), (10, 20, 30), dtype=np.uint8))
        menu = Match(600, 100, 52, 52, 1.0, "menu")
        exc = UnrecognizedTooltip("occluded", frame=frame, menu=menu,
                                  ocr=[{"text": "[4-?]", "confidence": 0.3, "roi": [0, 0, 1, 1]}])
        with tempfile.TemporaryDirectory() as temporary:
            vision.base_dir = Path(temporary)
            nav._exclude_tooltip(config["pages"][0], (-275, 375), exc)
            files = list((Path(temporary) / "artifacts").glob("map-*.png"))
            self.assertEqual(len(files), 2)
            diagnostic = Path(nav.excluded_nodes[0]["diagnostic"])
            metadata = json.loads(diagnostic.read_text(encoding="utf-8"))
            self.assertEqual(metadata["ocr"][0]["text"], "[4-?]")
            self.assertEqual(metadata["anchor_hint"], [-275, 375])
            self.assertTrue(Path(nav.excluded_nodes[0]["screenshot"]).is_file())
        self.assertEqual(window.captures, 0)
        self.assertEqual(inputs.hovers, [])
        self.assertEqual(inputs.clicks, [])

    def test_tooltip_ink_mask_removes_overlapping_white_text_and_gold_button(self):
        try:
            import numpy as np
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV/NumPy not installed")
        # Actual sampled BGR colors: title ink, difficulty-button fill, white
        # label behind the tooltip, and dark tooltip background.
        image = np.array([[(49, 201, 255), (2, 173, 225), (255, 255, 255), (5, 9, 11)]], dtype=np.uint8)
        result = MapNavigator._tooltip_text_image(image)
        self.assertEqual(result[0, :, 0].tolist(), [255, 0, 0, 0])
        self.assertEqual(result.shape, image.shape)

    def test_grayscale_similar_red_or_gray_marker_is_not_gold(self):
        try:
            import numpy as np
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV/NumPy not installed")
        marker = Match(0, 0, 50, 50, 1, "node")
        for color, expected in (((0, 200, 255), True), ((0, 0, 255), False), ((150, 150, 150), False)):
            with self.subTest(color=color):
                image = np.full((50, 50, 3), color, dtype=np.uint8)
                self.assertEqual(MapNavigator._golden(image, marker), expected)


class ObservedFooterTests(unittest.TestCase):
    def test_reduced_digit_one_recovers_edges_without_using_unmasked_scenery(self):
        try:
            import cv2
            import pytesseract  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV/Tesseract Python package not installed")
        local = Path(__file__).resolve().parents[1] / ".tools/tesseract/tesseract.exe"
        command = os.environ.get("TESSERACT_CMD") or (str(local) if local.is_file() else shutil.which("tesseract"))
        if not command:
            self.skipTest("Tesseract executable unavailable")
        config = configuration()
        config["current_stage_offset"] = [0, 0, 420, 23]
        config["difficulty_names"] = ["Inferno", "Pesadelo", "Normal", "Averno"]
        vision = Vision({"tesseract_cmd": command, "language": "por+eng", "ocr_scale": 2,
                         "min_confidence": .25}, Path("."))
        nav = MapNavigator(None, vision, None, Stop(), config)
        menu = Match(0, 0, 52, 52, 1.0, "menu")
        for name in ("stage_1_1_footer_digit_one.png", "stage_1_1_footer_difficulty_effect.png",
                     "stage_1_1_footer_digit_effect.png", "stage_1_1_footer_low_brightness_noise.png"):
            with self.subTest(fixture=name):
                image = cv2.imread(str(Path(__file__).parent / "fixtures" / name))
                frame = SimpleNamespace(image=image)
                self.assertEqual(nav._read_stage_from_frame(frame, menu), Stage("1-1", "Averno"))

    def test_reduced_footer_recovers_digit_eight_without_replacing_letters(self):
        try:
            import cv2
            import pytesseract  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV/Tesseract Python package not installed")
        local = Path(__file__).resolve().parents[1] / ".tools/tesseract/tesseract.exe"
        command = os.environ.get("TESSERACT_CMD") or (str(local) if local.is_file() else shutil.which("tesseract"))
        if not command:
            self.skipTest("Tesseract executable unavailable")
        config = configuration()
        config["current_stage_offset"] = [0, 0, 420, 23]
        config["difficulty_names"] = ["Inferno", "Pesadelo", "Normal"]
        vision = Vision({"tesseract_cmd": command, "language": "por+eng", "ocr_scale": 2,
                         "min_confidence": 0.25}, Path("."))
        nav = MapNavigator(None, vision, None, Stop(), config)
        menu = Match(0, 0, 52, 52, 1.0, "menu")
        for name in ("stage_2_8_footer_letter_b.png", "stage_2_8_footer_primary_only.png",
                     "stage_2_8_footer_effect.png", "stage_2_8_after_false_258.png",
                     "stage_2_8_after_false_258_next.png"):
            with self.subTest(fixture=name):
                image = cv2.imread(str(Path(__file__).parent / "fixtures" / name))
                self.assertEqual(nav._read_stage_from_frame(SimpleNamespace(image=image), menu),
                                 Stage("2-8", "Inferno"))

    def test_long_footer_keeps_difficulty_and_excludes_next_stage_portal(self):
        try:
            import cv2
            import pytesseract  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV/Tesseract Python package not installed")
        local = Path(__file__).resolve().parents[1] / ".tools/tesseract/tesseract.exe"
        command = os.environ.get("TESSERACT_CMD") or (str(local) if local.is_file() else shutil.which("tesseract"))
        if not command:
            self.skipTest("Tesseract executable unavailable")
        config = configuration()
        config.pop("current_stage_offset")  # Exercise the calibrated default.
        vision = Vision({"tesseract_cmd": command, "language": "por+eng", "ocr_scale": 2,
                         "min_confidence": 0.25}, Path("."))
        nav = MapNavigator(None, vision, None, Stop(), config)
        menu = Match(570, 9, 52, 52, 1.0, "menu")
        for name, expected in (("stage_4_6_long_footer_portal.png", "4-6"),
                               ("stage_4_7_footer_portal.png", "4-7")):
            with self.subTest(fixture=name):
                image = cv2.imread(str(Path(__file__).parent / "fixtures" / name))
                result = nav._read_stage_from_frame(SimpleNamespace(image=image), menu)
                self.assertEqual(result, Stage(expected, "Pesadelo"))


if __name__ == "__main__":
    unittest.main()
