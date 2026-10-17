"""Calibrated, visually checked navigation through the game's normal UI.

Routes describe screenshots supplied by the user; this module never invents a
map layout or treats the absence of a lock as evidence that a stage is open.
"""

from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Stage
from .vision import Match

log = logging.getLogger(__name__)
_STAGE_ID = re.compile(r"(?<!\d)([1-9]\d*)\s*[-–—]\s*([1-9]\d*)(?!\d)")


class NavigationError(RuntimeError):
    """The visible state did not prove that navigation was safe to continue."""


class CalibrationError(NavigationError):
    """A necessary route or visual reference has not been calibrated."""


class DryRunComplete(NavigationError):
    """The first intended click was identified, without changing the UI."""


@dataclass(frozen=True)
class _StageCard:
    stage: Stage
    match: Match
    roi: tuple[float, float, float, float]


def _normalized(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _roi(value: Any, name: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise CalibrationError(f"{name}: use [x, y, width, height] normalized to the window")
    try:
        x, y, width, height = (float(item) for item in value)
    except (ValueError, TypeError) as exc:
        raise CalibrationError(f"{name}: ROI contains invalid values") from exc
    if not all(math.isfinite(item) for item in (x, y, width, height)) or not (
        0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0
        and x + width <= 1.000001 and y + height <= 1.000001
    ):
        raise CalibrationError(f"{name}: ROI must fit inside the client area")
    return x, y, width, height


class Navigator:
    def __init__(self, window, vision, inputs, stop, config: dict):
        self.window = window
        self.vision = vision
        self.inputs = inputs
        self.stop = stop
        self.config = config
        self.timeout = float(config.get("timeout", 8))
        self.poll_interval = float(config.get("poll_interval", 0.5))
        self.retries = int(config.get("retries", 2))
        self.stage_confidence = float(config.get("stage_confidence", 0.55))
        if not (math.isfinite(self.timeout) and self.timeout >= 0):
            raise CalibrationError("navigation.timeout must be finite and non-negative")
        if not (math.isfinite(self.poll_interval) and self.poll_interval > 0):
            raise CalibrationError("navigation.poll_interval must be positive")
        if not (0 <= self.retries <= 20 and 0 <= self.stage_confidence <= 1):
            raise CalibrationError("Invalid navigation retries or stage_confidence")
        self._stage_pages: dict[str, str] = {}

    def _target(self, value: Any, name: str) -> dict:
        if not isinstance(value, dict) or sum(
            key in value for key in ("text", "template", "kind")
        ) != 1:
            raise CalibrationError(f"{name}: provide exactly one text, template or kind target")
        for key in ("text", "template"):
            if key in value and (not isinstance(value[key], str) or not value[key].strip()):
                raise CalibrationError(f"{name}: {key} reference cannot be empty")
        if "text" in value:
            try:
                pattern = re.compile(value["text"])
            except re.error as exc:
                raise CalibrationError(f"{name}: invalid text regular expression") from exc
            if pattern.search(""):
                raise CalibrationError(f"{name}: text expression must not match empty text")
        if "kind" in value and value["kind"] != "menu":
            raise CalibrationError(f"{name}: the only built-in visual detector is kind: menu")
        if "roi" in value:
            _roi(value["roi"], name + ".roi")
        return value

    def _steps(self, value: Any, name: str, *, required: bool = False) -> list[dict]:
        if not isinstance(value, list) or (required and not value):
            raise CalibrationError(f"{name}: configure a list of click/expect steps")
        for number, step in enumerate(value, 1):
            if not isinstance(step, dict):
                raise CalibrationError(f"{name}[{number}]: invalid navigation step")
            self._target(step.get("click"), f"{name}[{number}].click")
            self._target(step.get("expect"), f"{name}[{number}].expect")
        return value

    def _pages(self) -> list[dict]:
        if self.config.get("calibrated") is not True:
            raise CalibrationError(
                "World navigation is not calibrated. Configure observed click/expect "
                "routes, page markers and positive unlocked markers first."
            )
        self._steps(self.config.get("open_world"), "open_world", required=True)
        self._steps(self.config.get("return_to_battle"), "return_to_battle", required=True)
        _roi(self.config.get("current_stage_roi"), "current_stage_roi")
        names = self.config.get("difficulty_names", [])
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
            raise CalibrationError("Configure difficulty_names using the game's visible labels")
        pages = self.config.get("pages")
        if not isinstance(pages, list) or not pages:
            raise CalibrationError("Configure at least one reachable World page")
        seen = set()
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("name"), str) or not page["name"]:
                raise CalibrationError("Every World page needs a unique name")
            if page["name"] in seen:
                raise CalibrationError(f"Repeated page name: {page['name']}")
            seen.add(page["name"])
            if page.get("difficulty") not in names:
                raise CalibrationError(f"{page['name']}: difficulty must be in difficulty_names")
            if "act" in page and (not isinstance(page["act"], int) or page["act"] < 1):
                raise CalibrationError(f"{page['name']}: act must be a positive integer")
            self._steps(page.get("enter", []), page["name"] + ".enter")
            self._target(page.get("ready"), page["name"] + ".ready")
            self._target(page.get("unlocked"), page["name"] + ".unlocked")
            for optional in ("locked", "available"):
                if optional in page:
                    self._target(page[optional], page["name"] + "." + optional)
            _roi(page.get("stages_roi"), page["name"] + ".stages_roi")
            padding = page.get("card_padding", [0, 0, 0, 0])
            if not isinstance(padding, (list, tuple)) or len(padding) != 4:
                raise CalibrationError(f"{page['name']}: card_padding needs four normalized margins")
            try:
                valid_padding = all(math.isfinite(float(v)) and 0 <= float(v) < 1 for v in padding)
            except (ValueError, TypeError):
                valid_padding = False
            if not valid_padding:
                raise CalibrationError(f"{page['name']}: invalid card_padding")
        return pages

    def _validate_references(self, targets: list[dict]) -> None:
        """Validate every required reference before starting a live route."""
        validator = getattr(self.vision, "validate_target", None)
        for target in targets:
            self._target(target, "visual reference")
            if callable(validator):
                validator(target)
            elif "template" in target:
                path = Path(target["template"])
                if not path.is_absolute():
                    path = Path(getattr(self.vision, "base_dir", ".")) / path
                if not path.is_file():
                    raise CalibrationError(f"Template file not found: {path}")

    @staticmethod
    def _step_targets(steps: list[dict]) -> list[dict]:
        return [step[key] for step in steps for key in ("click", "expect")]

    def preflight(self, selection: bool = True) -> None:
        """Check all configured pages without interacting with the game.

        Discovery does not need stage-detail references; a full measurement run
        must call this with the default selection=True before discovery starts.
        """
        pages = self._pages()
        targets = self._step_targets(self.config["open_world"])
        targets.extend(self._step_targets(self.config["return_to_battle"]))
        for page in pages:
            targets.extend(self._step_targets(page.get("enter", [])))
            targets.extend(page[key] for key in ("ready", "unlocked", "locked", "available") if key in page)
            if selection:
                targets.append(self._target(page.get("select_expect"), page["name"] + ".select_expect"))
                confirmations = self._steps(page.get("select_confirm", []), page["name"] + ".select_confirm")
                targets.extend(self._step_targets(confirmations))
        repeat = self.config.get("repeat")
        if selection and repeat:
            if not isinstance(repeat, dict):
                raise CalibrationError("repeat must contain active and enable references")
            targets.append(self._target(repeat.get("active"), "repeat.active"))
            targets.extend(self._step_targets(self._steps([repeat.get("enable")], "repeat.enable")))
        self._validate_references(targets)

    def _find(self, target: dict, description: str, *, retries: int = 0):
        """Retry recognition only. A click is never replayed after a timeout."""
        self._target(target, description)
        for attempt in range(retries + 1):
            deadline = time.monotonic() + self.timeout
            while True:
                self.stop.check()
                frame = self.window.capture()
                match = self.vision.locate(frame.image, target)
                if match is not None:
                    return frame, match
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.stop.wait(min(self.poll_interval, remaining))
            log.warning("Visual target missing: %s (attempt %d/%d)", description, attempt + 1, retries + 1)
        raise NavigationError(f"Visual target not found: {description}")

    def execute_steps(self, steps: list[dict]) -> None:
        """Run a calibrated route, stopping at the first unconfirmed transition."""
        self._steps(steps, "route")
        self._validate_references(self._step_targets(steps))
        for number, step in enumerate(steps, 1):
            frame, match = self._find(step["click"], f"step {number} click", retries=self.retries)
            self.stop.check()
            if not self.inputs.click(frame, match):
                raise DryRunComplete(f"Dry run: located step {number}: {match.label}; no click performed")
            self._find(step["expect"], f"step {number} expected screen")

    def _stage_from_frame(self, frame) -> Stage | None:
        roi = _roi(self.config.get("current_stage_roi"), "current_stage_roi")
        lines = self.vision.read_lines(frame.image, roi=roi, mode="text")
        text = " ".join(line.text for line in lines if line.confidence >= self.stage_confidence)
        ids = {f"{int(match[0])}-{int(match[1])}" for match in _STAGE_ID.findall(text)}
        folded = _normalized(text)
        difficulties = {
            name for name in self.config.get("difficulty_names", [])
            if re.search(r"(?<!\w)" + re.escape(_normalized(name)) + r"(?!\w)", folded)
        }
        if len(ids) != 1 or len(difficulties) != 1:
            return None
        return Stage(next(iter(ids)), next(iter(difficulties)))

    def current_stage(self) -> Stage | None:
        self.stop.check()
        return self._stage_from_frame(self.window.capture())

    def _wait_stage(self, expected: Stage | None = None) -> Stage:
        deadline = time.monotonic() + self.timeout
        while True:
            self.stop.check()
            stage = self.current_stage()
            if stage is not None and (expected is None or stage == expected):
                return stage
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                wanted = expected.key if expected else "a unique stage and difficulty"
                raise NavigationError(f"Battlefield label did not confirm {wanted}")
            self.stop.wait(min(self.poll_interval, remaining))

    @staticmethod
    def _card_target(target: dict, card_roi: tuple[float, float, float, float]) -> dict | None:
        result = dict(target)
        left, top, width, height = card_roi
        right, bottom = left + width, top + height
        if "roi" in target:
            tx, ty, tw, th = _roi(target["roi"], "card detector roi")
            left, top, right, bottom = max(left, tx), max(top, ty), min(right, tx + tw), min(bottom, ty + th)
        if right <= left or bottom <= top:
            return None
        result["roi"] = [left, top, right - left, bottom - top]
        return result

    def _scan_page(self, page: dict, frame, requested: Stage | None = None) -> list[_StageCard]:
        height, width = frame.image.shape[:2]
        padding = [float(value) for value in page.get("card_padding", [0, 0, 0, 0])]
        lines = self.vision.read_lines(frame.image, roi=page["stages_roi"], mode="text")
        candidates: list[_StageCard] = []
        seen = set()
        for line in lines:
            ids = _STAGE_ID.findall(line.text)
            if line.confidence < self.stage_confidence or len(ids) != 1:
                continue
            act, level = map(int, ids[0])
            stage = Stage(f"{act}-{level}", page["difficulty"])
            if page.get("act") is not None and act != page["act"]:
                continue
            if stage.key in seen:
                raise NavigationError(f"Ambiguous duplicate stage label {stage.key} on {page['name']}")
            seen.add(stage.key)
            x, y, w, h = line.box
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
                continue
            left, top = max(0, x / width - padding[0]), max(0, y / height - padding[1])
            right = min(1, (x + w) / width + padding[2])
            bottom = min(1, (y + h) / height + padding[3])
            match = Match(x, y, w, h, line.confidence, stage.stage)
            candidates.append(_StageCard(stage, match, (left, top, right - left, bottom - top)))
        unlocked = []
        for card in candidates:
            if requested is not None and card.stage != requested:
                continue
            x, y, w, h = card.roi
            for other in candidates:
                if other is card:
                    continue
                cx, cy = other.match.center
                if x <= cx / width <= x + w and y <= cy / height <= y + h:
                    raise CalibrationError(
                        f"{page['name']}: card_padding for {card.stage.stage} includes "
                        f"{other.stage.stage}; tighten the card regions"
                    )
            locked_target = self._card_target(page["locked"], card.roi) if "locked" in page else None
            if locked_target is not None:
                # Every lock is a veto; multiple matches must not hide a lock.
                locked_target["strict"] = False
            if locked_target is not None and self.vision.locate(frame.image, locked_target) is not None:
                log.info("Locked stage ignored: %s", card.stage.key)
                continue
            unlocked_target = self._card_target(page["unlocked"], card.roi)
            if unlocked_target is not None and self.vision.locate(frame.image, unlocked_target) is not None:
                unlocked.append(card)
            else:
                log.info("Stage ignored without positive unlocked evidence: %s", card.stage.key)
        return unlocked

    def _open_page(self, page: dict):
        self.execute_steps(self.config["open_world"])
        if "available" in page:
            self.stop.check()
            frame = self.window.capture()
            if self.vision.locate(frame.image, page["available"]) is None:
                log.info("World page not visibly available: %s", page["name"])
                return None
        self.execute_steps(page.get("enter", []))
        frame, _ = self._find(page["ready"], f"World page {page['name']}")
        return frame

    def inspect_page(self, name: str) -> list[Stage]:
        self.preflight(selection=False)
        pages = self._pages()
        page = next((candidate for candidate in pages if candidate["name"] == name), None)
        if page is None:
            raise CalibrationError(f"Unknown configured World page: {name}")
        initial = self._wait_stage()
        frame = self._open_page(page)
        cards = self._scan_page(page, frame) if frame is not None else []
        self.execute_steps(self.config["return_to_battle"])
        self._wait_stage(initial)
        for card in cards:
            self._stage_pages.setdefault(card.stage.key, page["name"])
        return [card.stage for card in cards]

    def discover(self) -> list[Stage]:
        self.preflight(selection=False)
        pages = self._pages()
        self._stage_pages.clear()
        stages = {}
        for page in pages:
            for stage in self.inspect_page(page["name"]):
                stages[stage.key] = stage
        return sorted(stages.values(), key=lambda stage: (stage.difficulty, stage.position))

    def select(self, stage: Stage) -> None:
        self.preflight()
        pages = self._pages()
        if self.current_stage() == stage:
            return
        known_page = self._stage_pages.get(stage.key)
        candidates = [
            page for page in pages
            if page["difficulty"] == stage.difficulty
            and (page.get("act") is None or page["act"] == stage.position[0])
            and (known_page is None or page["name"] == known_page)
        ]
        if len(candidates) != 1:
            raise NavigationError(f"Discover World pages first to uniquely locate {stage.key}")
        page = candidates[0]
        self._target(page.get("select_expect"), page["name"] + ".select_expect")
        confirmations = self._steps(page.get("select_confirm", []), page["name"] + ".select_confirm")
        self._wait_stage()
        frame = self._open_page(page)
        if frame is None:
            raise NavigationError(f"Page for {stage.key} is not visibly available")
        # Unlock evidence and coordinates must come from this fresh page capture,
        # never from discovery's historical list.
        frame = self.window.capture()
        card = next(iter(self._scan_page(page, frame, requested=stage)), None)
        if card is None:
            raise NavigationError(f"Stage {stage.key} is absent, locked or not positively unlocked")
        self.stop.check()
        if not self.inputs.click(frame, card.match):
            raise DryRunComplete(f"Dry run: located unlocked stage {stage.key}; no click performed")
        self._find(page["select_expect"], f"selection of {stage.key}")
        self.execute_steps(confirmations)
        self._wait_stage(stage)
        log.info("Battlefield confirmed: %s", stage.key)

    def ensure_farming(self, stage: Stage) -> bool:
        self._wait_stage(stage)
        repeat = self.config.get("repeat")
        if not repeat:
            log.info("Repeat control not calibrated; automatic repeats must be enabled in the game")
            return False
        self._target(repeat.get("active"), "repeat.active")
        enable = self._steps([repeat.get("enable")], "repeat.enable", required=True)
        self._validate_references([repeat["active"], *self._step_targets(enable)])
        frame = self.window.capture()
        if self.vision.locate(frame.image, repeat["active"]) is not None:
            return True
        # Missing 'active' alone does not prove the toggle is off: the click
        # target must be a distinct, calibrated inactive-state marker.
        self.execute_steps(enable)
        self._find(repeat["active"], "repeat active state")
        self._wait_stage(stage)
        return True
