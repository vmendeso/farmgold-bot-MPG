from __future__ import annotations

import json
import math
from pathlib import Path


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    with path.open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    for section in ("window", "vision", "navigation", "measurement", "search", "output", "safety"):
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"Seção obrigatória ausente: {section}")
    for key in ("dry_run", "auto_switch_best"):
        if not isinstance(config.get(key), bool):
            raise ConfigError(f"{key} deve ser true ou false")
    for section, fields in {
        "measurement": ["battles_per_stage", "poll_interval", "battle_timeout", "stage_timeout", "max_log_gap"],
        "search": ["max_stages", "cache_hours", "exploration_every"],
    }.items():
        for field in fields:
            value = config[section].get(field)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ConfigError(f"{section}.{field} deve ser positivo")
    for section, field in (("measurement", "battles_per_stage"), ("search", "max_stages"), ("search", "exploration_every")):
        if not isinstance(config[section][field], int):
            raise ConfigError(f"{section}.{field} deve ser inteiro")
    early_stop = config["search"].get("early_stop_gold_per_minute")
    if early_stop is not None and (
        isinstance(early_stop, bool) or not isinstance(early_stop, (float, int))
        or not math.isfinite(early_stop) or early_stop <= 0
    ):
        raise ConfigError("search.early_stop_gold_per_minute deve ser positivo e finito, ou null para desativar")
    restart = config["measurement"].get("battle_restart_seconds", 0)
    if (isinstance(restart, bool) or not isinstance(restart, (float, int))
            or not math.isfinite(restart) or restart < 0):
        raise ConfigError("measurement.battle_restart_seconds deve ser não negativo e finito")
    retries = config["measurement"].get("max_ocr_failures", 5)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise ConfigError("measurement.max_ocr_failures deve ser inteiro positivo")
    delay = config["safety"].get("start_delay")
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
        raise ConfigError("safety.start_delay deve ser finito e não negativo")
    if config["safety"].get("emergency_key", "esc").lower() != "esc":
        raise ConfigError("A tecla de emergência implementada é ESC")
    for name in ("database", "ranking", "catalog", "artifacts", "log"):
        if not isinstance(config["output"].get(name), str) or not config["output"][name].strip():
            raise ConfigError(f"output.{name} deve ser um caminho não vazio")
    rate = config["measurement"].get("min_win_rate", .8)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
        raise ConfigError("measurement.min_win_rate deve estar entre 0 (exclusivo) e 1")
    if config["measurement"].get("log_order") not in ("newest_first", "oldest_first"):
        raise ConfigError("measurement.log_order inválido")
    roi = config["measurement"].get("logs_roi")
    if (not isinstance(roi, list) or len(roi) != 4
            or not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in roi)
            or min(roi) < 0 or roi[2] <= 0 or roi[3] <= 0
            or roi[0] + roi[2] > 1.001 or roi[1] + roi[3] > 1.001):
        raise ConfigError("measurement.logs_roi deve ser [x,y,largura,altura] proporcional à janela")
    config["_base_dir"] = path.parent
    from .groq_ocr import settings
    try:
        settings(config['vision'].get('groq'))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    config["_path"] = path
    return config


def output_path(config: dict, name: str) -> Path:
    path = Path(config["output"][name])
    return path if path.is_absolute() else config["_base_dir"] / path
