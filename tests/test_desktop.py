import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

from farmbot.desktop import Frame, HoverUnavailable, InputSafetyError, Rect, SafeInput, WindowError, WindowManager
from farmbot.safety import StopController, StopRequested
from farmbot.vision import Match


class DesktopSafetyTests(unittest.TestCase):
    def setUp(self):
        self.rect = Rect(-500, 100, 400, 200)
        self.window = Mock()
        self.window.assert_foreground = Mock()
        self.window.config = {"max_frame_age_seconds": 1.0}
        self.window.current.return_value = (17, self.rect)
        self.window._api.owns_point.return_value = True
        self.stop = StopController(key_check=lambda: False)
        self.frame = Frame(None, self.rect, 17, time.monotonic())
        self.match = Match(20, 30, 40, 20, .99, "Mundo")
        self.input = SafeInput(self.window, self.stop)

    def test_dry_run_never_imports_input_library_or_clicks(self):
        with patch.dict(sys.modules, {"pyautogui": None}):
            self.assertFalse(self.input.click(self.frame, self.match))
        self.window._api.owns_point.assert_called_with(17, -460, 140)

    def test_rejects_all_unsafe_geometry(self):
        targets = [Match(-1, 1, 20, 20, .99, "outside"), Match(390, 1, 20, 20, .99, "outside"), Match(1, 190, 20, 20, .99, "outside"), Match(1, 1, 0, 20, .99, "empty"), Match(float("nan"), 1, 20, 20, .99, "nan")]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(InputSafetyError):
                self.input.click(self.frame, target)

    def test_rejects_stale_or_future_frame(self):
        for captured in (time.monotonic() - 10, time.monotonic() + 10):
            with self.subTest(captured=captured), self.assertRaises(InputSafetyError):
                self.input.click(Frame(None, self.rect, 17, captured), self.match)

    def test_rejects_window_move_resize_and_handle_change(self):
        for changed in ((18, self.rect), (17, Rect(-499, 100, 400, 200)), (17, Rect(-500, 100, 401, 200))):
            self.window.current.return_value = changed
            with self.subTest(changed=changed), self.assertRaises(InputSafetyError):
                self.input.click(self.frame, self.match)

    def test_rejects_foreground_change_and_overlap(self):
        self.window.assert_foreground.side_effect = InputSafetyError("focus")
        with self.assertRaises(InputSafetyError):
            self.input.click(self.frame, self.match)
        self.window.assert_foreground.side_effect = None
        self.window._api.owns_point.return_value = False
        with self.assertRaises(InputSafetyError):
            self.input.click(self.frame, self.match)

    def test_rechecks_after_moving_pointer(self):
        self.input.dry_run = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        driver.moveTo.side_effect = lambda *args, **kwargs: setattr(self.window.current, "return_value", (17, Rect(0, 0, 400, 200)))
        with patch.dict(sys.modules, {"pyautogui": driver}), self.assertRaises(InputSafetyError):
            self.input.click(self.frame, self.match)
        self.assertTrue(driver.FAILSAFE)
        driver.click.assert_not_called()

    def test_real_input_uses_guarded_desktop_coordinates(self):
        self.input.dry_run = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}):
            self.assertTrue(self.input.click(self.frame, self.match))
        driver.click.assert_called_once_with(x=-460, y=140, button="left")
        self.assertTrue(driver.FAILSAFE)

    def test_dry_run_hover_does_not_import_input_or_move(self):
        self.window._api.owns_point.return_value = False
        with patch.dict(sys.modules, {"pyautogui": None}):
            self.assertFalse(self.input.hover(self.frame, self.match))
        self.window._api.owns_point.assert_not_called()

    def test_hover_waits_for_ownership_without_clicking(self):
        self.input.dry_run = False
        self.window._api.owns_point.side_effect = [False, True, True]
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}):
            self.assertTrue(self.input.hover(self.frame, self.match))
        driver.moveTo.assert_called_once_with(-460, 140, duration=0)
        driver.click.assert_not_called()
        self.assertTrue(driver.FAILSAFE)

    def test_hover_rejects_persistent_foreign_ownership(self):
        self.input.dry_run = False
        self.input.hover_ownership_timeout = .05
        self.window._api.owns_point.return_value = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}), self.assertRaisesRegex(HoverUnavailable, "hover"):
            self.input.hover(self.frame, self.match)
        driver.click.assert_not_called()

    def test_hover_tolerates_delayed_ownership_without_repeating_movement(self):
        now = [100.0]
        self.input.dry_run = False
        self.frame = Frame(None, self.rect, 17, now[0])
        self.input.stop = Mock()
        self.input.stop.wait.side_effect = lambda duration: now.__setitem__(0, now[0] + duration)
        self.window._api.owns_point.side_effect = lambda *args: now[0] >= 100.7
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}), patch("farmbot.desktop.time.monotonic", side_effect=lambda: now[0]):
            self.assertTrue(self.input.hover(self.frame, self.match))
        self.assertGreaterEqual(now[0], 100.7)
        self.assertLess(now[0], 101)
        driver.moveTo.assert_called_once()
        driver.click.assert_not_called()

    def test_hover_honors_callers_remaining_deadline(self):
        now = [100.0]
        self.input.dry_run = False
        self.frame = Frame(None, self.rect, 17, now[0])
        self.input.stop = Mock()
        self.input.stop.wait.side_effect = lambda duration: now.__setitem__(0, now[0] + duration)
        self.window._api.owns_point.return_value = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}), patch("farmbot.desktop.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(HoverUnavailable):
                self.input.hover(self.frame, self.match, deadline=100.2)
            self.assertAlmostEqual(now[0], 100.2)
            driver.moveTo.reset_mock()
            with self.assertRaises(HoverUnavailable):
                self.input.hover(self.frame, self.match, deadline=100.2)
            driver.moveTo.assert_not_called()
        driver.click.assert_not_called()

    def test_hover_wait_rechecks_every_hard_guard(self):
        for change in ("focus", "geometry", "handle", "age", "stop"):
            with self.subTest(change=change):
                self.setUp()
                self.input.dry_run = False
                self.window._api.owns_point.return_value = False
                self.input.stop = Mock()
                now = [100.0]
                self.frame = Frame(None, self.rect, 17, now[0])

                def change_after_wait(duration):
                    now[0] += duration
                    if change == "focus":
                        self.window.assert_foreground.side_effect = InputSafetyError("focus")
                    elif change == "geometry":
                        self.window.current.return_value = (17, Rect(0, 0, 400, 200))
                    elif change == "handle":
                        self.window.current.return_value = (18, self.rect)
                    elif change == "age":
                        now[0] = 102
                    elif change == "stop":
                        self.input.stop.check.side_effect = StopRequested("stop")

                self.input.stop.wait.side_effect = change_after_wait
                driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
                error_type = StopRequested if change == "stop" else InputSafetyError
                with patch.dict(sys.modules, {"pyautogui": driver}), patch("farmbot.desktop.time.monotonic", side_effect=lambda: now[0]):
                    with self.assertRaises(error_type) as caught:
                        self.input.hover(self.frame, self.match)
                self.assertNotIsInstance(caught.exception, HoverUnavailable)
                self.input.stop.wait.assert_called_once()
                driver.moveTo.assert_called_once()
                driver.click.assert_not_called()

    def test_hover_timeout_configuration_is_finite_positive_and_short(self):
        for value in (0, -1, float("nan"), float("inf"), True, "1", 2.01):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.window.config["hover_ownership_timeout_seconds"] = value
                SafeInput(self.window, self.stop)
        self.window.config["hover_ownership_timeout_seconds"] = .75
        self.assertEqual(SafeInput(self.window, self.stop).hover_ownership_timeout, .75)

    def test_hover_rejects_window_or_focus_change_after_movement(self):
        self.input.dry_run = False
        for change in ("geometry", "focus"):
            driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
            if change == "geometry":
                driver.moveTo.side_effect = lambda *args, **kwargs: setattr(self.window.current, "return_value", (17, Rect(0, 0, 400, 200)))
            else:
                driver.moveTo.side_effect = lambda *args, **kwargs: setattr(self.window.assert_foreground, "side_effect", InputSafetyError("focus"))
            with self.subTest(change=change), patch.dict(sys.modules, {"pyautogui": driver}), self.assertRaises(InputSafetyError):
                self.input.hover(self.frame, self.match)
            driver.click.assert_not_called()
            self.window.current.return_value = (17, self.rect)
            self.window.assert_foreground.side_effect = None

    def test_hover_rejects_outside_target_before_moving(self):
        self.input.dry_run = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}), self.assertRaises(InputSafetyError):
            self.input.hover(self.frame, Match(390, 1, 20, 20, .99, "outside"))
        driver.moveTo.assert_not_called()
        driver.click.assert_not_called()

    def test_hover_checks_emergency_stop_after_moving(self):
        self.input.dry_run = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        driver.moveTo.side_effect = lambda *args, **kwargs: self.stop.request_stop("test")
        with patch.dict(sys.modules, {"pyautogui": driver}), self.assertRaises(StopRequested):
            self.input.hover(self.frame, self.match)
        driver.click.assert_not_called()

    def test_click_still_rejects_unowned_point_before_moving(self):
        self.input.dry_run = False
        self.window._api.owns_point.return_value = False
        driver = types.SimpleNamespace(FAILSAFE=False, moveTo=Mock(), click=Mock())
        with patch.dict(sys.modules, {"pyautogui": driver}), self.assertRaises(InputSafetyError):
            self.input.click(self.frame, self.match)
        driver.moveTo.assert_not_called()
        driver.click.assert_not_called()

    def test_window_selection_rejects_multiple_matches(self):
        with patch("farmbot.desktop._Win32") as api:
            api.return_value.find.return_value = [11, 12]
            with self.assertRaises(WindowError):
                WindowManager({"title_regex": "My Party"})

    def test_window_manager_does_not_reacquire_changed_window(self):
        with patch("farmbot.desktop._Win32") as api:
            api.return_value.find.return_value = [17]
            api.return_value.process_id.return_value = 123
            api.return_value.rect.return_value = self.rect
            manager = WindowManager({"title_regex": "My Party"})
            api.return_value.process_id.return_value = 124
            with self.assertRaises(WindowError):
                manager.current()
            api.return_value.find.assert_called_once()


class EmergencyStopTests(unittest.TestCase):
    def test_esc_interrupts_wait_without_background_thread(self):
        state = iter([False, False, True])
        stop = StopController(key_check=lambda: next(state, True), poll_interval=.01)
        start = time.monotonic()
        with self.assertRaises(StopRequested):
            stop.wait(5)
        self.assertLess(time.monotonic() - start, .5)

    def test_background_thread_catches_short_press_during_work(self):
        pressed = __import__("threading").Event()
        stop = StopController(key_check=pressed.is_set, poll_interval=.01)
        with stop:
            pressed.set()
            self.assertTrue(stop._stop.wait(.5))
            pressed.clear()
            with self.assertRaises(StopRequested):
                stop.check()

    def test_stop_before_click(self):
        stop = StopController(key_check=lambda: False)
        stop.request_stop("test")
        with self.assertRaises(StopRequested):
            stop.wait(0)


if __name__ == "__main__":
    unittest.main()
