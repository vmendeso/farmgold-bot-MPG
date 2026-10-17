import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from farmbot.instance import InstanceAlreadyRunning, InstanceLock


class InstanceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "farmbot.lock"

    def run_child(self, code):
        return subprocess.run(
            [sys.executable, "-c", code, str(self.path)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def test_second_lock_is_rejected_without_releasing_first(self):
        with InstanceLock(self.path):
            second = InstanceLock(self.path)
            with self.assertRaisesRegex(InstanceAlreadyRunning, "Outro bot já está ativo"):
                second.__enter__()
            second.__exit__(None, None, None)
            with self.assertRaises(InstanceAlreadyRunning):
                InstanceLock(self.path).__enter__()

    def test_release_reuses_same_persistent_file_and_writes_informational_pid(self):
        with InstanceLock(self.path):
            first_inode = self.path.stat().st_ino
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.path.read_text(), f"pid={os.getpid()}\n")
        with InstanceLock(self.path):
            self.assertEqual(self.path.stat().st_ino, first_inode)
        self.assertTrue(self.path.is_file())

    def test_exception_releases_lock_and_preserves_original_exception(self):
        with self.assertRaisesRegex(ValueError, "body failed"):
            with InstanceLock(self.path):
                raise ValueError("body failed")
        with InstanceLock(self.path):
            pass

    def test_cleanup_is_idempotent_before_acquisition_and_after_release(self):
        lock = InstanceLock(self.path)
        lock.__exit__(None, None, None)
        self.assertFalse(self.path.exists())
        with lock:
            pass
        lock.__exit__(None, None, None)
        with lock:
            pass

    def test_stale_pid_does_not_block_a_new_instance(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("pid=999999999\n")
        with InstanceLock(self.path):
            pass
        self.assertEqual(self.path.read_text(), f"pid={os.getpid()}\n")

    def test_separate_process_cannot_acquire_owned_lock(self):
        script = (
            "import sys\n"
            "from farmbot.instance import InstanceLock, InstanceAlreadyRunning\n"
            "try:\n"
            "    with InstanceLock(sys.argv[1]): pass\n"
            "except InstanceAlreadyRunning:\n"
            "    sys.exit(23)\n"
        )
        with InstanceLock(self.path):
            result = self.run_child(script)
            self.assertEqual(result.returncode, 23, result.stderr)
        result = self.run_child(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_abrupt_process_exit_releases_os_lock(self):
        result = self.run_child(
            "import os,sys\n"
            "from farmbot.instance import InstanceLock\n"
            "lock=InstanceLock(sys.argv[1])\n"
            "lock.__enter__()\n"
            "os._exit(0)\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.path.is_file())
        with InstanceLock(self.path):
            pass


if __name__ == "__main__":
    unittest.main()
