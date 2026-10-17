import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from farmbot.cli import main, parser
from farmbot.models import Stage


class SearchBudgetArgumentTests(unittest.TestCase):
    def test_run_accepts_positive_budget_and_preserves_other_run_options(self):
        args = parser().parse_args(["--focus", "run", "--execute", "--max-stages", "2"])
        self.assertEqual(args.max_stages, 2)
        self.assertTrue(args.focus)
        self.assertTrue(args.execute)

    def test_omitted_budget_keeps_configuration_authoritative(self):
        self.assertIsNone(parser().parse_args(["run"]).max_stages)

    def test_nonpositive_or_noninteger_budget_fails_before_runtime(self):
        for value in ("0", "-1", "1.5", "nan", "abc"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser().parse_args(["run", "--max-stages", value])
                self.assertEqual(raised.exception.code, 2)

    def test_measurement_budget_is_only_a_run_option(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(["observe", "--max-stages", "2"])

    def test_runtime_override_reaches_search_without_rewriting_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.json"
            original_contents = '{"search":{"max_stages":12}}\n'
            source.write_text(original_contents, encoding="utf-8")
            config = {
                "_base_dir": Path(directory), "_path": source,
                "search": {"max_stages": 12}, "dry_run": True,
                "auto_switch_best": False, "vision": {}, "window": {},
                "navigation": {}, "safety": {"start_delay": 0},
                "output": {"database": "results.sqlite3"},
            }
            with patch("farmbot.cli.load_config", return_value=config), \
                    patch("farmbot.cli._logging"), \
                    patch("farmbot.vision.Vision"), \
                    patch("farmbot.desktop.WindowManager"), \
                    patch("farmbot.desktop.SafeInput"), \
                    patch("farmbot.navigation.Navigator"), \
                    patch("farmbot.safety.StopController"), \
                    patch("farmbot.cli.run_search") as search, \
                    contextlib.redirect_stdout(io.StringIO()):
                result = main(["--config", str(source), "run", "--max-stages", "2"])
            self.assertEqual(result, 0)
            self.assertEqual(search.call_args.args[5]["search"]["max_stages"], 2)
            self.assertEqual(source.read_text(encoding="utf-8"), original_contents)


class BattleCountArgumentTests(unittest.TestCase):
    def test_run_accepts_one_battle_with_five_stage_budget(self):
        args = parser().parse_args(["run", "--execute", "--max-stages", "5", "--battles-per-stage", "1"])
        self.assertEqual((args.max_stages, args.battles_per_stage), (5, 1))
        self.assertTrue(args.execute)
        self.assertIsNone(parser().parse_args(["run"]).battles_per_stage)

    def test_invalid_battle_counts_fail_before_runtime(self):
        for value in ("0", "-1", "1.5", "nan", "abc"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser().parse_args(["run", "--battles-per-stage", value])
                self.assertEqual(raised.exception.code, 2)

    def test_battle_count_override_is_only_a_run_option(self):
        for command in ("observe", "loop"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser().parse_args([command, "--battles-per-stage", "1"])

    def test_search_receives_override_or_saved_count_without_rewriting_config(self):
        for arguments, expected_count in (([], 5), (["--battles-per-stage", "1"], 1)):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "config.json"
                original_contents = '{"measurement":{"battles_per_stage":5}}\n'
                source.write_text(original_contents, encoding="utf-8")
                config = {
                    "_base_dir": Path(directory), "_path": source,
                    "search": {"max_stages": 12}, "dry_run": True,
                    "measurement": {"battles_per_stage": 5, "min_win_rate": .8},
                    "auto_switch_best": False, "vision": {}, "window": {},
                    "navigation": {}, "safety": {"start_delay": 0},
                    "output": {"database": "results.sqlite3"},
                }
                with patch("farmbot.cli.load_config", return_value=config), \
                        patch("farmbot.cli._logging"), \
                        patch("farmbot.vision.Vision"), \
                        patch("farmbot.desktop.WindowManager"), \
                        patch("farmbot.desktop.SafeInput"), \
                        patch("farmbot.navigation.Navigator"), \
                        patch("farmbot.safety.StopController"), \
                        patch("farmbot.cli.run_search") as search, \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = main(["--config", str(source), "run", "--max-stages", "5", *arguments])
                self.assertEqual(result, 0)
                runtime_config = search.call_args.args[5]
                self.assertEqual(runtime_config["measurement"]["battles_per_stage"], expected_count)
                self.assertEqual(runtime_config["measurement"]["min_win_rate"], .8)
                self.assertEqual(runtime_config["search"]["max_stages"], 5)
                self.assertEqual(source.read_text(encoding="utf-8"), original_contents)


class StageSelectionArgumentTests(unittest.TestCase):
    def test_selection_parses_exact_target_with_optional_focus_and_execute(self):
        args = parser().parse_args(["--focus", "select", "--stage", "4-7",
                                   "--difficulty", "Pesadelo", "--execute"])
        self.assertEqual((args.stage, args.difficulty), ("4-7", "Pesadelo"))
        self.assertTrue(args.focus)
        self.assertTrue(args.execute)

    def test_selection_without_execute_preserves_default_dry_run(self):
        args = parser().parse_args(["select", "--stage", "4-7", "--difficulty", "Pesadelo"])
        self.assertFalse(args.execute)

    def test_invalid_stage_or_missing_difficulty_fails_before_runtime(self):
        invalid = [
            ["select", "--stage", "4-7"],
            ["select", "--stage", "0-7", "--difficulty", "Pesadelo"],
            ["select", "--stage", "4/7", "--difficulty", "Pesadelo"],
            ["select", "--stage", "4-7", "--difficulty", ""],
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser().parse_args(arguments)
                self.assertEqual(raised.exception.code, 2)

    def test_selection_dispatches_to_navigator_without_collection_or_storage(self):
        for execute in (False, True):
            with self.subTest(execute=execute):
                directory = tempfile.TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                config = {
                    "_base_dir": Path(directory.name), "dry_run": True, "auto_switch_best": False,
                    "vision": {}, "window": {}, "navigation": {}, "safety": {"start_delay": 0},
                }
                arguments = ["select", "--stage", "4-7", "--difficulty", "Pesadelo"]
                if execute:
                    arguments.append("--execute")
                with patch("farmbot.cli.load_config", return_value=config), \
                        patch("farmbot.cli._logging"), \
                        patch("farmbot.vision.Vision"), \
                        patch("farmbot.desktop.WindowManager"), \
                        patch("farmbot.desktop.SafeInput") as inputs, \
                        patch("farmbot.navigation.Navigator") as navigation, \
                        patch("farmbot.safety.StopController"), \
                        patch("farmbot.cli.Collector") as collector, \
                        patch("farmbot.cli.Store") as store, \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = main(arguments)
                self.assertEqual(result, 0)
                self.assertEqual(inputs.call_args.kwargs["dry_run"], not execute)
                navigation.return_value.select.assert_called_once_with(Stage("4-7", "Pesadelo"))
                collector.assert_not_called()
                store.assert_not_called()


class LoopArgumentTests(unittest.TestCase):
    def test_loop_defaults_to_five_minutes_and_one_new_stage(self):
        args = parser().parse_args(["loop"])
        self.assertEqual((args.interval_minutes, args.max_stages), (5, 1))
        self.assertFalse(args.execute)
        self.assertFalse(args.auto_switch)

    def test_invalid_intervals_fail_before_desktop_access(self):
        for value in ("0", "-1", "nan", "inf", "1e309", "abc"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser().parse_args(["loop", "--interval-minutes", value])

    def test_loop_dispatches_fresh_cycle_handler_and_shared_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "_base_dir": Path(directory), "search": {"max_stages": 12},
                "dry_run": True, "auto_switch_best": False, "vision": {}, "window": {},
                "navigation": {}, "safety": {"start_delay": 0},
                "output": {"database": "results.sqlite3", "ranking": "ranking.csv"},
            }

            def two_rounds(callback, stop, interval_seconds):
                self.assertEqual(interval_seconds, 300)
                callback()
                callback()

            with patch("farmbot.cli.load_config", return_value=config), \
                    patch("farmbot.cli._logging"), \
                    patch("farmbot.vision.Vision"), \
                    patch("farmbot.desktop.WindowManager", autospec=True) as window, \
                    patch("farmbot.desktop.SafeInput"), \
                    patch("farmbot.navigation.Navigator"), \
                    patch("farmbot.safety.StopController") as controller, \
                    patch("farmbot.loop.run_loop", side_effect=two_rounds) as loop, \
                    patch("farmbot.continuous.SearchCycle") as cycle, \
                    patch("farmbot.cli.recent_stats", return_value=[]), \
                    patch("farmbot.cli.export_ranking"), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = main(["--focus", "loop", "--execute"])
            self.assertEqual(result, 0)
            self.assertEqual(cycle.return_value.call_count, 2)
            self.assertEqual(config["search"]["max_stages"], 1)
            self.assertEqual(cycle.call_args.kwargs, {"dry_run": False, "focus": True})
            window.assert_not_called()
            self.assertIs(loop.call_args.args[1], controller.return_value.__enter__.return_value)


class ControlCommandTests(unittest.TestCase):
    def test_gui_dispatch_does_not_touch_game_or_start_loop_in_cli(self):
        with patch("farmbot.gui.launch_gui", return_value=0) as launch, \
                patch("farmbot.cli.load_config") as config:
            self.assertEqual(main(["--config", "config.json", "gui", "--start"]), 0)
        launch.assert_called_once_with("config.json", auto_start=True)
        config.assert_not_called()

    def test_stop_signals_only_an_active_instance_without_window_access(self):
        from farmbot.instance import InstanceLock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"_base_dir": root}
            with patch("farmbot.cli.load_config", return_value=config), \
                    patch("farmbot.cli._logging"), \
                    patch("farmbot.desktop.WindowManager") as window, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["stop"]), 0)
                self.assertFalse((root / "data" / "farmbot.stop").exists())
                with InstanceLock(root / "data" / "farmbot.lock"):
                    self.assertEqual(main(["stop"]), 0)
                    self.assertTrue((root / "data" / "farmbot.stop").exists())
            window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
