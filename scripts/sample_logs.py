"""Read-only short live OCR sample; keeps only the game's log crop as evidence."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
from farmbot.config import load_config
from farmbot.desktop import WindowManager
from farmbot.results import parse_log_entry
from farmbot.safety import StopController
from farmbot.vision import Vision

config = load_config("config.json")
vision = Vision({**config["vision"], "min_confidence": 0}, config["_base_dir"])
directory = Path("artifacts/log-samples")
directory.mkdir(parents=True, exist_ok=True)
with WindowManager(config["window"]) as window, StopController() as stop:
    window.request_focus()
    stop.wait(.3)
    deadline = time.monotonic() + 50
    previous = None
    while time.monotonic() < deadline:
        frame = window.capture()
        rows = vision.read_lines(frame.image, config["measurement"]["logs_roi"], mode="logs")
        texts = tuple(row.text for row in rows)
        if texts != previous:
            parsed = [parse_log_entry(text) for text in texts]
            print(json.dumps({"time": time.time(), "texts": texts, "valid": [item is not None for item in parsed]}, ensure_ascii=False), flush=True)
            stamp = str(time.time_ns())
            x, y, w, h = config["measurement"]["logs_roi"]
            ih, iw = frame.image.shape[:2]
            crop = frame.image[int(y*ih):round((y+h)*ih), int(x*iw):round((x+w)*iw)]
            success, encoded = cv2.imencode(".png", crop)
            if success:
                (directory / f"{stamp}.png").write_bytes(encoded.tobytes())
            (directory / f"{stamp}.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
            previous = texts
        stop.wait(1)
