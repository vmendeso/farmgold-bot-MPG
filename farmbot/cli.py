from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import math
from pathlib import Path
import re
import sys
import uuid

from .config import load_config, output_path
from .models import BattleRecord, Stage, utc_now
from .results import LogTracker, parse_battle
from .runner import Collector, error_screenshot, print_ranking, recent_stats, run_search
from .storage import Store, export_ranking, stats


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use um inteiro positivo") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("use um inteiro positivo")
    return number


def _stage_id(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[1-9]\d*-[1-9]\d*", value):
        raise argparse.ArgumentTypeError("use uma fase no formato ato-fase, por exemplo 4-7")
    return value


def _nonempty_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("informe um texto não vazio")
    return value


def _positive_minutes(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use um intervalo positivo em minutos") from exc
    if not math.isfinite(number) or number <= 0 or not math.isfinite(number * 60):
        raise argparse.ArgumentTypeError("use um intervalo positivo e finito em minutos")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Mede Gold real por tempo usando somente a interface do jogo.")
    root.add_argument("--config", default="config.json", help="Arquivo JSON de configuração")
    root.add_argument("--verbose", action="store_true")
    root.add_argument("--focus", action="store_true", help="Solicita foco da janela identificada no início; nunca recupera foco durante cliques")
    commands = root.add_subparsers(dest="command", required=True)
    gui = commands.add_parser("gui", help="Abre a janela de controle do bot")
    gui.add_argument("--start", action="store_true", help="Inicia o loop ao abrir usando os ajustes salvos")
    commands.add_parser("stop", help="Solicita parada do bot ativo, preservando as medições")
    commands.add_parser("check-config", help="Valida configuração, OCR e referências sem clicar")
    capture = commands.add_parser("capture", help="Captura somente a área cliente do jogo")
    capture.add_argument("--output", default="artifacts/window.png")
    ocr = commands.add_parser("ocr", help="Testa OCR de screenshot ou da janela atual")
    ocr.add_argument("--image")
    ocr.add_argument("--all-text", action="store_true", help="Lê a imagem inteira, sem limitar aos logs")
    locate = commands.add_parser("locate", help="Localiza um alvo JSON; nunca clica")
    locate.add_argument("target", help='JSON inline ou caminho de arquivo com um alvo, ex.: {"text":"^Mundo$"}')
    locate.add_argument("--image")
    crop = commands.add_parser("crop", help="Recorta um PNG de referência; coordenadas em pixels da imagem")
    crop.add_argument("--image", required=True)
    crop.add_argument("--box", type=int, nargs=4, metavar=("X", "Y", "W", "H"), required=True)
    crop.add_argument("--output", required=True)
    nav = commands.add_parser("navigate", help="Testa uma rota configurada, com confirmação visual por passo")
    nav.add_argument("--flow", choices=["open_world", "return_to_battle"], required=True)
    nav.add_argument("--execute", action="store_true")
    discovery = commands.add_parser("discover", help="Descobre fases comprovadamente desbloqueadas")
    discovery.add_argument("--execute", action="store_true")
    discovery.add_argument("--page", help="Limita o teste a uma página configurada")
    selection = commands.add_parser("select", help="Seleciona uma fase/dificuldade confirmada visualmente, sem coletar batalhas")
    selection.add_argument("--stage", type=_stage_id, required=True, help="Identificador da fase, por exemplo 4-7")
    selection.add_argument("--difficulty", type=_nonempty_text, required=True, help="Nome exibido no jogo, por exemplo Pesadelo")
    selection.add_argument("--execute", action="store_true", help="Habilita cliques nesta seleção; caso contrário usa dry_run da configuração")
    observe = commands.add_parser("observe", help="Mede a fase atual sem clicar")
    observe.add_argument("--hover", action="store_true", help="Permite posicionar o ponteiro no jogo para revelar a legenda da fase")
    run = commands.add_parser("run", help="Busca as melhores fases; dry_run vem da configuração")
    run.add_argument("--execute", action="store_true", help="Autoriza os cliques de navegação desta execução")
    run.add_argument("--auto-switch", action="store_true", help="Seleciona a melhor fase ao terminar (requer cliques habilitados)")
    run.add_argument("--max-stages", type=_positive_int, metavar="N",
                     help="Limita novas coletas a N fases nesta execução; inclui a inicial quando medida, sem contar cache confiável")
    run.add_argument("--battles-per-stage", type=_positive_int, metavar="N",
                     help="Coleta N resultados novos por fase apenas nesta execução, sem alterar a configuração salva")
    loop = commands.add_parser("loop", help="Repete buscas em horários regulares, sem execuções simultâneas; ESC encerra")
    loop.add_argument("--execute", action="store_true", help="Autoriza cliques durante as rodadas")
    loop.add_argument("--auto-switch", action="store_true", help="Seleciona a melhor fase confiável ao fim de cada rodada")
    loop.add_argument("--max-stages", type=_positive_int, default=1, metavar="N",
                      help="Novas fases medidas por rodada (padrão: 1); mantém cinco batalhas por fase na configuração atual")
    loop.add_argument("--interval-minutes", type=_positive_minutes, default=5, metavar="MIN",
                      help="Intervalo entre horários de início (padrão: 5); horários vencidos são pulados")
    commands.add_parser("rank", help="Exporta o ranking das medições recentes")
    demo = commands.add_parser("demo", help="Executa simulação isolada, sem jogo ou OCR")
    demo.add_argument("--output", default="artifacts/demo")
    return root


def _logging(config: dict, verbose: bool):
    path = output_path(config, "log")
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")], force=True)


def _image(path: str):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Imagem inválida: {path}")
    return image


def _write_image(path: str | Path, image):
    import cv2
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("Falha ao codificar PNG")
    destination.write_bytes(encoded.tobytes())
    return destination


def _target(value: str):
    return json.loads(value) if value.lstrip().startswith("{") else json.loads(Path(value).read_text(encoding="utf-8-sig"))


def _recognize(args, config, vision, image):
    if args.command == "ocr":
        lines = vision.read_lines(image, roi=None if args.all_text else config["measurement"]["logs_roi"],
                                  mode="text" if args.all_text else "logs")
        for index, line in enumerate(lines, 1):
            print(json.dumps({"line": index, **asdict(line), "parsed": asdict(battle) if (battle := parse_battle(line.text)) else None}, ensure_ascii=False))
        if not lines:
            raise ValueError("Nenhuma linha reconhecida; verifique ROI, idioma e escala do OCR.")
    else:
        target = _target(args.target)
        vision.validate_target(target)
        matches = vision.locate_all(image, target)
        print(json.dumps([asdict(match) for match in matches], ensure_ascii=False, indent=2))
        import cv2
        annotated = image.copy()
        for match in matches:
            cv2.rectangle(annotated, (match.x, match.y), (match.x + match.width, match.y + match.height), (0, 255, 255), 2)
        path = _write_image(output_path(config, "artifacts") / "locate.png", annotated)
        print(f"Diagnóstico visual: {path}")


def demo(directory: str) -> int:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    run_id = "demo-" + str(uuid.uuid4())
    # An isolated in-memory database prevents synthetic samples entering live data.
    with Store(":memory:") as store:
        for stage, seconds, gold in (("4-8", 43.37, 1362), ("4-7", 35.0, 1200), ("4-6", 28.0, 1050)):
            def row(duration):
                return parse_battle(f"{stage} concluída · 0:{duration:05.2f} · +{gold:,} · +3,055 EXP")
            visible = [row(seconds + offset / 100) for offset in range(5)]
            tracker = LogTracker()
            tracker.prime(visible)
            for number in range(5):
                # Includes two identical consecutive outcomes as distinct rows.
                battle = row(seconds + (number // 2) / 100)
                visible = [battle] + visible[:4]
                for result in tracker.update(visible, seconds + 1):
                    store.add(BattleRecord(str(uuid.uuid4()), run_id, stage, "Pesadelo", result.duration,
                                           result.gold, result.exp, result.outcome, utc_now(), result.raw))
                assert tracker.update(visible, 0.1) == [], "Repeated capture must not recount rows"
        ranking = stats(store.records())
        if len(ranking) != 3 or any(row.victories != 5 for row in ranking):
            raise RuntimeError("Demo não produziu as quinze ocorrências esperadas")
        path = export_ranking(ranking, output / "ranking.csv")
        print("SIMULAÇÃO — valores ilustrativos, sem medições do jogo.")
        print_ranking(ranking)
        print(f"CSV: {path.resolve()}")
        (output / "summary.json").write_text(json.dumps([asdict(row) for row in ranking], ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _check_config(config: dict, vision) -> int:
    from .navigation import Navigator
    if config["navigation"].get("mode") == "icon_map":
        from .map_navigation import MapNavigator as Navigator
    from .safety import StopController
    problems = []
    try:
        ocr = vision._ocr()
        print(f"Tesseract: {ocr.get_tesseract_version()}")
        missing = set(config["vision"].get("language", "por+eng").split("+")) - set(ocr.get_languages())
        if missing:
            problems.append("Idiomas ausentes: " + ", ".join(sorted(missing)))
    except Exception as exc:
        problems.append(f"OCR: {exc}")
    try:
        Navigator(None, vision, None, StopController(), config["navigation"]).preflight()
    except Exception as exc:
        problems.append(f"Navegação: {exc}")
    print(f"dry_run={config['dry_run']}; auto_switch_best={config['auto_switch_best']}")
    for problem in problems:
        print(problem)
    return 2 if problems else 0


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    window = None
    config = None
    instance = None
    try:
        if args.command == "gui":
            from .gui import launch_gui
            return launch_gui(args.config, auto_start=args.start)
        if args.command == "demo":
            return demo(args.output)
        config = load_config(args.config)
        if args.command in ("run", "loop") and args.max_stages is not None:
            config["search"]["max_stages"] = args.max_stages
        if args.command == "run" and args.battles_per_stage is not None:
            config["measurement"]["battles_per_stage"] = args.battles_per_stage
        _logging(config, args.verbose)
        if args.command == "stop":
            from .instance import InstanceAlreadyRunning, InstanceLock
            directory = Path(config["_base_dir"]) / "data"
            try:
                with InstanceLock(directory / "farmbot.lock"):
                    print("Nenhum bot ativo neste projeto.")
            except InstanceAlreadyRunning:
                (directory / "farmbot.stop").write_text("stop\n", encoding="utf-8")
                print("Parada solicitada. Aguarde o salvamento das medições e a saída do bot.")
            return 0
        if args.command == "rank":
            with Store(output_path(config, "database")) as store:
                ranking = recent_stats(store, config)
                print_ranking(ranking)
                print(f"CSV: {export_ranking(ranking, output_path(config, 'ranking'))}")
            return 0
        if args.command == "crop":
            image = _image(args.image)
            x, y, w, h = args.box
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > image.shape[1] or y + h > image.shape[0]:
                raise ValueError("O recorte deve caber na imagem")
            print(_write_image(args.output, image[y:y + h, x:x + w]))
            return 0
        from .vision import Vision
        vision = Vision(config["vision"], config["_base_dir"])
        if args.command == "check-config":
            return _check_config(config, vision)
        if args.command in ("ocr", "locate") and args.image:
            _recognize(args, config, vision, _image(args.image))
            return 0
        from .desktop import SafeInput, WindowManager, save_frame
        from .navigation import DryRunComplete, Navigator
        if config["navigation"].get("mode") == "icon_map":
            from .map_navigation import MapNavigator as Navigator
        from .safety import StopController, StopRequested
        dry_run = config["dry_run"] and not getattr(args, "execute", False) and not getattr(args, "hover", False)
        if getattr(args, "auto_switch", False):
            config["auto_switch_best"] = True
        if args.command in ("run", "loop", "observe", "select", "navigate", "discover"):
            from .instance import InstanceLock
            instance = InstanceLock(Path(config["_base_dir"]) / "data" / "farmbot.lock")
            instance.__enter__()
        stop_file = Path(config["_base_dir"]) / "data" / "farmbot.stop" if instance is not None else None
        with StopController(stop_file=stop_file) as stop:
            print(f"Ative a janela do jogo em {config['safety']['start_delay']:g}s. ESC interrompe; Ctrl+C também.")
            stop.wait(config["safety"]["start_delay"])
            if args.command == "loop":
                from .continuous import SearchCycle
                from .loop import run_loop
                with Store(output_path(config, "database")) as store:
                    cycle = SearchCycle(config, vision, store, stop, dry_run=dry_run, focus=args.focus)
                    run_loop(cycle, stop, interval_seconds=args.interval_minutes * 60)
                return 0
            window = WindowManager(config["window"])
            if args.focus:
                window.request_focus()
                stop.wait(0.25)
                window.assert_foreground()
            inputs = SafeInput(window, stop, dry_run=dry_run)
            navigator = Navigator(window, vision, inputs, stop, config["navigation"])
            if args.command == "capture":
                print(save_frame(window.capture(), args.output))
            elif args.command in ("ocr", "locate"):
                _recognize(args, config, vision, window.capture().image)
            elif args.command == "navigate":
                if config["navigation"].get("mode") == "icon_map":
                    navigator.preflight()
                    if args.flow == "open_world":
                        navigator._open_world()
                    else:
                        navigator._close_world()
                        navigator._wait_stage()
                else:
                    navigator.execute_steps(config["navigation"][args.flow])
            elif args.command == "discover":
                stages = navigator.inspect_page(args.page) if args.page else navigator.discover()
                with Store(output_path(config, "database")) as store:
                    store.save_catalog(stages)
                path = output_path(config, "catalog")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps([asdict(stage) for stage in stages], ensure_ascii=False, indent=2), encoding="utf-8")
                print(path.read_text(encoding="utf-8"))
            elif args.command == "select":
                target = Stage(args.stage, args.difficulty)
                navigator.select(target)
                print(f"Fase confirmada: {target.stage} / {target.difficulty}")
            else:
                with Store(output_path(config, "database")) as store:
                    try:
                        if args.command == "observe":
                            target = navigator.current_stage()
                            if target is None:
                                raise ValueError("Fase/dificuldade não identificada; calibre current_stage_roi.")
                            Collector(window, vision, navigator, stop, store, config).collect(target)
                            print_ranking(recent_stats(store, config))
                        else:
                            run_search(window, vision, navigator, inputs, stop, config, store)
                    finally:
                        if not dry_run or args.command == "observe":
                            export_ranking(recent_stats(store, config), output_path(config, "ranking"))
        return 0
    except KeyboardInterrupt:
        print("Interrompido pelo usuário. Resultados já observados foram preservados.")
        return 130
    except Exception as exc:
        from .navigation import DryRunComplete
        from .safety import StopRequested
        if isinstance(exc, DryRunComplete):
            print(str(exc))
            return 0
        if window is not None and config is not None:
            error_screenshot(window, config)
        if isinstance(exc, StopRequested):
            print(str(exc))
            return 130
        logging.getLogger(__name__).error("%s: %s", type(exc).__name__, exc, exc_info=getattr(args, "verbose", False))
        return 2
    finally:
        try:
            if window is not None:
                window.close()
        finally:
            if instance is not None:
                instance.__exit__(None, None, None)
