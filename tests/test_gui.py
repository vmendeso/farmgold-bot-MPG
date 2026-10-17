import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from farmbot.gui import GuiSettings, WorkerManager, activity_status, build_command, read_tail, save_settings
from farmbot.instance import InstanceAlreadyRunning, InstanceLock


class GuiSettingsTests(unittest.TestCase):
    def test_defaults_are_dry_run_without_automatic_switch(self):
        settings = GuiSettings.from_config({})
        self.assertTrue(settings.dry_run)
        self.assertFalse(settings.auto_switch)
        self.assertEqual((settings.interval_minutes, settings.max_stages), (5, 1))

    def test_portuguese_decimal_input_and_success_percentage(self):
        settings = GuiSettings.from_values({"interval_minutes": "2,5", "min_win_rate_percent": "85,5", "battles_per_stage": "7"})
        self.assertEqual(settings.interval_minutes, 2.5)
        self.assertEqual(settings.min_win_rate_percent, 85.5)
        self.assertEqual(settings.battles_per_stage, 7)

    def test_restart_seconds_support_portuguese_decimal_zero_and_legacy_config(self):
        self.assertEqual(GuiSettings.from_config({}).battle_restart_seconds, 0)
        for text, expected in (("5", 5), ("2,5", 2.5), ("0", 0)):
            self.assertEqual(GuiSettings.from_values({"battle_restart_seconds": text}).battle_restart_seconds, expected)
        self.assertEqual(GuiSettings.from_config({"measurement": {"battle_restart_seconds": 5}}).battle_restart_seconds, 5)

    def test_early_stop_accepts_portuguese_decimal_and_zero_disables(self):
        self.assertEqual(GuiSettings.from_values({"early_stop_gold_per_minute": "3,5"}).early_stop_gold_per_minute, 3.5)
        self.assertEqual(GuiSettings.from_values({"early_stop_gold_per_minute": "0"}).early_stop_gold_per_minute, 0)

    def test_early_stop_config_maps_missing_and_null_to_zero_and_preserves_limit(self):
        for search in ({}, {"early_stop_gold_per_minute": None}, {"early_stop_gold_per_minute": 3}):
            with self.subTest(search=search):
                settings = GuiSettings.from_config({"search": search})
                self.assertEqual(settings.early_stop_gold_per_minute, search.get("early_stop_gold_per_minute") or 0)

    def test_invalid_values_never_produce_settings(self):
        for values in ({"interval_minutes": "nan"}, {"interval_minutes": "1e308"}, {"battles_per_stage": 1.5},
                       {"max_stages": 0}, {"battle_timeout": -1}, {"cache_hours": "inf"},
                       {"min_win_rate_percent": 101}, {"min_win_rate_percent": 0},
                       {"early_stop_gold_per_minute": -1}, {"early_stop_gold_per_minute": "nan"},
                       {"early_stop_gold_per_minute": "inf"}, {"early_stop_gold_per_minute": True},
                       {"battle_restart_seconds": -1}, {"battle_restart_seconds": "nan"},
                       {"battle_restart_seconds": "inf"}, {"battle_restart_seconds": True},
                       {"auto_switch": True}, {"dry_run": "false"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                GuiSettings.from_values(values)

    def test_config_preferences_are_preserved(self):
        settings = GuiSettings.from_config({"dry_run": False, "auto_switch_best": True,
                                           "loop": {"interval_minutes": 10, "max_stages": 3},
                                           "ui": {"focus_on_start": True}, "measurement": {"min_win_rate": .9}})
        self.assertFalse(settings.dry_run)
        self.assertTrue(settings.auto_switch)
        self.assertTrue(settings.focus_on_start)
        self.assertEqual(settings.min_win_rate_percent, 90)

    def test_command_uses_argument_list_and_global_focus_before_loop(self):
        settings = GuiSettings(dry_run=False, auto_switch=True, focus_on_start=True)
        command = build_command(Path("folder with spaces/config.json"), settings, python="python.exe")
        self.assertEqual(command[:6], ["python.exe", "-u", "-X", "utf8", "-m", "farmbot"])
        self.assertLess(command.index("--focus"), command.index("loop"))
        self.assertIn("--execute", command)
        self.assertIn("--auto-switch", command)
        self.assertEqual(command[command.index("--config") + 1], str(Path("folder with spaces/config.json").resolve()))

    def test_dry_run_command_does_not_enable_clicks(self):
        command = build_command("config.json", GuiSettings())
        self.assertNotIn("--execute", command)
        self.assertNotIn("--auto-switch", command)


class GuiFilesAndWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.original = {"dry_run": True, "auto_switch_best": False,
                         "measurement": {"battles_per_stage": 5, "min_win_rate": .8, "logs_roi": [0, 0, 1, .3]},
                         "search": {"cache_hours": 6, "max_stages": 12},
                         "navigation": {"menu": {"template": "my observed menu.png"}},
                         "custom": {"keep": [1, 2, 3]}, "_base_dir": "not serialized", "_path": "not serialized"}
        self.path.write_text(json.dumps(self.original), encoding="utf-8")

    def test_atomic_save_preserves_calibration_and_unknown_fields(self):
        settings = GuiSettings(interval_minutes=8, battles_per_stage=7, max_stages=2, min_win_rate_percent=85,
                               dry_run=False, focus_on_start=True)
        saved = save_settings(self.path, settings)
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved, on_disk)
        self.assertEqual(on_disk["navigation"], self.original["navigation"])
        self.assertEqual(on_disk["custom"], self.original["custom"])
        self.assertEqual(on_disk["measurement"]["logs_roi"], self.original["measurement"]["logs_roi"])
        self.assertEqual(on_disk["search"]["max_stages"], 12)
        self.assertEqual(on_disk["measurement"]["min_win_rate"], .85)
        self.assertEqual(on_disk["loop"], {"interval_minutes": 8, "max_stages": 2})
        self.assertTrue(on_disk["ui"]["focus_on_start"])
        self.assertFalse(any(key.startswith("_") for key in on_disk))
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_failed_replace_preserves_original_configuration(self):
        original_bytes = self.path.read_bytes()
        with patch("farmbot.gui.os.replace", side_effect=OSError("disk issue")), self.assertRaises(OSError):
            save_settings(self.path, GuiSettings())
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_restart_seconds_save_roundtrip_preserves_calibration(self):
        saved = save_settings(self.path, GuiSettings(battle_restart_seconds=5))
        self.assertEqual(saved["measurement"]["battle_restart_seconds"], 5)
        self.assertEqual(GuiSettings.from_config(saved).battle_restart_seconds, 5)
        self.assertEqual(saved["navigation"], self.original["navigation"])

    def test_early_stop_roundtrip_and_disable_preserve_other_search_values(self):
        enabled = save_settings(self.path, GuiSettings(early_stop_gold_per_minute=3))
        self.assertEqual(enabled["search"]["early_stop_gold_per_minute"], 3)
        self.assertEqual(GuiSettings.from_config(enabled).early_stop_gold_per_minute, 3)
        disabled = save_settings(self.path, GuiSettings(early_stop_gold_per_minute=0))
        self.assertIsNone(disabled["search"]["early_stop_gold_per_minute"])
        self.assertEqual(disabled["search"]["max_stages"], 12)
        self.assertEqual(disabled["navigation"], self.original["navigation"])

    def test_worker_is_hidden_and_stop_is_written_after_launch(self):
        worker = WorkerManager(self.path)
        worker.directory.mkdir()
        worker.stop_path.write_text("stale")
        process = Mock()
        process.poll.return_value = None
        def spawn(command, **kwargs):
            self.assertFalse(worker.stop_path.exists())
            self.assertEqual(kwargs["cwd"], self.path.parent)
            self.assertEqual(kwargs["creationflags"], __import__("subprocess").CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.assertNotIn("shell", kwargs)
            self.assertFalse(kwargs["stdout"].closed)
            return process
        with patch("farmbot.gui.subprocess.Popen", side_effect=spawn) as popen:
            worker.start(GuiSettings())
            self.assertTrue(worker.request_stop())
            self.assertTrue(worker.stop_path.is_file())
            self.assertTrue(worker.stopping)
            with self.assertRaises(RuntimeError):
                worker.start(GuiSettings())
            popen.assert_called_once()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_stop_request_created_during_popen_is_never_erased(self):
        worker = WorkerManager(self.path)
        process = Mock()
        process.poll.return_value = None
        def spawn(*args, **kwargs):
            worker.stop_path.write_text("immediate stop")
            return process
        with patch("farmbot.gui.subprocess.Popen", side_effect=spawn):
            worker.start(GuiSettings())
        self.assertEqual(worker.stop_path.read_text(), "immediate stop")

    def test_other_active_bot_prevents_launch_and_keeps_its_stop_request(self):
        worker = WorkerManager(self.path)
        worker.directory.mkdir()
        worker.stop_path.write_text("stop active bot")
        with InstanceLock(worker.directory / "farmbot.lock"), patch("farmbot.gui.subprocess.Popen") as popen:
            with self.assertRaises(InstanceAlreadyRunning):
                worker.start(GuiSettings())
            popen.assert_not_called()
            self.assertEqual(worker.stop_path.read_text(), "stop active bot")

    def test_stopped_worker_can_restart_and_stop_does_not_touch_unowned_processes(self):
        worker = WorkerManager(self.path)
        self.assertFalse(worker.request_stop())
        self.assertFalse(worker.stop_path.exists())
        first, second = Mock(), Mock()
        first.poll.return_value = 0
        second.poll.return_value = None
        worker.process = first
        with patch("farmbot.gui.subprocess.Popen", return_value=second):
            worker.start(GuiSettings())
        self.assertIs(worker.process, second)
        first.kill.assert_not_called()

    def test_tail_is_bounded_and_missing_file_is_empty(self):
        path = self.path.parent / "log.txt"
        self.assertEqual(read_tail(path), "")
        path.write_text("old line\n" * 100 + "latest line\n", encoding="utf-8")
        tail = read_tail(path, 40)
        self.assertIn("latest line", tail)
        self.assertLessEqual(len(tail), 40)

    def test_activity_distinguishes_waiting_active_stopping_and_stopped(self):
        log = "Ciclo 1 iniciado\nPróximo ciclo em 120 segundos\n"
        self.assertEqual(activity_status(True, False, log), "Aguardando próximo ciclo")
        self.assertEqual(activity_status(True, False, log + "Ciclo 2 iniciado\n"), "Ativo")
        self.assertEqual(activity_status(True, True, log), "Parando…")
        self.assertEqual(activity_status(False, False, log), "Parado")


if __name__ == "__main__":
    unittest.main()
