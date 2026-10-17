"""Inspect one freshly recognized gold node by hover, without clicking."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farmbot.config import load_config
from farmbot.desktop import SafeInput, WindowManager, save_frame
from farmbot.map_navigation import MapNavigator, UnrecognizedTooltip
from farmbot.safety import StopController
from farmbot.vision import Vision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--page", required=True)
    parser.add_argument("--hint", nargs=2, type=float, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--focus", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    vision = Vision(config["vision"], config["_base_dir"])
    with StopController() as stop, WindowManager(config["window"]) as window:
        stop.wait(config["safety"]["start_delay"])
        if args.focus:
            window.request_focus()
            stop.wait(.25)
        window.assert_foreground()
        navigator = MapNavigator(window, vision, SafeInput(window, stop, dry_run=False), stop, config["navigation"])
        navigator.preflight()
        page = next(page for page in navigator._pages() if page["name"] == args.page)
        frame, menu = navigator._capture_menu()
        if not all(navigator._visible(frame, menu, target) is not None for target in page["ready"]):
            raise RuntimeError("Abra a página do mapa solicitada antes do diagnóstico.")
        try:
            print(navigator._hover_node(page, tuple(args.hint)))
        except UnrecognizedTooltip as exc:
            print(exc)
        frame, menu = navigator._capture_menu()
        save_frame(frame, args.output)
        masked = navigator._tooltip_text_image(frame.image)
        for roi in navigator._tooltip_rois(frame.image, menu):
            print("ROI", roi)
            print("RAW", [line.text for line in navigator.tooltip_vision.read_lines(frame.image, roi=roi)])
            print("GOLD", [line.text for line in navigator.tooltip_vision.read_lines(masked, roi=roi)])


if __name__ == "__main__":
    main()
