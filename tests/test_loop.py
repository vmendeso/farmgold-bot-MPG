import unittest
from unittest.mock import Mock

from farmbot.loop import CycleDeferred, run_loop
from farmbot.safety import StopRequested


class FakeClock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeStop:
    def __init__(self, clock):
        self.clock = clock
        self.stopped = False
        self.waits = []
        self.interrupt_wait = False

    def check(self):
        if self.stopped:
            raise StopRequested("ESC")

    def wait(self, seconds):
        self.waits.append(seconds)
        if self.interrupt_wait:
            self.clock.advance(min(seconds, 5))
            self.stopped = True
            raise StopRequested("ESC durante espera")
        self.clock.advance(seconds)


class LoopTests(unittest.TestCase):
    def run_durations(self, durations, interval=300, start=0):
        clock = FakeClock(start)
        stop = FakeStop(clock)
        starts = []
        active = False

        def callback():
            nonlocal active
            self.assertFalse(active, "Callbacks must never overlap")
            active = True
            index = len(starts)
            starts.append(clock.now)
            clock.advance(durations[index])
            active = False
            if len(starts) == len(durations):
                stop.stopped = True

        with self.assertRaises(StopRequested):
            run_loop(callback, stop, interval, monotonic=clock.monotonic)
        return starts, stop.waits

    def test_first_callback_is_immediate_and_starts_follow_fixed_cadence(self):
        starts, waits = self.run_durations([10, 25, 0], start=123)
        self.assertEqual(starts, [123, 423, 723])
        self.assertEqual(waits, [290, 275])

    def test_callback_duration_is_not_added_to_the_interval(self):
        starts, waits = self.run_durations([299, 299, 0])
        self.assertEqual(starts, [0, 300, 600])
        self.assertEqual(waits, [1, 1])

    def test_slow_cycles_skip_expired_slots_without_immediate_catch_up(self):
        starts, waits = self.run_durations([301, 601, 0])
        self.assertEqual(starts, [0, 600, 1500])
        self.assertEqual(waits, [299, 299])

    def test_exact_deadline_remains_available_without_unnecessary_skip(self):
        starts, waits = self.run_durations([300, 600, 0])
        self.assertEqual(starts, [0, 300, 900])
        self.assertEqual(waits, [])

    def test_fractional_interval_preserves_start_schedule(self):
        starts, waits = self.run_durations([0.25, 0.5, 0], interval=1.5, start=5)
        self.assertEqual(starts, [5, 6.5, 8])
        self.assertEqual(waits, [1.25, 1])

    def test_callback_exception_propagates_without_wait_or_restart(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        error = RuntimeError("visual confirmation failed")
        callback = Mock(side_effect=error)
        with self.assertRaises(RuntimeError) as raised:
            run_loop(callback, stop, monotonic=clock.monotonic)
        self.assertIs(raised.exception, error)
        callback.assert_called_once_with()
        self.assertEqual(stop.waits, [])

    def test_keyboard_interrupt_from_callback_is_not_swallowed(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        callback = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            run_loop(callback, stop, monotonic=clock.monotonic)
        callback.assert_called_once_with()
        self.assertEqual(stop.waits, [])

    def test_esc_during_wait_prevents_the_next_callback(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        stop.interrupt_wait = True
        callback = Mock()
        with self.assertRaises(StopRequested):
            run_loop(callback, stop, monotonic=clock.monotonic)
        callback.assert_called_once_with()
        self.assertEqual(stop.waits, [300])

    def test_pending_stop_after_wait_is_checked_before_callback(self):
        clock = FakeClock()
        stop = FakeStop(clock)

        def wait_then_stop(seconds):
            clock.advance(seconds)
            stop.stopped = True

        stop.wait = wait_then_stop
        callback = Mock()
        with self.assertRaises(StopRequested):
            run_loop(callback, stop, monotonic=clock.monotonic)
        callback.assert_called_once_with()

    def test_stop_before_first_callback_prevents_any_execution(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        stop.stopped = True
        callback = Mock()
        with self.assertRaises(StopRequested):
            run_loop(callback, stop, monotonic=clock.monotonic)
        callback.assert_not_called()
        self.assertEqual(stop.waits, [])

    def test_invalid_interval_rejected_before_work_or_wait(self):
        for interval in (0, -1, float("nan"), float("inf"), True, None, "invalid"):
            with self.subTest(interval=interval):
                stop = Mock()
                callback = Mock()
                with self.assertRaises(ValueError):
                    run_loop(callback, stop, interval)
                callback.assert_not_called()
                stop.wait.assert_not_called()

    def test_logs_report_start_finish_next_wait_and_skipped_slots(self):
        with self.assertLogs("farmbot.loop", level="INFO") as captured:
            self.run_durations([301, 0])
        messages = "\n".join(captured.output)
        self.assertIn("Ciclo 1 iniciado", messages)
        self.assertIn("Ciclo 1 concluído em 301.0 segundos", messages)
        self.assertIn("Próximo ciclo em 299.0 segundos", messages)
        self.assertIn("1 horário(s) vencido(s)", messages)

    def test_explicit_deferred_cycle_waits_until_next_slot_without_completion_log(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        starts = []

        def callback():
            starts.append(clock.now)
            if len(starts) == 1:
                raise CycleDeferred("Janela sem foco; aguardar próxima rodada")
            stop.stopped = True

        with self.assertLogs("farmbot.loop", level="INFO") as captured, self.assertRaises(StopRequested):
            run_loop(callback, stop, monotonic=clock.monotonic)
        self.assertEqual(starts, [0, 300])
        self.assertEqual(stop.waits, [300])
        messages = "\n".join(captured.output)
        self.assertIn("Ciclo 1 adiado", messages)
        self.assertNotIn("Ciclo 1 concluído", messages)
        self.assertIn("Ciclo 2 concluído", messages)

    def test_slow_deferred_cycle_skips_all_expired_slots_without_immediate_retry(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        starts = []

        def callback():
            starts.append(clock.now)
            if len(starts) == 1:
                clock.advance(650)
                raise CycleDeferred("Condição operacional ainda não confirmada")
            stop.stopped = True

        with self.assertRaises(StopRequested):
            run_loop(callback, stop, monotonic=clock.monotonic)
        self.assertEqual(starts, [0, 900])
        self.assertEqual(stop.waits, [250])

    def test_esc_during_deferred_wait_prevents_any_new_callback(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        stop.interrupt_wait = True
        callback = Mock(side_effect=CycleDeferred("Janela temporariamente indisponível"))
        with self.assertRaises(StopRequested):
            run_loop(callback, stop, monotonic=clock.monotonic)
        callback.assert_called_once_with()
        self.assertEqual(stop.waits, [300])


if __name__ == "__main__":
    unittest.main()
