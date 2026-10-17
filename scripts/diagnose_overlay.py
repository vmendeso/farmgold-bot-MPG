"""Inspect transparent-window hit testing; moves the pointer but never clicks."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ctypes import wintypes
from farmbot.config import load_config
from farmbot.desktop import WindowManager
from farmbot.safety import StopController
from farmbot.vision import Vision

config = load_config("config.json")
with WindowManager(config["window"]) as window, StopController() as stop:
    frame = window.capture()
    match = Vision(config["vision"], config["_base_dir"]).locate(
        frame.image, json.loads(Path("templates/live-menu.target.json").read_text()))
    if match is None:
        raise RuntimeError("Menu not uniquely visible")
    x, y = match.center
    x, y = frame.rect.left + x, frame.rect.top + y
    assert frame.rect.left <= x < frame.rect.right and frame.rect.top <= y < frame.rect.bottom
    user32 = window._api.user32
    def inspect(label):
        hit = user32.WindowFromPoint(wintypes.POINT(x, y))
        root = user32.GetAncestor(hit, 2) if hit else 0
        print(label, "game", window.hwnd, "hit", hit, "root", root,
              "hit_title", window._api.title(root) if root else None,
              "game_pid", window._api.process_id(window.hwnd),
              "hit_pid", window._api.process_id(root) if root else None)
    inspect("before")
    window.assert_foreground()
    stop.check()
    import pyautogui
    pyautogui.FAILSAFE = True
    print("pointer", pyautogui.position(), "target", (x, y))
    pyautogui.moveTo(x, y, duration=0)
    stop.wait(.5)
    inspect("after_hover")
