"""Local Tk control panel; imports and settings helpers need no display."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .instance import InstanceLock


def _number(value, label: str, *, integer: bool = False, allow_zero: bool = False) -> float | int:
    kind = "número não negativo e finito" if allow_zero else "número positivo e finito"
    if integer:
        kind = "inteiro não negativo" if allow_zero else "inteiro positivo"
    if isinstance(value, bool):
        raise ValueError(f"{label}: informe um {kind}.")
    try:
        number = float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: informe um {kind}.") from exc
    if (not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero)
            or (integer and not number.is_integer())):
        raise ValueError(f"{label}: informe um {kind}.")
    return int(number) if integer else number


@dataclass(frozen=True)
class GuiSettings:
    interval_minutes: float = 5.0
    battles_per_stage: int = 5
    battle_restart_seconds: float = 0.0
    max_stages: int = 1
    battle_timeout: float = 180.0
    cache_hours: float = 6.0
    min_win_rate_percent: float = 80.0
    early_stop_gold_per_minute: float = 0.0
    auto_switch: bool = False
    dry_run: bool = True
    focus_on_start: bool = False

    @classmethod
    def from_values(cls, values: dict) -> GuiSettings:
        defaults = asdict(cls())
        values = {**defaults, **values}
        parsed = {
            "interval_minutes": _number(values["interval_minutes"], "Intervalo"),
            "battles_per_stage": _number(values["battles_per_stage"], "Batalhas por fase", integer=True),
            "battle_restart_seconds": _number(values["battle_restart_seconds"], "Reinício entre batalhas", allow_zero=True),
            "max_stages": _number(values["max_stages"], "Fases por rodada", integer=True),
            "battle_timeout": _number(values["battle_timeout"], "Limite por batalha"),
            "cache_hours": _number(values["cache_hours"], "Validade dos resultados"),
            "min_win_rate_percent": _number(values["min_win_rate_percent"], "Taxa mínima de sucesso"),
            "early_stop_gold_per_minute": _number(values["early_stop_gold_per_minute"], "Queda para trocar", allow_zero=True),
        }
        if not math.isfinite(parsed["interval_minutes"] * 60):
            raise ValueError("Intervalo muito grande.")
        if parsed["min_win_rate_percent"] > 100:
            raise ValueError("Taxa mínima de sucesso deve ser maior que 0 e até 100%.")
        for key in ("auto_switch", "dry_run", "focus_on_start"):
            if not isinstance(values[key], bool):
                raise ValueError(f"Opção inválida: {key}.")
            parsed[key] = values[key]
        if parsed["dry_run"] and parsed["auto_switch"]:
            raise ValueError("Desmarque a troca automática enquanto usar simulação sem cliques.")
        return cls(**parsed)

    @classmethod
    def from_config(cls, config: dict) -> GuiSettings:
        measurement, search = config.get("measurement", {}), config.get("search", {})
        loop, ui = config.get("loop", {}), config.get("ui", {})
        early_stop = search.get("early_stop_gold_per_minute")
        return cls.from_values({
            "interval_minutes": loop.get("interval_minutes", 5),
            "battles_per_stage": measurement.get("battles_per_stage", 5),
            "battle_restart_seconds": measurement.get("battle_restart_seconds", 0),
            "max_stages": loop.get("max_stages", 1),
            "battle_timeout": measurement.get("battle_timeout", 180),
            "cache_hours": search.get("cache_hours", 6),
            "min_win_rate_percent": measurement.get("min_win_rate", 0.8) * 100,
            "early_stop_gold_per_minute": 0 if early_stop is None else early_stop,
            "auto_switch": config.get("auto_switch_best", False),
            "dry_run": config.get("dry_run", True),
            "focus_on_start": ui.get("focus_on_start", False),
        })


def read_config(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("A configuração deve ser um objeto JSON.")
    return value


def save_settings(path: str | Path, settings: GuiSettings) -> dict:
    """Preserve current calibration and unknown fields, then replace atomically."""
    path = Path(path).resolve()
    settings = GuiSettings.from_values(asdict(settings))
    current = read_config(path)
    updated = {key: copy.deepcopy(value) for key, value in current.items() if not key.startswith("_")}
    for name in ("measurement", "search", "loop", "ui"):
        if not isinstance(updated.setdefault(name, {}), dict):
            raise ValueError(f"Seção inválida na configuração: {name}.")
    updated["dry_run"] = settings.dry_run
    updated["auto_switch_best"] = settings.auto_switch
    updated["measurement"].update(battles_per_stage=settings.battles_per_stage,
                                  battle_restart_seconds=settings.battle_restart_seconds,
                                  battle_timeout=settings.battle_timeout,
                                  min_win_rate=settings.min_win_rate_percent / 100)
    updated["search"]["cache_hours"] = settings.cache_hours
    updated["search"]["early_stop_gold_per_minute"] = settings.early_stop_gold_per_minute or None
    updated["loop"].update(interval_minutes=settings.interval_minutes, max_stages=settings.max_stages)
    updated["ui"]["focus_on_start"] = settings.focus_on_start
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.stem}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return updated


def build_command(config_path: str | Path, settings: GuiSettings, python: str | None = None) -> list[str]:
    settings = GuiSettings.from_values(asdict(settings))
    executable = Path(python or sys.executable)
    if executable.name.lower() == "pythonw.exe" and executable.with_name("python.exe").is_file():
        executable = executable.with_name("python.exe")
    command = [str(executable), "-u", "-X", "utf8", "-m", "farmbot", "--config", str(Path(config_path).resolve())]
    if settings.focus_on_start:
        command.append("--focus")
    command.extend(["loop", "--interval-minutes", str(settings.interval_minutes), "--max-stages", str(settings.max_stages)])
    if not settings.dry_run:
        command.append("--execute")
    if settings.auto_switch:
        command.append("--auto-switch")
    return command


def read_tail(path: Path, limit: int = 48000) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            start = max(0, stream.tell() - limit)
            stream.seek(start)
            value = stream.read(limit).decode("utf-8", errors="replace")
        return value.partition("\n")[2] if start and "\n" in value else value
    except OSError:
        return ""


def activity_status(running: bool, stopping: bool, log: str) -> str:
    if not running:
        return "Parado"
    if stopping:
        return "Parando…"
    for line in reversed(log.splitlines()):
        if "Próximo ciclo em" in line:
            return "Aguardando próximo ciclo"
        if "Ciclo " in line and " iniciado" in line:
            break
    return "Ativo"


class WorkerManager:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).resolve()
        self.directory = self.config_path.parent / "data"
        self.stop_path = self.directory / "farmbot.stop"
        self.stdout_path = self.directory / "gui.stdout.log"
        self.stderr_path = self.directory / "gui.stderr.log"
        self.process = None
        self.stopping = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, settings: GuiSettings) -> None:
        if self.running:
            raise RuntimeError("O bot já está em execução nesta janela.")
        command = build_command(self.config_path, settings)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Never erase a stop request while another active bot owns this lock.
        # Release this probe before the worker acquires its own lifetime lock.
        with InstanceLock(self.directory / "farmbot.lock"):
            self.stop_path.unlink(missing_ok=True)
        with self.stdout_path.open("wb") as stdout, self.stderr_path.open("wb") as stderr:
            self.process = subprocess.Popen(
                command, cwd=self.config_path.parent, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        self.stopping = False

    def request_stop(self) -> bool:
        if not self.running:
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text("Parada solicitada pela interface.\n", encoding="utf-8")
        self.stopping = True
        return True


def launch_gui(config_path: str | Path, auto_start: bool = False) -> int:
    # Lazy imports keep validation, CLI tests and the headless bot independent
    # from a graphical session and from Tk availability.
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    config_path = Path(config_path).resolve()
    config = read_config(config_path)
    initial = GuiSettings.from_config(config)
    root = tk.Tk()
    root.title("My Party — Controle do Farm Bot")
    root.geometry("900x680+40+40")
    root.minsize(800, 620)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
    style.configure("Status.TLabel", font=("Segoe UI", 12, "bold"))
    outer = ttk.Frame(root, padding=18)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Controle do farm", style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, text="Ajuste os ciclos e acompanhe os resultados. ESC também interrompe o bot.").pack(anchor="w", pady=(3, 12))

    manager = WorkerManager(config_path)
    variables = {}
    for key, value in asdict(initial).items():
        variables[key] = tk.BooleanVar(root, value=value) if isinstance(value, bool) else tk.StringVar(root, value=f"{value:g}")
    form = ttk.LabelFrame(outer, text="Configurações", padding=12)
    form.pack(fill="x")
    fields = [("interval_minutes", "Intervalo (minutos)"), ("battles_per_stage", "Batalhas por fase"),
              ("max_stages", "Fases por rodada"), ("battle_timeout", "Limite por batalha (s)"),
              ("cache_hours", "Validade dos resultados (h)"), ("min_win_rate_percent", "Sucesso mínimo (%)"),
              ("early_stop_gold_per_minute", "Queda para trocar (Gold/min)"),
              ("battle_restart_seconds", "Reinício entre batalhas (s)")]
    for index, (key, label) in enumerate(fields):
        row, column = divmod(index, 3)
        cell = ttk.Frame(form)
        cell.grid(row=row, column=column, sticky="ew", padx=(0, 18), pady=(0, 8))
        ttk.Label(cell, text=label).pack(anchor="w")
        ttk.Entry(cell, textvariable=variables[key], width=22).pack(fill="x", pady=(3, 0))
        form.columnconfigure(column, weight=1)
    for column, (key, label) in enumerate((("dry_run", "Simulação (sem cliques)"),
                                          ("auto_switch", "Trocar para a melhor fase"),
                                          ("focus_on_start", "Focar o jogo ao iniciar"))):
        ttk.Checkbutton(form, text=label, variable=variables[key]).grid(row=3, column=column, sticky="w", pady=(3, 2))
    ttk.Label(form, text="Queda para trocar: 0 desativa a troca antecipada.").grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
    ttk.Label(outer, text="Alterações são aplicadas ao iniciar. Para ajustar um bot ativo, pare e inicie novamente.").pack(anchor="w", pady=(7, 10))

    actions = ttk.Frame(outer)
    actions.pack(fill="x")
    start_button = ttk.Button(actions, text="▶ Iniciar", width=15)
    stop_button = ttk.Button(actions, text="■ Parar", width=15)
    save_button = ttk.Button(actions, text="Salvar ajustes", width=17)
    start_button.pack(side="left", padx=(0, 7))
    stop_button.pack(side="left", padx=(0, 7))
    save_button.pack(side="left")
    status = tk.StringVar(root, value="Parado")
    ttk.Label(actions, textvariable=status, style="Status.TLabel").pack(side="right")
    notice = tk.StringVar(root, value="Pronto. Mantenha a janela do jogo visível durante a execução.")
    ttk.Label(outer, textvariable=notice, wraplength=850).pack(anchor="w", pady=(8, 8))

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)
    log_frame = ttk.Frame(notebook)
    ranking_frame = ttk.Frame(notebook, padding=6)
    notebook.add(log_frame, text="Atividade")
    notebook.add(ranking_frame, text="Ranking")
    log_text = ScrolledText(log_frame, wrap="word", font=("Consolas", 9), state="disabled", height=13)
    log_text.pack(fill="both", expand=True)
    columns = ("stage", "difficulty", "gold_per_minute", "gold_per_hour", "samples", "reliable")
    ranking = ttk.Treeview(ranking_frame, columns=columns, show="headings", height=10)
    for key, title, width in zip(columns, ("Fase", "Dificuldade", "Gold/min", "Gold/h", "Amostras", "Confiável"), (70, 160, 120, 130, 90, 90)):
        ranking.heading(key, text=title)
        ranking.column(key, width=width, anchor="w" if key in ("stage", "difficulty") else "e")
    ranking.pack(fill="both", expand=True)
    ttk.Label(ranking_frame, text="Resultados observados no jogo; a tabela atualiza após a exportação do ranking.").pack(anchor="w", pady=(6, 0))

    closing = False
    last_text = None
    last_rank_stamp = None
    reported_process = None

    def settings() -> GuiSettings:
        return GuiSettings.from_values({key: variable.get() for key, variable in variables.items()})

    def save() -> bool:
        nonlocal config
        try:
            config = save_settings(config_path, settings())
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Confira os ajustes", str(exc), parent=root)
            return False
        notice.set("Ajustes salvos. Eles serão usados ao iniciar o bot.")
        return True

    def start() -> None:
        nonlocal last_text, reported_process
        if manager.running or not save():
            return
        try:
            manager.start(settings())
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("Não foi possível iniciar", str(exc), parent=root)
            return
        last_text = None
        reported_process = None
        notice.set("Bot iniciado. Use Parar ou ESC para encerrar com segurança.")
        status.set("Ativo")

    def stop() -> None:
        try:
            if manager.request_stop():
                status.set("Parando…")
                notice.set("Parada solicitada. Aguardando o bot salvar os resultados e encerrar.")
        except OSError as exc:
            messagebox.showerror("Falha ao solicitar parada", str(exc), parent=root)

    def close() -> None:
        nonlocal closing
        if manager.running:
            closing = True
            stop()
            notice.set("Encerrando o bot antes de fechar esta janela. Aguarde a operação em andamento.")
        else:
            root.destroy()

    def refresh() -> None:
        nonlocal last_text, last_rank_stamp, reported_process
        running = manager.running
        errors = read_tail(manager.stderr_path)
        output = read_tail(manager.stdout_path)
        text = errors + ("\n\n" + output if output else "")
        if text != last_text:
            follow = log_text.yview()[1] >= .98
            log_text.configure(state="normal")
            log_text.delete("1.0", "end")
            log_text.insert("end", text or "As mensagens do bot aparecerão aqui.")
            log_text.configure(state="disabled")
            if follow:
                log_text.see("end")
            last_text = text
        status.set(activity_status(running, manager.stopping, errors))
        start_button.configure(state="disabled" if running or closing else "normal")
        stop_button.configure(state="normal" if running and not manager.stopping else "disabled")
        if manager.process is not None and not running and reported_process is not manager.process:
            reported_process = manager.process
            code = manager.process.poll()
            notice.set("Bot parado. Resultados preservados." if code in (0, 130) else f"O bot encerrou com código {code}. Veja o motivo na aba Atividade.")
        destination = Path(config.get("output", {}).get("ranking", "data/ranking.csv"))
        if not destination.is_absolute():
            destination = config_path.parent / destination
        try:
            stamp = (destination.stat().st_mtime_ns, destination.stat().st_size)
            if stamp != last_rank_stamp:
                with destination.open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))[:200]
                ranking.delete(*ranking.get_children())
                for row in rows:
                    values = [row.get(key, "") for key in columns]
                    for index in (2, 3):
                        try:
                            values[index] = f"{float(values[index]):,.2f}"
                        except (TypeError, ValueError):
                            pass
                    values[-1] = "Sim" if str(values[-1]).lower() == "true" else "Não"
                    ranking.insert("", "end", values=values)
                last_rank_stamp = stamp
        except (OSError, csv.Error, UnicodeError):
            pass  # A writer may be replacing the CSV; retry next refresh.
        if closing and not running:
            root.destroy()
            return
        root.after(400, refresh)

    start_button.configure(command=start)
    stop_button.configure(command=stop)
    save_button.configure(command=save)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, refresh)
    if auto_start:
        root.after(500, start)
    root.mainloop()
    return 0
