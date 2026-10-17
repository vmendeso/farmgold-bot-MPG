"""Apply calibration from the captured 2560px transparent-window client."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "config.json"
config = json.loads(path.read_text(encoding="utf-8"))
def template(name, threshold=.9, offset=None):
    target = {"template": f"templates/{name}.png", "threshold": threshold, "scales": [0.75, 1]}
    if name in ("normal_option", "act1"):
        target["resampling"] = "linear"
    if name in ("act4", "act2", "nightmare_selected"):
        target["resampling"] = "nearest"
    if name == "act4_selected":
        target.update(resampling="linear", scales=[.74, .75, 1])
    if name == "averno_option":
        target["scales"] = [.74, .75, 1]
    if name in ("inferno_selected", "act2_selected", "world_footer"):
        target["scales"] = [1, 4/3]
    if name == "node_gold":
        target.update(threshold=.8, scales=[.74, .75, 1], resampling="linear")
    if offset is not None:
        target["offset"] = offset
    return target

selector = [-745, 225, 225, 80]
act1 = [-485, 220, 150, 85]
act4 = [-35, 220, 165, 85]
act2 = [-335, 220, 165, 85]
def page(act, difficulty, option, selected):
    offset = {1: act1, 2: act2, 4: act4}[act]
    return {
        "name": f"ato{act}_{option}", "act": act, "difficulty": difficulty,
        "enter": [
            {"click": template("difficulty_arrow", offset=selector),
             "expect": template("normal_option", offset=[-750, 300, 240, 390]),
             "skip_if": template(selected, offset=selector)},
            {"click": template(f"{option}_option", offset=[-750, 300, 240, 390]),
             "expect": template(selected, offset=selector),
             "skip_if": template(selected, offset=selector)},
            {"click": template(f"act{act}", offset=offset),
             "expect": template(f"act{act}_selected", offset=offset),
             "skip_if": template(f"act{act}_selected", offset=offset)},
        ],
        "ready": [template(f"act{act}_selected", offset=offset), template(selected, offset=selector)],
    }

config["vision"].update({"ocr_scale": 2, "logs_ocr_timeout": 6, "green_logs": False, "min_confidence": 0, "text_psm": 6,
                         "logs_linewise": True, "logs_line_psm": 7})
config["measurement"].update({
    "log_order": "oldest_first", "poll_interval": .5, "max_ocr_failures": 20,
    "logs_roi": [992/2560, 1000/2560, 576/2560, 136/2560],
    "logs_anchor": {"target": template("menu"), "reference_width": 52, "offset": [-589, -329, 576, 136]},
})
config["navigation"] = {
    "mode": "icon_map", "calibrated": True, "timeout": 10, "poll_interval": .25, "retries": 2,
    "difficulty_names": ["Normal", "Difícil", "Pesadelo", "Inferno", "Averno"],
    "anchor_width": 52, "hover_delay": .3, "max_nodes": 12,
    "world_header_anchor": [-363, 151], "panel_search_offset": [-1100, -1100, 1600, 2100],
    "world_footer": template("world_footer"), "world_footer_anchor": [-350/.75, 633/.75],
    "menu": template("menu"), "world_ready": template("world_ready"),
    "world_tab": template("world_tab"), "close_world": template("close_world"),
    "node_gold": template("node_gold", .85),
    "battle_hover_offset": [-310, -75, 24, 24],
    "current_stage_offset": [-570, 21, 560, 31],
    "world_offset": [-800, 100, 1000, 800], "world_tab_offset": [-800, 45, 1000, 90],
    "nodes_offset": [-740, 315, 850, 540], "tooltip_offset": [-850, 35, 1150, 900],
    "label_confidence": .25, "label_word_confidence": .25,
    "pages": [page(4, "Pesadelo", "nightmare", "nightmare_selected"), page(1, "Averno", "averno", "averno_selected"),
              page(2, "Inferno", "inferno", "inferno_selected")],
}
path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(path)
