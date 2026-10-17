from pathlib import Path
import tempfile
import unittest

from farmbot.safety import StopController, StopRequested


class StopFileTests(unittest.TestCase):
    def test_immediate_stop_before_worker_initialization_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stop"
            path.write_text("stop")
            stop = StopController(key_check=lambda: False, stop_file=path)
            with self.assertRaises(StopRequested):
                stop.check()
            self.assertFalse(path.exists())
            with self.assertRaises(StopRequested):
                stop.check()

    def test_background_signal_is_sticky_and_does_not_stop_next_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stop"
            with StopController(key_check=lambda: False, stop_file=path) as stop:
                path.write_text("stop")
                self.assertTrue(stop._stop.wait(.5))
                with self.assertRaises(StopRequested):
                    stop.wait(300)
            self.assertFalse(path.exists())
            with StopController(key_check=lambda: False, stop_file=path) as next_stop:
                next_stop.check()


if __name__ == "__main__":
    unittest.main()
