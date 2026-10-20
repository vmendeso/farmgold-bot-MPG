import json
from pathlib import Path
import tempfile
import unittest

from farmbot.config import ConfigError, load_config


class EarlyStopConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.config = {
            "dry_run": True, "auto_switch_best": False,
            "window": {}, "vision": {}, "navigation": {},
            "measurement": {
                "battles_per_stage": 5, "poll_interval": .5, "battle_timeout": 180,
                "stage_timeout": 1200, "max_log_gap": 120,
                "log_order": "oldest_first", "logs_roi": [0, 0, 1, .3],
            },
            "search": {"max_stages": 12, "cache_hours": 6, "exploration_every": 3},
            "output": {key: f"data/{key}" for key in ("database", "ranking", "catalog", "artifacts", "log")},
            "safety": {"start_delay": 5},
        }

    def load(self):
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        return load_config(self.path)

    def test_omitted_or_null_threshold_is_disabled(self):
        self.assertIsNone(self.load()["search"].get("early_stop_gold_per_minute"))
        self.config["search"]["early_stop_gold_per_minute"] = None
        self.assertIsNone(self.load()["search"]["early_stop_gold_per_minute"])

    def test_positive_finite_threshold_preserves_gold_per_minute_units(self):
        for value in (3, 2.5):
            with self.subTest(value=value):
                self.config["search"]["early_stop_gold_per_minute"] = value
                self.assertEqual(self.load()["search"]["early_stop_gold_per_minute"], value)

    def test_invalid_threshold_fails_configuration_validation(self):
        for value in (True, False, 0, -1, "3", float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value):
                self.config["search"]["early_stop_gold_per_minute"] = value
                with self.assertRaisesRegex(ConfigError, "search.early_stop_gold_per_minute"):
                    self.load()

    def test_restart_seconds_default_zero_and_accepts_nonnegative_finite_numbers(self):
        self.assertEqual(self.load()["measurement"].get("battle_restart_seconds", 0), 0)
        for value in (0, 5, 2.5):
            self.config["measurement"]["battle_restart_seconds"] = value
            self.assertEqual(self.load()["measurement"]["battle_restart_seconds"], value)

    def test_restart_seconds_rejects_invalid_values(self):
        for value in (True, False, -1, "5", None, float("inf"), float("nan")):
            with self.subTest(value=value):
                self.config["measurement"]["battle_restart_seconds"] = value
                with self.assertRaisesRegex(ConfigError, "measurement.battle_restart_seconds"):
                    self.load()

    def test_fractional_and_boolean_collection_budgets_are_rejected(self):
        for section, name in (("measurement", "battles_per_stage"), ("search", "max_stages"),
                              ("search", "exploration_every")):
            previous = self.config[section][name]
            for value in (True, False, 1.5, 0, -1, "5"):
                with self.subTest(field=name, value=value):
                    self.config[section][name] = value
                    with self.assertRaisesRegex(ConfigError, section + "." + name):
                        self.load()
            self.config[section][name] = previous

    def test_nonfinite_or_nonpositive_time_limits_cannot_start_a_run(self):
        for name in ("poll_interval", "battle_timeout", "stage_timeout", "max_log_gap"):
            previous = self.config["measurement"][name]
            for value in (True, 0, -1, float("inf"), float("nan")):
                with self.subTest(field=name, value=value):
                    self.config["measurement"][name] = value
                    with self.assertRaisesRegex(ConfigError, "measurement." + name):
                        self.load()
            self.config["measurement"][name] = previous

    def test_invalid_log_regions_and_order_are_rejected_before_capture(self):
        for roi in ([0, 0, 0, .3], [-.1, 0, .5, .3], [.9, 0, .5, .3],
                    [0, .9, .5, .3], [0, 0, float("nan"), .3], [False, 0, 1, .3], [0, 0, 1]):
            with self.subTest(roi=roi):
                self.config["measurement"]["logs_roi"] = roi
                with self.assertRaisesRegex(ConfigError, "logs_roi"):
                    self.load()
        self.config["measurement"]["logs_roi"] = [0, 0, 1, .3]
        self.config["measurement"]["log_order"] = "bottom"
        with self.assertRaisesRegex(ConfigError, "log_order"):
            self.load()

    def test_zero_start_delay_is_valid_but_negative_and_nonfinite_delays_are_not(self):
        self.config["safety"]["start_delay"] = 0
        self.assertEqual(self.load()["safety"]["start_delay"], 0)
        for value in (-1, True, float("inf"), float("nan")):
            with self.subTest(value=value):
                self.config["safety"]["start_delay"] = value
                with self.assertRaisesRegex(ConfigError, "start_delay"):
                    self.load()


if __name__ == "__main__":
    unittest.main()
