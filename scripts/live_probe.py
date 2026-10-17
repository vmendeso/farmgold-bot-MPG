"""One visually located calibration click, with screenshots before and after."""
from __future__ import annotations

import argparse
import json
import ctypes
from ctypes import wintypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from farmbot.config import load_config
from farmbot.desktop import SafeInput, WindowManager, save_frame
from farmbot.safety import StopController
from farmbot.vision import Vision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--target", required=True, help="JSON file containing one visual target")
    parser.add_argument("--output", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--focus", action="store_true", help="Request focus for the identified game window before capture")
    args = parser.parse_args()
    config = load_config(args.config)
    target = json.loads(Path(args.target).read_text(encoding="utf-8-sig"))
    vision = Vision(config["vision"], config["_base_dir"])
    vision.validate_target(target)
    with StopController() as stop, WindowManager(config["window"]) as window:
        if args.focus:
            activate = window._api.user32.SetForegroundWindow
            activate.argtypes = [wintypes.HWND]
            activate.restype = wintypes.BOOL
            activate(window.hwnd)
            stop.wait(0.5)
            window.assert_foreground()
        frame = window.capture()
        save_frame(frame, Path(args.output).with_suffix(".before.png"))
        match = vision.locate(frame.image, target)
        if match is None:
            raise RuntimeError("The configured visual target is absent or ambiguous; no click")
        print(match)
        if SafeInput(window, stop, dry_run=not args.execute).click(frame, match):
            stop.wait(1)
            save_frame(window.capture(), args.output)
            print("After-click capture:", args.output)


if __name__ == "__main__":
    main()
