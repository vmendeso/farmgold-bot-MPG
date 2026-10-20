"""Live measurement orchestration. All persisted victories originate in visible logs."""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from .config import output_path
from .models import BattleRecord, ParsedBattle, Stage, UntimedDefeat, utc_now
from .navigation import NavigationError
from .results import LogTracker, parse_log_entry
from .search import SearchPlanner
from .profitability import profitability_drop, recent_profitability
from .recovery import failed_stages, nearest_lower_stage
from .storage import Store, export_ranking

log = logging.getLogger(__name__)


def recent_stats(store: Store, config: dict):
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=config["search"]["cache_hours"])).isoformat()
    return recent_profitability(store.records(since=since),
                                battles_per_stage=config["measurement"]["battles_per_stage"],
                                min_win_rate=config["measurement"]["min_win_rate"],
                                battle_restart_seconds=config["measurement"].get("battle_restart_seconds", 0),
                                cache_hours=config["search"]["cache_hours"], now=now)


def profit_drop(target, measurements, config, catalog):
    return profitability_drop(target, measurements,
                              threshold=config["search"].get("early_stop_gold_per_minute"),
                              battles_per_stage=config["measurement"]["battles_per_stage"],
                              min_win_rate=config["measurement"]["min_win_rate"],
                              cache_hours=config["search"]["cache_hours"], accessible=catalog)


def log_profit_drop(decision, reason):
    log.info("%s: %s %.2f Gold/min; referência %s %.2f Gold/min; diferença %.2f Gold/min",
             reason, decision.candidate.target.key, decision.candidate.gold_per_minute,
             decision.reference.target.key, decision.reference.gold_per_minute, decision.difference)


def read_results(vision, image, measurement: dict, diagnostics: dict | None = None):
    """Read a complete log snapshot; optional diagnostics never contain OCR text."""
    from .vision import VisionError

    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics.clear()
    if "logs_anchor" not in measurement:
        roi = measurement["logs_roi"]
    else:
        anchor = measurement["logs_anchor"]
        if not isinstance(anchor, dict) or not isinstance(anchor.get("target"), dict):
            raise ValueError("logs_anchor must include a visual target")
        reference_width = anchor.get("reference_width")
        offsets = anchor.get("offset")
        if (isinstance(reference_width, bool) or not isinstance(reference_width, (int, float))
                or not math.isfinite(reference_width) or reference_width <= 0
                or not isinstance(offsets, (list, tuple)) or len(offsets) != 4
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in offsets)
                or offsets[2] <= 0 or offsets[3] <= 0):
            raise ValueError("logs_anchor needs positive reference_width and [dx,dy,width,height] offsets")
        match = vision.locate(image, anchor["target"])
        if match is None:
            diagnostics["cause"] = "missing_anchor"
            log.debug("Âncora visual do log não encontrada")
            return None
        scale = match.width / reference_width
        left, top = match.x + offsets[0] * scale, match.y + offsets[1] * scale
        width, height = offsets[2] * scale, offsets[3] * scale
        image_height, image_width = image.shape[:2]
        if (not all(math.isfinite(value) for value in (left, top, width, height))
                or min(width, height) <= 0 or min(left, top) < 0
                or left + width > image_width or top + height > image_height):
            diagnostics["cause"] = "invalid_roi"
            log.debug("Região do log ancorada ultrapassa a imagem")
            return None
        roi = [left / image_width, top / image_height, width / image_width, height / image_height]
    diagnostics["roi"] = list(roi)
    try:
        lines = vision.read_lines(image, roi=roi, mode="logs")
    except VisionError:
        diagnostics["cause"] = "ocr_error"
        raise
    rows = [parse_log_entry(line.text, measurement.get("notification_patterns")) for line in lines]
    diagnostics["line_count"] = len(lines)
    # A partial read must not masquerade as a shift in the rolling log.
    if any(row is None for row in rows):
        diagnostics.update(cause="rejected_rows", rejected_lines=[
            {"line": index, "reason": "empty_or_unconfirmed" if not line.text.strip() else "unrecognized_format"}
            for index, (line, row) in enumerate(zip(lines, rows), 1) if row is None
        ])
        log.debug("OCR incompleto: %s", result_read_failure(diagnostics))
        return None
    return rows


def result_read_failure(diagnostics: dict) -> str:
    cause = diagnostics.get("cause")
    if cause == "missing_anchor":
        return "Âncora visual do log não encontrada; região do histórico não confirmada"
    if cause == "invalid_roi":
        return "Região ancorada do log ultrapassa a captura da janela"
    if cause == "rejected_rows":
        rejected = diagnostics["rejected_lines"]
        details = ", ".join(
            f"{row['line']} ({'vazia/sem consenso' if row['reason'] == 'empty_or_unconfirmed' else 'formato rejeitado'})"
            for row in rejected[:8]
        )
        if len(rejected) > 8:
            details += f", mais {len(rejected) - 8}"
        return f"Leitura incompleta do log: {len(rejected)}/{diagnostics['line_count']} linhas rejeitadas; linhas {details}"
    return "Leitura incompleta do log de batalhas"


def save_log_diagnostic(frame, config: dict, target: Stage, run_id: str, diagnostics: dict):
    """Persist only this rejected log crop, without capturing or moving the UI."""
    try:
        from .desktop import save_frame
        from .vision import _roi_box

        roi = diagnostics.get("roi")
        if roi is None:
            return None  # Never guess the log region or save the whole client.
        x, y, width, height = _roi_box(frame.image, roi)
        crop = frame.image[y:y + height, x:x + width]
        path = output_path(config, "artifacts") / f"ocr-log-{time.time_ns()}.png"
        save_frame(SimpleNamespace(image=crop), path)
        metadata = {key: diagnostics[key] for key in ("cause", "roi", "line_count", "rejected_lines")
                    if key in diagnostics}
        metadata.update(stage=target.stage, difficulty=target.difficulty, run_id=run_id,
                        crop_box=[x, y, width, height])
        path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        log.warning("Primeira falha OCR: recorte do histórico salvo em %s", path)
        return path
    except Exception as exc:
        log.warning("Não foi possível salvar o diagnóstico OCR: %s", exc)
        return None


def error_screenshot(window, config: dict, name: str = "error"):
    from .desktop import save_frame
    path = output_path(config, "artifacts") / f"{name}-{time.time_ns()}.png"
    try:
        save_frame(window.capture(), path)
        log.warning("Screenshot: %s", path)
    except Exception as exc:
        log.warning("Não foi possível capturar a janela: %s", exc)
        previous = getattr(window, "last_frame", None)
        if previous is not None:
            try:
                fallback = path.with_name(path.stem + "-last-successful-frame.png")
                save_frame(previous, fallback)
                log.warning("Última captura válida (anterior ao erro): %s", fallback)
            except Exception as fallback_error:
                log.warning("Falha ao salvar última captura: %s", fallback_error)


class StageDefeated(NavigationError):
    """A newly appended, twice-confirmed defeat ended the current measurement."""

    def __init__(self, target: Stage, completed: int, observed_stage: Stage, raw: str, *, timed: bool = False):
        self.target = target
        self.completed = completed
        self.observed_stage = observed_stage
        self.raw = raw
        self.timed = timed
        description = "Derrota confirmada" if timed else "Derrota confirmada sem duração"
        super().__init__(f"{description} no log: {raw}; medição encerrada")


def save_stage_diagnostic(navigator, config, target):
    """Save the actual OCR label frame, never a later screenshot of animation."""
    evidence = getattr(navigator, "last_label_evidence", None)
    if not isinstance(evidence, dict) or evidence.get("frame") is None:
        return None
    try:
        from .desktop import save_frame
        from .vision import _roi_box
        image = evidence["frame"].image
        x, y, w, h = _roi_box(image, evidence["roi"])
        path = output_path(config, "artifacts") / f"ocr-stage-{time.time_ns()}.png"
        save_frame(SimpleNamespace(image=image[y:y+h, x:x+w]), path)
        metadata = {key: evidence[key] for key in ("reads", "selected", "error") if key in evidence}
        metadata.update(expected=target.key, crop_box=[x, y, w, h])
        path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        log.warning("Diagnóstico da legenda no instante da leitura: %s", path)
        return path
    except Exception as exc:
        log.warning("Não foi possível salvar diagnóstico da legenda: %s", exc)
        return None


class Collector:
    def __init__(self, window, vision, navigator, stop, store: Store, config: dict, *,
                 comparison_catalog=None, stop_on_defeat: bool = False):
        self.window, self.vision, self.navigator = window, vision, navigator
        self.stop, self.store, self.config = stop, store, config
        self.comparison_catalog = comparison_catalog
        self.stop_on_defeat = stop_on_defeat

    def _profit_drop(self, target):
        if self.comparison_catalog is None:
            return None
        return profit_drop(target, recent_stats(self.store, self.config), self.config, self.comparison_catalog)

    def collect(self, target: Stage) -> int:
        from .vision import VisionError
        cfg = self.config["measurement"]
        decision = self._profit_drop(target)
        if decision is not None:
            log_profit_drop(decision, "Coleta dispensada pelo histórico")
            return 0
        run_id = str(uuid.uuid4())
        tracker = LogTracker(cfg["log_order"], cfg["max_log_gap"])
        start = last_result = last_update = time.monotonic()
        pending = None
        primed = False
        completed = 0
        ocr_failures = 0
        ocr_diagnostic_attempted = False
        label_diagnostic_attempted = False
        failure_reason = ""
        changed_stage = None
        changed_deadline = None
        changed_snapshots = 0
        confirmation_timeout = min(10.0, float(self.config["navigation"].get("timeout", 10.0)))
        log.info("Medição: %s / %s, %s batalhas", target.stage, target.difficulty, cfg["battles_per_stage"])
        try:
            while completed < cfg["battles_per_stage"]:
                self.stop.check()
                now = time.monotonic()
                if changed_deadline is not None and now >= changed_deadline:
                    raise NavigationError(f"Fase alterada sem derrota nova confirmada: {target} -> {changed_stage}")
                if now - last_result >= cfg["battle_timeout"] or now - start >= cfg["stage_timeout"]:
                    failure_reason = "Timeout sem resultado visual novo e válido"
                    break
                frame = None
                diagnostics = {}
                try:
                    visible = self.navigator.current_stage()
                    if visible is None:
                        raise VisionError("Legenda da fase não reconhecida")
                    if visible != target:
                        if (self.comparison_catalog is not None
                                and visible.difficulty == target.difficulty
                                and visible not in self.comparison_catalog):
                            raise VisionError(f"Legenda fora do catálogo confirmado: {visible.key}; nova leitura necessária")
                        # A defeat can make the game retreat before its log is
                        # read. Only a primed log and a confirmed lower stage in
                        # the same difficulty permit two fresh reads for proof.
                        lower = (visible.difficulty == target.difficulty
                                 and visible.position < target.position
                                 and (self.comparison_catalog is None or visible in self.comparison_catalog))
                        if not primed or not lower or (changed_stage is not None and visible != changed_stage):
                            raise NavigationError(f"Fase/dificuldade alterada: esperado {target}, observado {visible}")
                        if changed_stage is None:
                            changed_stage = visible
                            changed_deadline = time.monotonic() + confirmation_timeout
                            pending = None
                            log.info("Recuo observado para %s; confirmando derrota nova no histórico", visible.key)
                    elif changed_stage is not None:
                        raise NavigationError("Fase mudou novamente antes da confirmação da derrota")
                    frame = self.window.capture()
                    rows = read_results(self.vision, frame.image, cfg, diagnostics=diagnostics)
                    if rows is None:
                        raise VisionError(result_read_failure(diagnostics))
                except VisionError as exc:
                    ocr_failures += 1
                    pending = None
                    limit = cfg.get("max_ocr_failures", 5)
                    if ocr_failures == 1 or ocr_failures % 5 == 0 or ocr_failures >= limit:
                        log.warning("OCR não confirmado (%s/%s): %s", ocr_failures, limit, exc)
                    if frame is not None and diagnostics.get("roi") is not None and not ocr_diagnostic_attempted:
                        ocr_diagnostic_attempted = True
                        save_log_diagnostic(frame, self.config, target, run_id, diagnostics)
                    if frame is None and not label_diagnostic_attempted:
                        label_diagnostic_attempted = True
                        save_stage_diagnostic(self.navigator, self.config, target)
                    if ocr_failures >= limit:
                        raise
                    delay = cfg["poll_interval"]
                    if changed_deadline is not None:
                        delay = min(delay, max(0, changed_deadline - time.monotonic()))
                    self.stop.wait(delay)
                    continue
                if ocr_failures:
                    log.info("OCR recuperado após %s tentativa(s) não confirmada(s)", ocr_failures)
                ocr_failures = 0
                signature = (visible, tuple(row.fingerprint for row in rows)) if rows else None
                if changed_stage is not None:
                    changed_snapshots += 1
                    if time.monotonic() >= changed_deadline:
                        raise NavigationError(f"Fase alterada sem derrota nova confirmada: {target} -> {changed_stage}")
                # Require two consecutive matching OCR snapshots before treating
                # an alteration as a new log state; each occurrence remains distinct.
                if rows and signature == pending:
                    if not primed:
                        tracker.prime(rows)
                        primed = True
                        last_update = time.monotonic()
                    else:
                        update_time = time.monotonic()
                        additions = tracker.update(rows, update_time - last_update)
                        last_update = update_time
                        log.debug("Log: %s", tracker.diagnostics)
                        if changed_stage is not None:
                            additions = [row for row in additions
                                         if (isinstance(row, UntimedDefeat)
                                             or (isinstance(row, ParsedBattle) and row.outcome == "defeat"))
                                         and row.stage == target.stage]
                            if not additions:
                                raise NavigationError(f"Recuo sem derrota nova confirmada no log: {target} -> {changed_stage}")
                        must_handle_defeat = any(
                            (isinstance(row, UntimedDefeat)
                             or (isinstance(row, ParsedBattle) and row.outcome == "defeat"
                                 and (self.stop_on_defeat or changed_stage is not None)))
                            and row.stage == target.stage for row in additions)
                        before = completed
                        for row in additions:
                            if isinstance(row, UntimedDefeat):
                                if row.stage == target.stage:
                                    raise StageDefeated(target, completed, visible, row.raw)
                                log.warning("Derrota sem duração de fase anterior ignorada: %s", row.raw)
                                continue
                            if not isinstance(row, ParsedBattle):
                                log.debug("Notificação usada como âncora do log: %s", row.raw)
                                continue
                            if row.stage != target.stage:
                                log.warning("Resultado de fase anterior ignorado: %s", row.raw)
                                continue
                            record = BattleRecord(str(uuid.uuid4()), run_id, row.stage, target.difficulty,
                                                  row.duration, row.gold, row.exp, row.outcome, utc_now(), row.raw)
                            self.store.add(record)
                            completed += 1
                            last_result = time.monotonic()
                            log.info("%s/%s: %s", completed, cfg["battles_per_stage"], row.raw)
                            if row.outcome == "defeat" and (self.stop_on_defeat or changed_stage is not None):
                                raise StageDefeated(target, completed, visible, row.raw, timed=True)
                            if completed >= cfg["battles_per_stage"] and not must_handle_defeat:
                                break
                        if before < completed < cfg["battles_per_stage"]:
                            decision = self._profit_drop(target)
                            if decision is not None:
                                log_profit_drop(decision, f"Troca antecipada após {completed} batalha(s)")
                                return completed
                pending = signature
                if changed_stage is not None and changed_snapshots >= 2:
                    raise NavigationError(f"Recuo sem duas leituras iguais da derrota: {target} -> {changed_stage}")
                if completed < cfg["battles_per_stage"]:
                    delay = cfg["poll_interval"]
                    if changed_deadline is not None:
                        delay = min(delay, max(0, changed_deadline - time.monotonic()))
                    self.stop.wait(delay)
        except StageDefeated as exc:
            # Timed defeats are already persisted with the displayed duration.
            # Untimed defeats retain only observed interruption time, never an
            # invented duration or reward-bearing battle record.
            if not exc.timed:
                failure_reason = f"{type(exc).__name__}: {exc}"
            raise
        except BaseException as exc:
            failure_reason = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, NavigationError):
                save_stage_diagnostic(self.navigator, self.config, target)
            raise
        finally:
            if failure_reason:
                elapsed = max(0.001, time.monotonic() - last_result)
                self.store.add(BattleRecord(str(uuid.uuid4()), run_id, target.stage, target.difficulty,
                                           elapsed, 0, 0, "interrupted", utc_now(), failure_reason))
                log.warning("Tentativa interrompida após %.1fs: %s", elapsed, failure_reason)
                error_screenshot(self.window, self.config, "measurement")
        return completed


def print_ranking(ranking) -> None:
    print(f"{'Fase':<8} {'Dificuldade':<14} {'Tempo/s':>9} {'Gold médio':>12} {'Gold/min':>12} {'Gold/h':>13} {'V/D/I':>9} {'Confiável':>10}")
    for row in ranking:
        attempts = f"{row.victories}/{row.defeats}/{row.interruptions}"
        print(f"{row.stage:<8} {row.difficulty:<14} {row.mean_seconds:>9.2f} {row.mean_gold:>12.2f} "
              f"{row.gold_per_minute:>12.2f} {row.gold_per_hour:>13.2f} {attempts:>9} {'sim' if row.reliable else 'não':>10}")


def run_search(window, vision, navigator, inputs, stop, config: dict, store: Store):
    if not inputs.dry_run:
        navigator.preflight()
    if not inputs.dry_run and config["navigation"].get("mode") == "icon_map":
        initial = navigator.prepare_battle()
    else:
        initial = navigator.current_stage()
    if initial is None:
        raise NavigationError("Não foi possível identificar a fase e dificuldade atuais; confira a região da legenda.")
    print(f"Fase inicial: {initial.stage} / {initial.difficulty}")
    if inputs.dry_run:
        rows = read_results(vision, window.capture().image, config["measurement"])
        print(f"DRY RUN: {len(rows or [])} linhas reconhecidas; nenhum resultado foi gravado como batalha nova.")
        # A dry run diagnoses only what is visible. It must not pretend menus opened.
        steps = config["navigation"].get("open_world", [])
        if steps:
            navigator.execute_steps(steps[:1])
        return []
    # Both profitability comparisons and retreat need a confirmed catalog.
    # Discovery does not select a new battle stage.
    catalog = sorted(set(navigator.discover() + [initial]))
    failed = failed_stages(store.records(), cache_hours=config["search"]["cache_hours"])
    available = [stage for stage in catalog if stage not in failed]
    for stage in sorted(failed.intersection(catalog)):
        log.info("Histórico evita fase com derrota confirmada recente: %s", stage.key)
    collector = Collector(window, vision, navigator, stop, store, config,
                          comparison_catalog=available, stop_on_defeat=True)
    store.save_catalog(catalog)
    catalog_path = output_path(config, "catalog")
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps([asdict(stage) for stage in catalog], ensure_ascii=False, indent=2), encoding="utf-8")
    planner = SearchPlanner(initial, available, config["measurement"]["battles_per_stage"],
                            config["search"]["max_stages"], config["measurement"]["min_win_rate"],
                            config["search"]["cache_hours"], config["search"]["exploration_every"],
                            early_stop_gold_per_minute=config["search"].get("early_stop_gold_per_minute"))
    attempted: set[Stage] = set()
    retreat_destination = None

    def measure(target):
        nonlocal retreat_destination
        while True:
            stop.check()
            skipped = profit_drop(target, recent_stats(store, config), config,
                                  collector.comparison_catalog) is not None
            if config["navigation"].get("repeat"):
                navigator.ensure_farming(target)
            try:
                collector.collect(target)
            except StageDefeated as exc:
                failed.add(target)
                attempted.add(target)
                planner.mark_tested(target)
                collector.comparison_catalog = [stage for stage in catalog if stage not in failed]
                lower = nearest_lower_stage(target, collector.comparison_catalog)
                if lower is None:
                    raise NavigationError(f"Derrota em {target.key}; nenhuma fase inferior confirmada na mesma dificuldade") from exc
                log.warning("Derrota confirmada em %s; recuando para %s", target.key, lower.key)
                # select() verifies the live label even if the game already
                # retreated automatically; it then needs no redundant click.
                navigator.select(lower)
                retreat_destination = lower
                if not planner.budget_remaining or lower in attempted:
                    return
                target = lower
                continue
            if not skipped:
                attempted.add(target)
                planner.mark_tested(target)
            return

    initial_measurement = initial
    if initial in failed:
        initial_measurement = nearest_lower_stage(initial, available)
        if initial_measurement is None:
            raise NavigationError(f"Derrota recente em {initial.key}; nenhuma fase inferior confirmada na mesma dificuldade")
        log.info("Recuo pelo histórico de derrota: %s -> %s", initial.key, initial_measurement.key)
        navigator.select(initial_measurement)
        retreat_destination = initial_measurement
    initial_stats = next((row for row in recent_stats(store, config) if row.target == initial_measurement), None)
    if not initial_stats or not initial_stats.reliable:
        measure(initial_measurement)
    while (target := planner.next_stage([row for row in recent_stats(store, config)
                                        if row.target not in failed])) is not None:
        navigator.select(target)
        measure(target)
        export_ranking(recent_stats(store, config), output_path(config, "ranking"))
    ranking = [row for row in recent_stats(store, config) if row.target in catalog and row.target not in failed]
    export_ranking(ranking, output_path(config, "ranking"))
    print_ranking(ranking)
    best = next((row for row in ranking if row.reliable), None)
    if best:
        print(f"\nMelhor fase medida: {best.stage} / {best.difficulty}: {best.gold_per_hour:.2f} Gold/h.")
        original = next((row for row in ranking if row.target == initial), None)
        if original and original.gold_per_minute > 0:
            gain = (best.gold_per_minute / original.gold_per_minute - 1) * 100
            print(f"Diferença sobre {initial.stage} / {initial.difficulty}: {gain:+.1f}%.")
        if config["auto_switch_best"]:
            navigator.select(best.target)
            if not navigator.ensure_farming(best.target):
                print("Fase selecionada. Repetição automática não confirmada; calibre navigation.repeat se necessário.")
    else:
        print("Ainda não há uma fase com amostras e consistência suficientes.")
    if not config["auto_switch_best"] or best is None:
        destination = retreat_destination if initial in failed else initial
        navigator.select(destination)
        print("Fase inferior confirmada após derrota." if initial in failed else "Fase inicial restaurada.")
    return ranking
