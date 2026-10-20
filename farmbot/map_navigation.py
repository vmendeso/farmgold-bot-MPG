"""Navigation for the observed icon map, anchored to its recognized menu.

Only gold markers are eligible. Stage numbers come from stable tooltip OCR,
never from map order or a remembered coordinate.
"""
from __future__ import annotations

import logging
import json
import math
import re
import time
from pathlib import Path

from .desktop import HoverUnavailable
from .models import Stage
from .navigation import CalibrationError, DryRunComplete, NavigationError, Navigator, _normalized, _STAGE_ID
from .vision import Match, Vision, VisionError

log = logging.getLogger(__name__)
_TOOLTIP_ID = re.compile(r"\[\s*([1-9]\d*)\s*[-–—]\s*([1-9]\d*)\s*\]")


class UnrecognizedTooltip(NavigationError):
    """A positively gold node lacked a readable, stable tooltip title."""

    def __init__(self, message: str, *, frame=None, menu=None, ocr=None):
        super().__init__(message)
        self.frame = frame
        self.menu = menu
        self.ocr = list(ocr or [])


class WorldAnchorUnavailable(NavigationError):
    """No current-frame World anchor; recognition may be retried without clicks."""


class NodeUnavailable(NavigationError):
    """A previously seen marker needs fresh positive visual confirmation."""


class MapNavigator(Navigator):
    def __init__(self, window, vision, inputs, stop, config: dict):
        super().__init__(window, vision, inputs, stop, config)
        self.anchor_width = float(config.get("anchor_width", 52))
        self.hover_delay = float(config.get("hover_delay", 0.2))
        self.label_timeout = float(config.get("label_timeout", 4.0))
        self.label_poll_interval = float(config.get("label_poll_interval", 0.15))
        self.label_hover_delay = float(config.get("label_hover_delay", 0.4))
        self.max_nodes = int(config.get("max_nodes", 12))
        fraction = config.get("node_hover_fraction", [0.5, 0.85])
        if not isinstance(fraction, (list, tuple)) or len(fraction) != 2:
            raise CalibrationError("node_hover_fraction requires [x_fraction, y_fraction]")
        self.node_hover_fraction = tuple(map(float, fraction))
        if not all(math.isfinite(value) and 0 < value < 1 for value in self.node_hover_fraction):
            raise CalibrationError("node_hover_fraction must remain strictly inside the recognized marker")
        if not math.isfinite(self.anchor_width) or self.anchor_width <= 0:
            raise CalibrationError("anchor_width must be positive")
        if not math.isfinite(self.hover_delay) or not 0 < self.hover_delay <= 5:
            raise CalibrationError("hover_delay must be between 0 and 5 seconds")
        if not 1 <= self.max_nodes <= 50:
            raise CalibrationError("max_nodes must be between 1 and 50")
        if not all(math.isfinite(value) and value > 0 for value in (
            self.label_timeout, self.label_poll_interval, self.label_hover_delay
        )):
            raise CalibrationError("label_timeout, label_poll_interval and label_hover_delay must be positive")
        self.label_vision = vision
        self.label_fallback_vision = None
        self.label_edge_vision = None
        self.label_edge_confirm_vision = None
        self.last_label_evidence = None
        self.tooltip_vision = vision
        if isinstance(vision, Vision):
            self.label_vision = Vision({**vision.config, "text_psm": 7,
                                       "white_text": True,
                                       "min_confidence": config.get("label_word_confidence", 0.25)}, vision.base_dir)
            # The same tiny 8 can become B at 2x while remaining readable at
            # 3x. Independent preprocessing variants vote on complete labels;
            # never replace characters or fill fields from another reading.
            fallback_scale = 3.0 if float(vision.config.get("ocr_scale", 3.0)) != 3.0 else 4.0
            self.label_fallback_vision = Vision(
                {**self.label_vision.config, "ocr_scale": fallback_scale}, vision.base_dir)
            # A strict brightness mask can remove a digit's antialiased edge
            # at every scale. An alternate neutral-pixel mask preserves more of
            # that edge while still excluding colored, animated scenery.
            self.label_edge_vision = Vision(
                {**self.label_vision.config, "ocr_scale": 2.0, "white_text_value_min": 150}, vision.base_dir)
            self.label_edge_confirm_vision = Vision(
                {**self.label_edge_vision.config, "ocr_scale": 3.0}, vision.base_dir)
            self.tooltip_vision = Vision({**vision.config, "text_psm": 7,
                                         "min_confidence": 0}, vision.base_dir)
        self._hints: dict[str, tuple[str, tuple[float, float]]] = {}
        self.excluded_nodes: list[dict] = []
        self._tooltip_diagnostics: list[dict] = []

    def _offset(self, key: str, fallback: list[float]) -> list[float]:
        value = self.config.get(key, fallback)
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise CalibrationError(f"{key} must contain [dx, dy, width, height]")
        values = list(map(float, value))
        if not all(math.isfinite(item) for item in values) or min(values[2:]) <= 0:
            raise CalibrationError(f"Invalid {key}")
        return values

    def _box(self, image, menu: Match, offset) -> tuple[int, int, int, int]:
        dx, dy, w, h = offset
        scale = menu.width / self.anchor_width
        height, width = image.shape[:2]
        left, top = round(menu.x + dx * scale), round(menu.y + dy * scale)
        right, bottom = round(left + w * scale), round(top + h * scale)
        left, top, right, bottom = max(0, left), max(0, top), min(width, right), min(height, bottom)
        if right <= left or bottom <= top:
            raise NavigationError("Calibrated region is outside the game client")
        return left, top, right - left, bottom - top

    @staticmethod
    def _normalized_box(image, box) -> list[float]:
        height, width = image.shape[:2]
        x, y, w, h = box
        return [x / width, y / height, w / width, h / height]

    def _panel_search_box(self, image, menu):
        return self._box(image, menu, self._offset("panel_search_offset", [-1100, -1100, 1600, 2100]))

    def _panel_origin(self, image, menu):
        reference = self.config.get("world_header_anchor")
        if reference is None:
            return menu
        target = dict(self.config["world_ready"])
        target["roi"] = self._normalized_box(image, self._panel_search_box(image, menu))
        header = self.vision.locate(image, target)
        if header is None:
            fallback = self.config.get("world_footer")
            if fallback is None:
                return None
            target = dict(fallback)
            target["roi"] = self._normalized_box(image, self._panel_search_box(image, menu))
            header = self.vision.locate(image, target)
            if header is None:
                return None
            reference = self.config["world_footer_anchor"]
        scale = menu.width / self.anchor_width
        return Match(round(header.x - reference[0] * scale), round(header.y - reference[1] * scale),
                     menu.width, menu.height, header.confidence, "world panel origin")

    def _anchored(self, target: dict, image, menu: Match, offset=None) -> dict | None:
        result = dict(target)
        position = result.pop("offset", offset)
        if self.config.get("world_header_anchor") is not None:
            if target.get("template") in (self.config["world_tab"].get("template"),
                                           self.config["world_ready"].get("template")):
                result["roi"] = self._normalized_box(image, self._panel_search_box(image, menu))
                return result
            menu = self._panel_origin(image, menu)
            if menu is None:
                return None  # An inventory close button is not proof of World.
        if position is None:
            position = self._offset("world_offset", [-800, 100, 1000, 800])
        result["roi"] = self._normalized_box(image, self._box(image, menu, position))
        return result

    def _capture_menu(self):
        self.stop.check()
        frame = self.window.capture()
        menu = self.vision.locate(frame.image, self.config["menu"])
        if menu is None:
            raise NavigationError("The unique calibrated menu anchor is not visible")
        return frame, menu

    def _pages(self) -> list[dict]:
        if self.config.get("calibrated") is not True:
            raise CalibrationError("Icon-map navigation must be calibrated first")
        pages = self.config.get("pages")
        names = self.config.get("difficulty_names", [])
        if not isinstance(pages, list) or not pages or not names:
            raise CalibrationError("Configure icon-map pages and difficulty_names")
        seen = set()
        for page in pages:
            if not isinstance(page, dict) or not page.get("name") or page["name"] in seen:
                raise CalibrationError("Every icon-map page needs a unique name")
            seen.add(page["name"])
            if not isinstance(page.get("act"), int) or page["act"] < 1 or page.get("difficulty") not in names:
                raise CalibrationError(f"Invalid act or difficulty on {page['name']}")
            self._steps(page.get("enter", []), page["name"] + ".enter")
            ready = page.get("ready")
            if not isinstance(ready, list) or len(ready) < 2:
                raise CalibrationError(f"{page['name']}: ready needs both selected-act and selected-difficulty targets")
            for target in ready:
                self._target(target, page["name"] + ".ready")
        return pages

    def _steps(self, value, name: str, *, required: bool = False) -> list[dict]:
        steps = super()._steps(value, name, required=required)
        for number, step in enumerate(steps, 1):
            if "skip_if" in step:
                self._target(step["skip_if"], f"{name}[{number}].skip_if")
        return steps

    @staticmethod
    def _step_targets(steps: list[dict]) -> list[dict]:
        return [step[key] for step in steps for key in ("click", "expect", "skip_if") if key in step]

    def preflight(self, selection: bool = True) -> None:
        if "world_footer" in self.config:
            anchor = self.config.get("world_footer_anchor")
            if (not isinstance(anchor, (list, tuple)) or len(anchor) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in anchor)):
                raise CalibrationError("world_footer_anchor requires two finite offsets")
            self._validate_references([self._target(self.config["world_footer"], "world_footer")])
        reference = self.config.get("world_header_anchor")
        if reference is not None:
            if (not isinstance(reference, (list, tuple)) or len(reference) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in reference)):
                raise CalibrationError("world_header_anchor requires two finite offsets")
            self._offset("panel_search_offset", [-1100, -1100, 1600, 2100])
        pages = self._pages()
        targets = [self._target(self.config.get(key), key)
                   for key in ("menu", "world_ready", "world_tab", "close_world", "node_gold")]
        if "template" not in self.config["node_gold"]:
            raise CalibrationError("node_gold requires an observed gold-node template")
        for page in pages:
            targets.extend(page["ready"])
            targets.extend(self._step_targets(page.get("enter", [])))
        self._validate_references(targets)
        for key, default in (
            ("battle_hover_offset", [-310, -75, 24, 24]),
            ("current_stage_offset", [-570, 21, 560, 31]),
            ("world_offset", [-800, 100, 1000, 800]),
            ("nodes_offset", [-740, 315, 850, 520]),
            ("tooltip_offset", [-850, 35, 1150, 900]),
        ):
            self._offset(key, default)

    def _visible(self, frame, menu: Match, target: dict, offset=None):
        anchored = self._anchored(target, frame.image, menu, offset)
        return self.vision.locate(frame.image, anchored) if anchored is not None else None

    def _wait_target(self, target: dict, *, absent: bool = False, offset=None, deadline=None):
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        while True:
            frame, menu = self._capture_menu()
            match = self._visible(frame, menu, target, offset)
            if (match is None) == absent:
                return frame, menu, match
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NavigationError(f"Map transition was not visually confirmed: {target}")
            self.stop.wait(min(self.poll_interval, remaining))

    def _capture_hover_menu(self):
        frame, menu = self._capture_menu()
        return frame, menu, menu

    def _hover_fresh(self, acquire, *, deadline=None):
        """Retry ownership acquisition only, with fresh visual evidence each time.

        The caller's deadline includes target recognition and every retry. No
        click, focus recovery, or other input-safety exception is retried here.
        """
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        attempts = 0
        while True:
            self.stop.check()
            if time.monotonic() >= deadline:
                raise HoverUnavailable("Map hover acquisition exceeded the navigation deadline")
            frame, menu, match = acquire()
            if time.monotonic() >= deadline:
                raise HoverUnavailable("Map hover target recognition exceeded the navigation deadline")
            try:
                moved = self.inputs.hover(frame, match, deadline=deadline)
                if attempts:
                    log.info("Map hover recovered after %d retries", attempts)
                return frame, menu, match, moved
            except HoverUnavailable:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                attempts += 1
                if attempts == 1:
                    log.warning("Map hover temporarily unavailable; retrying with fresh visual targets")
                self.stop.wait(min(self.poll_interval, remaining))

    def _click_target(self, target: dict, offset=None) -> None:
        deadline = time.monotonic() + self.timeout
        frame, menu, match, moved = self._hover_fresh(
            lambda: self._wait_target(target, offset=offset, deadline=deadline), deadline=deadline)
        if not moved:
            raise DryRunComplete(f"Dry run: target identified for hover/click: {match.label}")
        # Hover can enable overlay hit testing. Coordinates are recognized again
        # on a new capture before the ordinary ownership-checked click.
        frame, menu, match = self._wait_target(target, offset=offset, deadline=deadline)
        if time.monotonic() >= deadline:
            raise NavigationError("Map target confirmation exceeded the navigation deadline")
        if not self.inputs.click(frame, match):
            raise DryRunComplete(f"Dry run: target identified for click: {match.label}")

    def execute_steps(self, steps: list[dict]) -> None:
        self._steps(steps, "icon-map route")
        self._validate_references(self._step_targets(steps))
        for step in steps:
            if "skip_if" in step:
                frame, menu = self._capture_menu()
                if self._visible(frame, menu, step["skip_if"]) is not None:
                    log.info("Route step already satisfied by its visible skip_if target")
                    continue
            self._click_target(step["click"])
            self._wait_target(step["expect"])

    def _open_world(self) -> None:
        frame, menu = self._capture_menu()
        if self._visible(frame, menu, self.config["world_ready"]) is not None:
            return
        tab_offset = self._offset("world_tab_offset", [-800, 45, 1000, 90])
        if self._visible(frame, menu, self.config["world_tab"], tab_offset) is None:
            _, _, _, moved = self._hover_fresh(self._capture_hover_menu)
            if not moved:
                raise DryRunComplete("Dry run: menu anchor identified; cursor and game unchanged")
            frame, menu = self._capture_menu()
            if not self.inputs.click(frame, menu):
                raise DryRunComplete("Dry run: menu anchor identified; no click performed")
            frame, menu, _ = self._wait_target(self.config["world_tab"], offset=tab_offset)
        if self._visible(frame, menu, self.config["world_ready"]) is None:
            self._click_target(self.config["world_tab"], tab_offset)
        self._wait_target(self.config["world_ready"])

    def _close_world(self) -> None:
        # A node tooltip can hide the World header and even its close button.
        # First reveal the controls by moving onto the recognized menu anchor.
        # This is only a hover; current_stage itself never invokes this method.
        frame, menu, _, moved = self._hover_fresh(self._capture_hover_menu)
        if moved:
            self.stop.wait(self.hover_delay)
            frame, menu = self._capture_menu()
        header = self._visible(frame, menu, self.config["world_ready"])
        close = self._visible(frame, menu, self.config["close_world"])
        if header is not None or close is not None:
            self._click_target(self.config["close_world"])
            self._wait_target(self.config["world_ready"], absent=True)
            self._wait_target(self.config["close_world"], absent=True)

    def prepare_battle(self) -> Stage:
        """Close a recognized World panel, then prove the battlefield stage.

        Navigation callers may use this before reading their initial stage.
        Observation-only callers should continue using current_stage().
        """
        self._validate_references([
            self._target(self.config.get(key), key)
            for key in ("menu", "world_ready", "close_world")
        ])
        self._close_world()
        return self._wait_stage()

    def current_stage(self) -> Stage | None:
        """Return only a stage repeated on two consecutive fresh captures.

        Animated scenery can turn a visible 7 into an OCR 1 for one frame.
        No observation from a prior call is reused when the label is unstable.
        """
        self.last_label_evidence = None
        deadline = time.monotonic() + self.label_timeout
        hover_retry_logged = False
        while time.monotonic() < deadline:
            frame, menu = self._capture_menu()
            hover = self._box(frame.image, menu, self._offset("battle_hover_offset", [-310, -75, 24, 24]))
            try:
                moved = self.inputs.hover(frame, Match(*hover, 1.0, "battlefield label hover"), deadline=deadline)
            except HoverUnavailable:
                # Only an ownership acquisition timeout is transient. A fresh
                # menu/capture is required before rehover; focus, age, geometry
                # and emergency-stop errors are never caught here.
                if not hover_retry_logged:
                    log.warning("Battlefield hover temporarily unavailable; retrying with a fresh capture")
                    hover_retry_logged = True
                self.stop.wait(min(self.label_poll_interval, max(0, deadline - time.monotonic())))
                continue
            if moved:
                self.stop.wait(min(self.label_hover_delay, max(0, deadline - time.monotonic())))
            break
        previous = None
        while time.monotonic() < deadline:
            frame, menu = self._capture_menu()
            stage = self._read_stage_from_frame(frame, menu, deadline=deadline)
            if time.monotonic() >= deadline:
                break
            if stage is not None and stage == previous:
                return stage
            previous = stage
            self.stop.wait(min(self.label_poll_interval, max(0, deadline - time.monotonic())))
        log.warning("Battlefield stage label did not stabilize within %.2fs", self.label_timeout)
        return None

    def _parse_stage_lines(self, lines):
        text = " ".join(line.text for line in lines if line.confidence >= float(self.config.get("label_confidence", 0.25)))
        ids = {f"{int(a)}-{int(b)}" for a, b in _STAGE_ID.findall(text)}
        normalized = _normalized(text)
        difficulties = {name for name in self.config["difficulty_names"]
                        if re.search(r"(?<!\w)" + re.escape(_normalized(name)) + r"(?!\w)", normalized)}
        ambiguous = len(ids) > 1 or len(difficulties) > 1
        stage = Stage(next(iter(ids)), next(iter(difficulties))) if len(ids) == len(difficulties) == 1 else None
        return stage, ambiguous

    def _read_stage_from_frame(self, frame, menu: Match, *, deadline=None) -> Stage | None:
        """Require two preprocessing variants to agree, then temporal confirmation.

        A syntactically valid 2-58 is not more trustworthy than 2-B. Read all
        available variants: ties and multi-stage readings cannot authorize a
        stage. No expected ID, range of levels or character repair is used.
        """
        roi = self._normalized_box(frame.image, self._box(
            frame.image, menu, self._offset("current_stage_offset", [-570, 21, 560, 31])))
        evidence = self.last_label_evidence = {"frame": frame, "roi": roi, "reads": [], "selected": None}
        votes = {}
        ambiguous = False
        for name, reader in (("primary", self.label_vision), ("scaled", self.label_fallback_vision),
                             ("edges", self.label_edge_vision), ("edges_scaled", self.label_edge_confirm_vision)):
            if reader is None:
                continue
            self.stop.check()
            if deadline is not None and time.monotonic() >= deadline:
                return None
            try:
                lines = reader.read_lines(frame.image, roi=roi, mode="text", deadline=deadline)
            except VisionError as exc:
                evidence["error"] = str(exc)
                raise
            stage, multiple = self._parse_stage_lines(lines)
            ambiguous |= multiple
            evidence["reads"].append({"variant": name, "stage": stage.key if stage else None,
                                      "ambiguous": multiple,
                                      "lines": [{"text": line.text, "confidence": line.confidence} for line in lines]})
            if stage is not None:
                votes[stage] = votes.get(stage, 0) + 1
        if deadline is not None and time.monotonic() >= deadline:
            return None
        if ambiguous or not votes:
            return None
        highest = max(votes.values())
        winners = [stage for stage, count in votes.items() if count == highest]
        if highest < 2 or len(winners) != 1:
            return None
        evidence["selected"] = winners[0].key
        return winners[0]

    def _ready(self, page: dict) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            frame, menu = self._capture_menu()
            if all(self._visible(frame, menu, target) is not None for target in page["ready"]):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NavigationError(f"Selected act/difficulty not confirmed: {page['name']}")
            self.stop.wait(min(self.poll_interval, remaining))

    def _open_page(self, page: dict) -> None:
        self._open_world()
        frame, menu = self._capture_menu()
        if all(self._visible(frame, menu, target) is not None for target in page["ready"]):
            return
        self.execute_steps(page.get("enter", []))
        self._ready(page)

    @staticmethod
    def _golden(image, match: Match) -> bool:
        import cv2
        import numpy as np
        crop = image[match.y:match.y + match.height, match.x:match.x + match.width]
        if crop.size == 0:
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([18, 150, 160]), np.array([40, 255, 255]))
        return np.count_nonzero(mask) / mask.size >= 0.15

    def _nodes(self, frame, menu: Match) -> list[Match]:
        target = self._anchored(self.config["node_gold"], frame.image, menu,
                                self._offset("nodes_offset", [-740, 315, 850, 520]))
        if target is None:
            raise WorldAnchorUnavailable("World anchors are not uniquely visible; no node selection permitted")
        nodes = [match for match in self.vision.locate_all(frame.image, target) if self._golden(frame.image, match)]
        if len(nodes) > self.max_nodes:
            raise NavigationError(f"More than {self.max_nodes} gold-node matches; calibration is ambiguous")
        return sorted(nodes, key=lambda match: (match.y, match.x))

    def _capture_nodes(self):
        """Retry only missing panel evidence, never clicks or stale frames."""
        deadline = time.monotonic() + self.timeout
        attempts = 0
        while True:
            frame, menu = self._capture_menu()
            try:
                nodes = self._nodes(frame, menu)
                if attempts:
                    log.info("World anchors recovered after %d retries", attempts)
                return frame, menu, nodes
            except WorldAnchorUnavailable:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                attempts += 1
                if attempts == 1:
                    log.warning("World anchors temporarily hidden; retrying fresh captures for up to %.1fs", self.timeout)
                self.stop.wait(min(self.poll_interval, remaining))

    def _hint(self, node: Match, menu: Match) -> tuple[float, float]:
        x, y = node.center
        scale = menu.width / self.anchor_width
        return (x - menu.x) / scale, (y - menu.y) / scale

    def _node_at_hint(self, frame, menu: Match, hint: tuple[float, float]) -> Match:
        scale = menu.width / self.anchor_width
        expected = menu.x + hint[0] * scale, menu.y + hint[1] * scale
        nearby = [node for node in self._nodes(frame, menu)
                  if math.dist(node.center, expected) <= float(self.config.get("node_tolerance", 15)) * scale]
        if len(nearby) != 1:
            raise NodeUnavailable("Previously observed node is no longer uniquely gold; no input performed")
        return nearby[0]

    def _capture_node_at_hint(self, hint, *, deadline=None):
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        attempts = 0
        while True:
            frame, menu = self._capture_menu()
            try:
                node = self._node_at_hint(frame, menu, hint)
                if attempts:
                    log.info("Gold marker recovered after %d retries", attempts)
                return frame, menu, node
            except (NodeUnavailable, WorldAnchorUnavailable):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                attempts += 1
                if attempts == 1:
                    log.warning("Gold marker temporarily unconfirmed; retrying fresh captures for up to %.1fs", self.timeout)
                self.stop.wait(min(self.poll_interval, remaining))

    def _node_hover_match(self, node: Match) -> Match:
        """Keep the pointer inside the detected icon, below its usual center.

        On the observed map the tooltip follows the pointer; this lower point
        keeps top-row titles from sliding behind the game's menu tabs.
        """
        fx, fy = self.node_hover_fraction
        x = node.x + min(node.width - 1, int(node.width * fx))
        y = node.y + min(node.height - 1, int(node.height * fy))
        return Match(x, y, 1, 1, node.confidence, node.label)

    def _exclude_tooltip(self, page: dict, hint, exc: UnrecognizedTooltip) -> None:
        record = {"page": page["name"], "anchor_hint": list(hint), "reason": str(exc)}
        record.update(self._save_tooltip_diagnostic(page, hint, exc))
        self.excluded_nodes.append(record)
        log.warning("Gold node excluded on %s at hint %s: %s", page["name"], hint, exc)

    def _save_tooltip_diagnostic(self, page: dict, hint, exc: UnrecognizedTooltip) -> dict:
        """Persist the frame that actually failed; never capture or move here."""
        frame = exc.frame if exc.frame is not None else getattr(self.window, "last_frame", None)
        if frame is None:
            return {}
        try:
            from .desktop import save_frame
            import cv2
            directory = Path(self.config.get("diagnostics_dir", "artifacts"))
            if not directory.is_absolute():
                directory = Path(getattr(self.vision, "base_dir", ".")) / directory
            stem = f"map-{re.sub(r'[^A-Za-z0-9_-]', '_', page['name'])}-unreadable-{time.time_ns()}"
            screenshot = save_frame(frame, directory / f"{stem}.png")
            metadata = {"page": page["name"], "act": page["act"], "difficulty": page["difficulty"],
                        "anchor_hint": list(hint), "reason": str(exc), "ocr": exc.ocr,
                        "frame_file": str(screenshot)}
            paths = {"screenshot": str(screenshot)}
            if exc.menu is not None:
                x, y, w, h = self._tooltip_box(frame.image, exc.menu)
                crop_path = directory / f"{stem}-tooltip.png"
                ok, encoded = cv2.imencode(".png", frame.image[y:y + h, x:x + w])
                if not ok:
                    raise OSError("Could not encode tooltip diagnostic")
                crop_path.write_bytes(encoded.tobytes())
                metadata["tooltip_box"] = [x, y, w, h]
                paths["tooltip_screenshot"] = str(crop_path)
            details = directory / f"{stem}.json"
            details.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            paths["diagnostic"] = str(details)
            log.warning("Unreadable tooltip diagnostic: %s", screenshot)
            return paths
        except Exception as diagnostic_error:
            log.warning("Could not save the unreadable tooltip frame: %s", diagnostic_error)
            return {}

    def _tooltip_box(self, image, menu):
        if self.config.get("world_header_anchor") is not None:
            return self._panel_search_box(image, menu)
        return self._box(image, menu, self._offset("tooltip_offset", [-850, 35, 1150, 900]))

    def _tooltip_rois(self, image, menu: Match) -> list[list[float]]:
        import cv2
        import numpy as np
        x, y, w, h = self._tooltip_box(image, menu)
        scale = menu.width / self.anchor_width
        # Use the same bright-ink mask for region detection and OCR. Including
        # the darker gold borders merged a valid title with the act buttons,
        # producing an over-height contour that was incorrectly rejected.
        mask = self._tooltip_text_image(image[y:y + h, x:x + w])[:, :, 0]
        kernel = np.ones((max(1, round(3 * scale)), max(1, round(17 * scale))), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions = []
        height, width = image.shape[:2]
        margin = max(2, round(5 * scale))
        for contour in contours:
            a, b, cw, ch = cv2.boundingRect(contour)
            if cw < 120 * scale or not 12 * scale <= ch <= 55 * scale:
                continue
            left, top = max(0, x + a - margin), max(0, y + b - margin)
            right, bottom = min(width, x + a + cw + margin), min(height, y + b + ch + margin)
            regions.append(self._normalized_box(image, (left, top, right - left, bottom - top)))
        return sorted(regions, key=lambda roi: roi[1])[:12]

    @staticmethod
    def _tooltip_text_image(image):
        """Keep the observed bright gold title ink, excluding text behind it.

        The tooltip title can overlap white difficulty/tab text and a darker
        gold button. Grayscale merged these into malformed stage digits. This
        color mask preserves visible title pixels without repairing any ID.
        """
        import cv2
        import numpy as np
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([18, 150, 240]), np.array([40, 255, 255]))
        return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    def _read_tooltip(self, frame, menu: Match, act: int | None = None) -> str | None:
        ids = set()
        self._tooltip_diagnostics = []
        regions = self._tooltip_rois(frame.image, menu)
        if not regions:
            return None
        text_image = self._tooltip_text_image(frame.image)
        for roi in regions:
            for line in self.tooltip_vision.read_lines(text_image, roi=roi, mode="text"):
                self._tooltip_diagnostics.append({"roi": roi, "text": line.text, "confidence": line.confidence})
                if line.confidence < float(self.config.get("tooltip_confidence", 0.25)):
                    continue
                for first, second in _TOOLTIP_ID.findall(line.text):
                    if act is None or int(first) == act:
                        ids.add(f"{int(first)}-{int(second)}")
        return next(iter(ids)) if len(ids) == 1 else None

    def _clear_tooltip(self, *, deadline=None) -> None:
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        _, _, _, moved = self._hover_fresh(self._capture_hover_menu, deadline=deadline)
        if not moved:
            raise DryRunComplete("Dry run: tooltip clearing would hover the menu; no movement performed")
        while True:
            self.stop.wait(min(self.hover_delay, max(0, deadline - time.monotonic())))
            frame, menu = self._capture_menu()
            cleared = self._read_tooltip(frame, menu) is None
            if time.monotonic() >= deadline:
                raise NavigationError("Previous stage tooltip did not disappear")
            if cleared:
                return

    def _hover_node(self, page: dict, hint: tuple[float, float]) -> Stage:
        deadline = time.monotonic() + self.timeout
        self._clear_tooltip(deadline=deadline)

        def acquire_node():
            frame, menu, node = self._capture_node_at_hint(hint, deadline=deadline)
            return frame, menu, self._node_hover_match(node)

        _, _, _, moved = self._hover_fresh(acquire_node, deadline=deadline)
        if not moved:
            raise DryRunComplete("Dry run: gold node identified; no hover performed")
        previous = None
        while True:
            self.stop.wait(min(self.hover_delay, max(0, deadline - time.monotonic())))
            frame, menu = self._capture_menu()
            current = self._read_tooltip(frame, menu, page["act"])
            if time.monotonic() >= deadline:
                raise UnrecognizedTooltip("Gold-node tooltip did not show a stable bracketed stage ID",
                                          frame=frame, menu=menu, ocr=self._tooltip_diagnostics)
            if current is not None and current == previous:
                return Stage(current, page["difficulty"])
            previous = current

    def inspect_page(self, name: str) -> list[Stage]:
        self.preflight(selection=False)
        page = next((item for item in self._pages() if item["name"] == name), None)
        if page is None:
            raise CalibrationError(f"Unknown map page: {name}")
        initial = self.prepare_battle()
        self._open_page(page)
        self._clear_tooltip()
        frame, menu, nodes = self._capture_nodes()
        hints = [self._hint(node, menu) for node in nodes]
        found = {}
        for hint in hints:
            try:
                stage = self._hover_node(page, hint)
            except UnrecognizedTooltip as exc:
                self._exclude_tooltip(page, hint, exc)
                continue
            if stage.key in found:
                raise NavigationError(f"Different gold nodes reported the same tooltip ID: {stage.key}")
            found[stage.key] = stage
            self._hints[stage.key] = page["name"], hint
            log.info("Unlocked stage recognized: %s (page %s)", stage.key, page["name"])
        self._close_world()
        self._wait_stage(initial)
        return sorted(found.values(), key=lambda stage: stage.position)

    def discover(self) -> list[Stage]:
        self.preflight(selection=False)
        found = {}
        self._hints.clear()
        self.excluded_nodes.clear()
        for page in self._pages():
            for stage in self.inspect_page(page["name"]):
                found[stage.key] = stage
        return sorted(found.values(), key=lambda stage: (stage.difficulty, stage.position))

    def select(self, stage: Stage) -> None:
        self.preflight()
        if self.prepare_battle() == stage:
            return
        pages = [page for page in self._pages() if page["act"] == stage.position[0]
                 and page["difficulty"] == stage.difficulty]
        known = self._hints.get(stage.key)
        if known:
            pages = [page for page in pages if page["name"] == known[0]]
        if len(pages) != 1:
            raise NavigationError(f"No unique calibrated map page for {stage.key}")
        page = pages[0]
        self._open_page(page)
        self._clear_tooltip()
        frame, menu, nodes = self._capture_nodes()
        hints = [self._hint(node, menu) for node in nodes]
        if known:
            hints.sort(key=lambda hint: math.dist(hint, known[1]))
        selected_hint = None
        for hint in hints:
            try:
                observed = self._hover_node(page, hint)
            except UnrecognizedTooltip as exc:
                self._exclude_tooltip(page, hint, exc)
                continue
            self._hints[observed.key] = page["name"], hint
            if observed == stage:
                selected_hint = hint
                break
        if selected_hint is None:
            raise NavigationError(f"No gold marker with a confirmed tooltip for {stage.key}")
        # Recheck both positive gold and the tooltip on the exact click frame.
        frame, menu, node = self._capture_node_at_hint(selected_hint)
        if self._read_tooltip(frame, menu, page["act"]) != stage.stage:
            raise NavigationError("Tooltip changed before stage selection")
        if not self.inputs.click(frame, node):
            raise DryRunComplete(f"Dry run: confirmed {stage.key}; no click performed")
        self.stop.wait(self.hover_delay)
        self._close_world()
        self._wait_stage(stage)
        log.info("Direct map selection confirmed: %s", stage.key)

