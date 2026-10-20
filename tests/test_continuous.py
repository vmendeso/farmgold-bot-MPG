from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from farmbot.continuous import SearchCycle
from farmbot.desktop import Frame, InputSafetyError, Rect, WindowError
from farmbot.loop import CycleDeferred
from farmbot.navigation import CalibrationError, DryRunComplete, NavigationError
from farmbot.safety import StopRequested
from farmbot.vision import VisionError


class FailSafeException(Exception):
    pass


class SearchCycleTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "_base_dir": Path("project"), "window": {"exact_title": "Game"},
            "navigation": {"mode": "icon_map"},
            "output": {"ranking": "data/ranking.csv", "artifacts": "artifacts"},
        }
        self.vision, self.store, self.stop = Mock(), Mock(), Mock()
        self.windows = [Mock(last_frame=None, assert_foreground=Mock()), Mock(last_frame=None, assert_foreground=Mock())]
        self.navigators = [Mock(), Mock()]
        self.window_factory = self.patch("WindowManager", side_effect=self.windows)
        self.input_factory = self.patch("SafeInput", side_effect=[Mock(), Mock()])
        self.map_factory = self.patch("MapNavigator", side_effect=self.navigators)
        self.generic_factory = self.patch("Navigator", return_value=Mock())
        self.search = self.patch("run_search", return_value=["best"])
        self.recent = self.patch("recent_stats", return_value=["persisted stats"])
        self.export = self.patch("export_ranking")
        self.save = self.patch("save_frame")
        self.cycle = SearchCycle(self.config, self.vision, self.store, self.stop, dry_run=False)

    def patch(self, name, **kwargs):
        item = patch("farmbot.continuous." + name, **kwargs)
        result = item.start()
        self.addCleanup(item.stop)
        return result

    def test_each_cycle_creates_new_window_input_and_navigator_then_closes(self):
        self.assertEqual(self.cycle(), ["best"])
        self.assertEqual(self.cycle(), ["best"])
        self.assertEqual(self.window_factory.call_count, 2)
        self.assertEqual(self.input_factory.call_count, 2)
        self.assertEqual(self.map_factory.call_count, 2)
        self.assertIs(self.search.call_args_list[0].args[0], self.windows[0])
        self.assertIs(self.search.call_args_list[1].args[0], self.windows[1])
        for window, navigator in zip(self.windows, self.navigators):
            window.assert_foreground.assert_called_once()
            navigator.preflight.assert_called_once()
            window.close.assert_called_once()
            window.request_focus.assert_not_called()
        self.assertEqual(self.export.call_count, 2)

    def test_foreground_and_preflight_are_checked_before_search(self):
        events = []
        self.navigators[0].preflight.side_effect = lambda: events.append("preflight")
        self.windows[0].assert_foreground.side_effect = lambda: events.append("foreground")
        self.search.side_effect = lambda *args: events.append("search")
        self.cycle()
        self.assertEqual(events, ["preflight", "foreground", "search"])

    def test_missing_game_defers_without_any_capture_or_hover_and_can_reacquire(self):
        self.window_factory.side_effect = [WindowError("game absent"), self.windows[1]]
        with self.assertRaises(CycleDeferred) as caught:
            self.cycle()
        self.assertIsInstance(caught.exception.__cause__, WindowError)
        self.input_factory.assert_not_called()
        self.search.assert_not_called()
        self.save.assert_not_called()
        self.assertEqual(self.export.call_count, 1)
        self.assertEqual(self.cycle(), ["best"])
        self.windows[1].close.assert_called_once()

    def test_real_capture_failure_defers_cycle_and_next_cycle_uses_new_window(self):
        from mss.exception import ScreenShotError
        from farmbot.desktop import WindowManager

        with patch("farmbot.desktop._Win32") as api:
            api.return_value.find.return_value = [17]
            api.return_value.rect.return_value = Rect(0, 0, 30, 20)
            api.return_value.process_id.return_value = 123
            api.return_value.foreground.return_value = True
            window = WindowManager({"exact_title": "Game"})
        backend = window._capture = Mock()
        backend.grab.side_effect = ScreenShotError("desktop unavailable")
        self.window_factory.side_effect = [window, self.windows[1]]
        self.search.side_effect = lambda current, *_args: current.capture()
        with self.assertRaises(CycleDeferred) as caught:
            self.cycle()
        self.assertIsInstance(caught.exception.__cause__, WindowError)
        backend.close.assert_called_once()
        self.assertIsNone(window.last_frame)
        self.navigators[0].select.assert_not_called()
        self.save.assert_not_called()
        self.search.side_effect = None
        self.assertEqual(self.cycle(), ["best"])
        self.windows[1].request_focus.assert_not_called()

    def test_background_game_defers_before_capture_hover_or_navigation(self):
        self.windows[0].assert_foreground.side_effect = InputSafetyError("background")
        with self.assertRaises(CycleDeferred):
            self.cycle()
        self.windows[0].capture.assert_not_called()
        self.search.assert_not_called()
        self.navigators[0].prepare_battle.assert_not_called()
        self.navigators[0].select.assert_not_called()
        self.save.assert_not_called()
        self.windows[0].close.assert_called_once()
        self.export.assert_called_once()

    def test_explicit_focus_is_consumed_on_first_call_only(self):
        self.cycle = SearchCycle(self.config, self.vision, self.store, self.stop, dry_run=False, focus=True)
        self.cycle()
        self.cycle()
        self.windows[0].request_focus.assert_called_once()
        self.windows[1].request_focus.assert_not_called()
        self.stop.wait.assert_called_once_with(0.25)

    def test_focus_request_is_consumed_even_if_first_window_is_absent(self):
        self.cycle = SearchCycle(self.config, self.vision, self.store, self.stop, dry_run=False, focus=True)
        self.window_factory.side_effect = [WindowError("game absent"), self.windows[1]]
        with self.assertRaises(CycleDeferred):
            self.cycle()
        self.cycle()
        self.windows[1].request_focus.assert_not_called()
        self.stop.wait.assert_not_called()

    def test_preflight_calibration_failure_is_fatal_without_focus_or_other_gui(self):
        self.cycle = SearchCycle(self.config, self.vision, self.store, self.stop, dry_run=False, focus=True)
        self.navigators[0].preflight.side_effect = CalibrationError("missing route")
        with self.assertRaises(CalibrationError):
            self.cycle()
        self.windows[0].request_focus.assert_not_called()
        self.windows[0].capture.assert_not_called()
        self.search.assert_not_called()
        self.windows[0].close.assert_called_once()
        self.export.assert_called_once()

    def test_stop_during_preflight_prevents_pending_focus_request(self):
        self.cycle = SearchCycle(self.config, self.vision, self.store, self.stop, dry_run=False, focus=True)
        self.stop.check.side_effect = [None, StopRequested("ESC during preflight")]
        with self.assertRaises(StopRequested):
            self.cycle()
        self.windows[0].request_focus.assert_not_called()
        self.search.assert_not_called()
        self.windows[0].close.assert_called_once()

    def test_recoverable_search_errors_defer_without_restoration_clicks(self):
        for exception in (WindowError, InputSafetyError, VisionError, NavigationError):
            with self.subTest(exception=exception):
                self.window_factory.side_effect = None
                self.window_factory.return_value = Mock(last_frame=None, assert_foreground=Mock())
                self.input_factory.side_effect = None
                self.map_factory.side_effect = None
                navigator = self.map_factory.return_value = Mock()
                self.search.side_effect = exception("transient")
                with self.assertRaises(CycleDeferred) as caught:
                    self.cycle()
                self.assertIsInstance(caught.exception.__cause__, exception)
                navigator.select.assert_not_called()
                self.window_factory.return_value.close.assert_called_once()

    def test_dry_run_complete_is_success_printed_without_error_screenshot(self):
        self.search.side_effect = DryRunComplete("dry run: first target")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cycle(), [])
        self.assertIn("dry run: first target", output.getvalue())
        self.save.assert_not_called()
        self.export.assert_called_once()
        self.windows[0].close.assert_called_once()

    def test_stop_failsafe_keyboard_and_unexpected_errors_propagate(self):
        for exception in (StopRequested, KeyboardInterrupt, FailSafeException, RuntimeError, ValueError, CalibrationError):
            with self.subTest(exception=exception):
                self.window_factory.side_effect = None
                window = self.window_factory.return_value = Mock(last_frame=None, assert_foreground=Mock())
                self.input_factory.side_effect = None
                self.map_factory.side_effect = None
                self.map_factory.return_value = Mock()
                self.search.side_effect = exception("stop")
                with self.assertRaises(exception):
                    self.cycle()
                window.close.assert_called_once()

    def test_error_diagnostic_uses_real_last_frame_without_extra_capture(self):
        frame = Frame(SimpleNamespace(shape=(20, 30, 3)), Rect(0, 0, 30, 20), 15, 10)
        self.windows[0].last_frame = frame
        self.search.side_effect = VisionError("unreadable")
        with self.assertRaises(CycleDeferred):
            self.cycle()
        self.save.assert_called_once()
        self.assertIs(self.save.call_args.args[0], frame)
        self.windows[0].capture.assert_not_called()
        self.assertEqual(self.save.call_args.args[1].parent, Path("project/artifacts"))

    def test_mock_or_empty_last_frame_does_not_create_fake_screenshot(self):
        self.windows[0].last_frame = Mock()
        self.search.side_effect = VisionError("unreadable")
        with self.assertRaises(CycleDeferred):
            self.cycle()
        self.save.assert_not_called()

    def test_generic_navigation_selected_without_icon_map_mode(self):
        self.config["navigation"] = {}
        self.cycle()
        self.generic_factory.assert_called_once()
        self.map_factory.assert_not_called()

    def test_export_failure_still_closes_capture_and_stops_scheduler(self):
        self.export.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            self.cycle()
        self.windows[0].close.assert_called_once()

    def test_export_failure_never_replaces_emergency_stop(self):
        self.search.side_effect = StopRequested("ESC")
        self.export.side_effect = OSError("disk full")
        with self.assertRaisesRegex(StopRequested, "ESC"):
            self.cycle()
        self.windows[0].close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
