"""Lazy OpenCV/Tesseract recognition; returned boxes use original image pixels."""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import json
import logging
import math
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)


class VisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class Match:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    label: str

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def _dependencies():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError as exc:
        raise VisionError("Instale OpenCV e NumPy de requirements.txt para reconhecimento visual.") from exc


def _roi_box(image: Any, roi: Any = None) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        raise VisionError("Imagem vazia.")
    if roi is None:
        return 0, 0, width, height
    if len(roi) != 4:
        raise VisionError("ROI deve conter [x, y, largura, altura] normalizados.")
    x, y, w, h = map(float, roi)
    if not all(math.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
        raise VisionError("ROI deve caber inteiramente na imagem (valores de 0 a 1).")
    left, top = int(x * width), int(y * height)
    right, bottom = min(width, round((x + w) * width)), min(height, round((y + h) * height))
    if right <= left or bottom <= top:
        raise VisionError("ROI pequena demais para esta resolucao.")
    return left, top, right - left, bottom - top


def _nms(matches: list[Match], overlap: float = 0.3) -> list[Match]:
    kept: list[Match] = []
    for candidate in sorted(matches, key=lambda match: match.confidence, reverse=True):
        duplicate = False
        for other in kept:
            intersection = max(0, min(candidate.x + candidate.width, other.x + other.width) - max(candidate.x, other.x)) * max(0, min(candidate.y + candidate.height, other.y + other.height) - max(candidate.y, other.y))
            union = candidate.width * candidate.height + other.width * other.height - intersection
            if union and intersection / union > overlap:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def _resampling_modes(target: dict) -> tuple[str, ...]:
    value = target.get("resampling", "default")
    modes = [value] if isinstance(value, str) else value
    if (not isinstance(modes, (list, tuple)) or not 1 <= len(modes) <= 3
            or any(mode not in ("default", "linear", "nearest") for mode in modes)):
        raise VisionError("Invalid template resampling")
    return tuple(dict.fromkeys(modes))


class Vision:
    def __init__(self, config: dict, base_dir: Path):
        self.config = config
        self.base_dir = Path(base_dir)
        # Cache recognition only, never battle events. Identical visible rows
        # still produce separate OCRLine objects in their current positions.
        self._log_row_cache = OrderedDict()

    def validate_target(self, target: dict) -> None:
        """Preflight a configured target without capturing or touching the UI."""
        if not isinstance(target, dict):
            raise VisionError("Alvo visual deve ser um objeto de configuracao.")
        kinds = sum(("text" in target, "template" in target, target.get("kind") == "menu"))
        if kinds != 1:
            raise VisionError("Configure exatamente text, template ou kind: menu para cada alvo.")
        try:
            threshold = float(target.get("threshold", self.config.get("match_threshold", 0.88)))
            if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                raise ValueError("threshold deve estar entre 0 e 1")
            _roi_box(SimpleNamespace(shape=(10000, 10000)), target.get("roi"))
            if "text" in target:
                re.compile(str(target["text"]), re.IGNORECASE)
            if "template" in target:
                if "template_variants" in target:
                    for variant in self._template_targets(target):
                        self.validate_target(variant)
                    return
                self._read_template(target)
                _resampling_modes(target)
                scales = target.get("scales", [0.8, 0.9, 1.0, 1.1, 1.2])
                if not isinstance(scales, (list, tuple)) or not 1 <= len(scales) <= 30:
                    raise ValueError("Configure de 1 a 30 escalas de template")
                if any(not math.isfinite(float(scale)) or not 0 < float(scale) <= 8 for scale in scales):
                    raise ValueError("Escalas de template devem estar entre 0 e 8")
        except (ValueError, TypeError, re.error) as exc:
            raise VisionError(f"Configuracao de alvo invalida: {exc}") from exc

    def _ocr(self):
        try:
            import pytesseract
        except ImportError as exc:
            raise VisionError("Instale pytesseract e o executavel local Tesseract OCR.") from exc
        command = self.config.get("tesseract_cmd")
        if command:
            pytesseract.pytesseract.tesseract_cmd = str(command)
        return pytesseract

    def read_lines(self, image: Any, roi=None, mode: str = "text", *, deadline: float | None = None) -> list[OCRLine]:
        if mode not in ("text", "logs"):
            raise ValueError("Modo OCR deve ser text ou logs.")
        cv2, np = _dependencies()
        x, y, width, height = _roi_box(image, roi)
        crop = image[y:y + height, x:x + width]
        if mode == "logs" and self.config.get("logs_linewise", False):
            return self._read_log_rows(crop, x, y, deadline=deadline)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
        if mode == "text" and self.config.get("white_text", False) and crop.ndim == 3:
            # Footer labels sit over animated scenery. Isolate their neutral,
            # bright pixels instead of thresholding the scenery with the text.
            saturation = int(self.config.get("white_text_saturation_max", 50))
            value = int(self.config.get("white_text_value_min", 190))
            if not 0 <= saturation <= 255 or not 0 <= value <= 255:
                raise VisionError("Limites HSV para white_text devem estar entre 0 e 255.")
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            gray = cv2.inRange(hsv, np.array([0, 0, value]), np.array([179, saturation, 255]))
        elif mode == "logs" and self.config.get("green_logs", True) and crop.ndim == 3:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            # Keep green rewards, red defeats and neutral labels. Selecting only
            # green silently drops defeats when victory and defeat rows coexist.
            colored = cv2.inRange(hsv, np.array([0, 35, 65]), np.array([179, 255, 255]))
            neutral = cv2.inRange(hsv, np.array([0, 0, 130]), np.array([179, 80, 255]))
            mask = cv2.bitwise_or(colored, neutral)
            if np.count_nonzero(mask) > mask.size * 0.001:
                gray = mask
        scale = float(self.config.get("ocr_scale", 3.0))
        if not math.isfinite(scale) or not 1 <= scale <= 6:
            raise VisionError("ocr_scale deve estar entre 1 e 6.")
        enlarged = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if np.mean(binary) < 127:
            binary = cv2.bitwise_not(binary)
        psm = int(self.config.get(f"{mode}_psm", 6))
        timeout = None
        if deadline is not None:
            timeout = min(float(self.config.get("ocr_timeout", 5.0)), deadline - time.monotonic())
            if not math.isfinite(timeout) or timeout <= 0:
                raise VisionError("Prazo compartilhado de OCR esgotado.")
        return self._read_processed(binary, x, y, scale, psm, timeout=timeout)

    def _read_processed(self, pixels: Any, x: float, y: float, scale: float, psm: int, timeout: float | None = None) -> list[OCRLine]:
        if not 3 <= psm <= 13:
            raise VisionError("PSM do Tesseract deve estar entre 3 e 13.")
        language = str(self.config.get("language", "por+eng"))
        if not re.fullmatch(r"[A-Za-z0-9_+]+", language):
            raise VisionError("Identificador de idioma Tesseract invalido.")
        timeout = float(self.config.get("ocr_timeout", 5.0)) if timeout is None else timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise VisionError("ocr_timeout deve ser positivo e finito.")
        engine = self._ocr()
        try:
            data = engine.image_to_data(pixels, lang=language, config=f"--psm {psm}", output_type=engine.Output.DICT, timeout=timeout)
        except (RuntimeError, OSError) as exc:
            raise VisionError(f"Falha no OCR: {exc}. Confira Tesseract, idiomas instalados e o timeout.") from exc
        minimum = float(self.config.get("min_confidence", 0.45))
        if minimum > 1:
            minimum /= 100
        groups: dict[tuple, list[tuple]] = {}
        for index, raw in enumerate(data.get("text", [])):
            content = str(raw).strip()
            confidence = float(data["conf"][index]) / 100
            if not content or confidence < minimum:
                continue
            key = tuple(data.get(name, [0] * len(data["text"]))[index] for name in ("block_num", "par_num", "line_num"))
            box = tuple(int(data[name][index]) for name in ("left", "top", "width", "height"))
            groups.setdefault(key, []).append((content, confidence, box))
        result = []
        for words in groups.values():
            words.sort(key=lambda word: word[2][0])
            left = min(word[2][0] for word in words)
            top = min(word[2][1] for word in words)
            right = max(word[2][0] + word[2][2] for word in words)
            bottom = max(word[2][1] + word[2][3] for word in words)
            box = (int(x + left / scale), int(y + top / scale), max(1, math.ceil((right - left) / scale)), max(1, math.ceil((bottom - top) / scale)))
            result.append(OCRLine(" ".join(word[0] for word in words), sum(word[1] for word in words) / len(words), box))
        return sorted(result, key=lambda line: (line.box[1], line.box[0]))

    def _read_log_rows(self, crop: Any, x: int, y: int, *, deadline: float | None = None) -> list[OCRLine]:
        """Read each projected text row independently, preserving occurrences.

        Green victory rows retain their original antialiasing while yellow coin
        pixels are removed. Other colors (loot, defeats) keep their grayscale.
        No recognition result is repaired or substituted with an expected stage.
        """
        cv2, np = _dependencies()
        scale = float(self.config.get("ocr_scale", 3.0))
        timeout = float(self.config.get("logs_ocr_timeout", self.config.get("ocr_timeout", 5.0)))
        if not math.isfinite(scale) or not 1 <= scale <= 6:
            raise VisionError("ocr_scale deve estar entre 1 e 6.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise VisionError("ocr_timeout deve ser positivo e finito.")
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
        active = np.flatnonzero(np.count_nonzero(gray > 50, axis=1) >= 2)
        spans: list[tuple[int, int]] = []
        if active.size:
            start = previous = int(active[0])
            for value in active[1:]:
                current = int(value)
                if current - previous > 3:
                    if previous - start >= 2:
                        spans.append((start, previous + 1))
                    start = current
                previous = current
            if previous - start >= 2:
                spans.append((start, previous + 1))
        if len(spans) > 32:
            raise VisionError("Mais de 32 linhas no ROI de logs; ajuste a calibracao.")
        deadline = min(deadline, time.monotonic() + timeout) if deadline is not None else time.monotonic() + timeout
        settings_key = json.dumps(self.config, sort_keys=True, default=str)
        result = []
        for top, bottom in spans:
            row = gray[top:bottom].copy()
            if crop.ndim == 3:
                hsv = cv2.cvtColor(crop[top:bottom], cv2.COLOR_BGR2HSV)
                green = cv2.inRange(hsv, np.array([30, 10, 10]), np.array([95, 255, 255]))
                bright = row > 50
                if np.count_nonzero((green > 0) & bright) > np.count_nonzero(bright) * 0.55:
                    row = cv2.bitwise_and(row, green)
            ys, xs = np.where(row > 50)
            if not xs.size:
                result.append(OCRLine("", 0.0, (x, y + top, crop.shape[1], bottom - top)))
                continue
            left, right = int(xs.min()), int(xs.max()) + 1
            upper, lower = int(ys.min()), int(ys.max()) + 1
            row = row[upper:lower, left:right]
            key = (settings_key, row.shape, row.tobytes())
            if key in self._log_row_cache:
                text, confidence = self._log_row_cache[key]
                self._log_row_cache.move_to_end(key)
            else:
                text, confidence, confirmed = self._recognize_log_row(
                    row, x + left, y + top + upper, scale, deadline)
                if confirmed:
                    self._log_row_cache[key] = (text, confidence)
                    while len(self._log_row_cache) > 128:
                        self._log_row_cache.popitem(last=False)
            # An unreadable visual row remains present so partial snapshots
            # cannot masquerade as a shift of the rolling battle log.
            result.append(OCRLine(text, confidence, (x + left, y + top + upper, right - left, lower - upper)))
        return result

    def _recognize_log_row(self, row, x, y, scale, deadline):
        """Require two matching numerical reads; retry pixels, never fix digits.

        Resizing changes whether tiny decimal dots survive Tesseract's internal
        threshold. Bounded attempts allow a missing dot to be recovered
        and a conflicting numerical read to be outvoted, without expected gold,
        stage, duration or rewards being supplied to the OCR engine.
        """
        from .models import ParsedBattle, UntimedDefeat
        from .results import parse_log_entry

        cv2, _ = _dependencies()
        # Subpixel rendering changes a decimal even when the same text merely
        # moves to another log row. For tiny glyphs, grayscale scaling alone
        # is insufficient: also threshold AFTER enlarging, preserving the dot.
        # Larger fonts keep their proven grayscale path.
        if row.shape[0] <= 12:
            variants = [(scale, False), (3.0, False), (3.0, True), (4.0, True),
                        (6.0, False), (2.0, True), (4.0, False)]
            # A binary view at 2x can preserve a decimal lost in gray
            # without the digit change caused by a larger binary rendering.
            # A final grayscale 4x view also avoids a separator becoming an
            # extra reward sign. Both still need an independent matching read.
        else:
            scales = [scale] + [candidate for candidate in (2.0, 3.0, 4.0) if candidate != scale]
            variants = [(factor, False) for factor in scales[:3]]
        variants = list(dict.fromkeys(variants))
        votes = {}
        attempts = []
        for factor, binary in variants:
            enlarged = cv2.resize(row, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
            if binary:
                _, enlarged = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            padding = 18 if row.shape[0] <= 12 else max(6, round(6 * factor))
            # Grayscale remains first: hard masks at native resolution changed
            # real 9s to 0s. A binary alternative alone is never confirmation.
            pixels = cv2.copyMakeBorder(255 - enlarged, padding, padding, padding, padding,
                                       cv2.BORDER_CONSTANT, value=255)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VisionError("Timeout total do OCR por linha excedido.")
            parts = self._read_processed(pixels, x - padding / factor, y - padding / factor,
                                         factor, int(self.config.get("logs_line_psm", 7)), remaining)
            text = " ".join(part.text for part in sorted(parts, key=lambda part: part.box[0]))
            confidence = sum(part.confidence for part in parts) / len(parts) if parts else 0.0
            attempts.append((text, confidence))
            entry = parse_log_entry(text)
            if isinstance(entry, (ParsedBattle, UntimedDefeat)):
                matching = votes.setdefault(entry.fingerprint, [])
                matching.append((text, confidence))
                if len(matching) >= 2:
                    best = max(matching, key=lambda item: item[1])
                    return *best, True
            elif entry is not None and not votes:
                return text, confidence, True
        log.debug("Linha sem consenso OCR: %s", [text for text, _ in attempts])
        # Never expose a valid but unconfirmed battle to the collector. Unknown
        # text remains visible for diagnostics and custom notification patterns.
        if votes:
            return "", 0.0, False
        return *attempts[0], False

    def locate(self, image: Any, target: dict) -> Match | None:
        matches = self.locate_all(image, target)
        if target.get("strict", True) and len(matches) != 1:
            if len(matches) > 1:
                log.warning("Alvo ambiguo (%d correspondencias): %s", len(matches), target)
            return None
        return max(matches, key=lambda match: match.confidence) if matches else None

    def locate_all(self, image: Any, target: dict) -> list[Match]:
        if not isinstance(target, dict):
            raise VisionError("Alvo visual deve ser um objeto de configuracao.")
        threshold = float(target.get("threshold", self.config.get("match_threshold", 0.88)))
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise VisionError("threshold deve estar entre 0 e 1.")
        kinds = sum(("text" in target, "template" in target, target.get("kind") == "menu"))
        if kinds != 1:
            raise VisionError("Configure exatamente text, template ou kind: menu para cada alvo.")
        if "text" in target:
            pattern = re.compile(str(target["text"]), re.IGNORECASE)
            return [Match(*line.box, line.confidence, line.text) for line in self.read_lines(image, target.get("roi")) if line.confidence >= threshold and pattern.search(line.text)]
        if "template" in target:
            return self._templates(image, target, threshold)
        return self._menus(image, target, threshold)

    def _read_template(self, target: dict):
        cv2, np = _dependencies()
        path = Path(target["template"])
        if not path.is_absolute():
            path = self.base_dir / path
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise VisionError(f"Template nao encontrado: {path}") from exc
        if not raw:
            raise VisionError(f"Template vazio: {path}")
        try:
            template = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        except cv2.error as exc:
            raise VisionError(f"Template invalido: {path}") from exc
        if template is None or template.size == 0:
            raise VisionError(f"Template invalido: {path}")
        if float(np.std(template)) < 1e-6:
            raise VisionError("Template uniforme nao identifica um elemento. Recorte o icone com detalhes.")
        return path, template

    @staticmethod
    def _template_targets(target: dict) -> list[dict]:
        """Native captures share the original ROI, threshold and logical label."""
        variants = target.get("template_variants")
        if not isinstance(variants, list) or not 1 <= len(variants) <= 3:
            raise VisionError("template_variants requires one to three native references")
        base = {key: value for key, value in target.items() if key != "template_variants"}
        base.setdefault("label", Path(base["template"]).stem)
        result = [base]
        for variant in variants:
            if (not isinstance(variant, dict) or not isinstance(variant.get("template"), str)
                    or not variant["template"] or set(variant) - {"template", "scales", "resampling"}):
                raise VisionError("Native references allow only template, scales and resampling")
            result.append({**base, **variant})
        return result

    def _templates(self, image: Any, target: dict, threshold: float) -> list[Match]:
        if "template_variants" in target:
            return _nms([match for variant in self._template_targets(target)
                         for match in self._templates(image, variant, threshold)])
        modes = _resampling_modes(target)
        if len(modes) > 1:
            # Merge the same physical match across preparations, but preserve
            # separate locations so strict recognition still rejects ambiguity.
            return _nms([match for mode in modes for match in self._templates(
                image, {**target, "resampling": mode}, threshold)])
        target = {**target, "resampling": modes[0]}
        cv2, np = _dependencies()
        path, template = self._read_template(target)
        x, y, width, height = _roi_box(image, target.get("roi"))
        crop = image[y:y + height, x:x + width]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        matches: list[Match] = []
        scales = target.get("scales", [0.8, 0.9, 1.0, 1.1, 1.2])
        if not scales or len(scales) > 30:
            raise VisionError("Configure de 1 a 30 escalas de template.")
        for scale in scales:
            scale = float(scale)
            if not math.isfinite(scale) or scale <= 0 or scale > 8:
                raise VisionError("Escala de template deve estar entre 0 e 8.")
            tw, th = round(template.shape[1] * scale), round(template.shape[0] * scale)
            if tw < 3 or th < 3 or tw > width or th > height:
                continue
            resampling = target.get("resampling", "default")
            if resampling == "default":
                resized = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            elif resampling in ("linear", "nearest"):
                # Preserve the calibrated scale rather than deriving a slightly
                # different ratio from rounded integer dimensions.
                resized = cv2.resize(template, None, fx=scale, fy=scale,
                                     interpolation=cv2.INTER_LINEAR if resampling == "linear" else cv2.INTER_NEAREST)
            else:
                raise VisionError("Invalid template resampling")
            if float(np.std(resized)) < 1e-6:
                continue
            scores = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            scores[~np.isfinite(scores)] = -1
            for _ in range(32):
                _, score, _, point = cv2.minMaxLoc(scores)
                if score < threshold:
                    break
                px, py = point
                matches.append(Match(x + px, y + py, tw, th, float(score), str(target.get("label", path.stem))))
                scores[max(0, py - th // 2):min(scores.shape[0], py + th // 2 + 1), max(0, px - tw // 2):min(scores.shape[1], px + tw // 2 + 1)] = -1
        return _nms(matches)

    def _menus(self, image: Any, target: dict, threshold: float) -> list[Match]:
        cv2, np = _dependencies()
        x, y, width, height = _roi_box(image, target.get("roi", [0.72, 0.60, 0.28, 0.40]))
        crop = image[y:y + height, x:x + width]
        if crop.ndim != 3:
            return []
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        brown = cv2.inRange(hsv, np.array([5, 65, 25]), np.array([35, 255, 235]))
        contours, _ = cv2.findContours(brown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        result = []
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            if min(bw, bh) < 18 or not 0.65 <= bw / bh <= 1.5 or bw > image.shape[1] * 0.18:
                continue
            if cv2.contourArea(contour) / (bw * bh) < 0.4:
                continue
            button = crop[by:by + bh, bx:bx + bw]
            light = cv2.inRange(cv2.cvtColor(button, cv2.COLOR_BGR2GRAY), 150, 255)
            horizontal = cv2.morphologyEx(light, cv2.MORPH_OPEN, np.ones((1, max(3, int(bw * 0.3))), dtype=np.uint8))
            lines, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            bars = sorted([cv2.boundingRect(line) for line in lines if 0.3 <= cv2.boundingRect(line)[2] / bw <= 0.85 and 0.025 <= cv2.boundingRect(line)[3] / bh <= 0.18], key=lambda box: box[1])
            if len(bars) != 3:
                continue
            centers = [bar[0] + bar[2] / 2 for bar in bars]
            gap1, gap2 = bars[1][1] - bars[0][1], bars[2][1] - bars[1][1]
            if max(centers) - min(centers) > bw * 0.1 or min(gap1, gap2) <= 0 or abs(gap1 - gap2) > bh * 0.08:
                continue
            confidence = 0.96 - 0.15 * abs(1 - bw / bh) - 0.3 * abs(gap1 - gap2) / bh
            if confidence >= threshold:
                result.append(Match(x + bx, y + by, bw, bh, confidence, "menu"))
        return _nms(result)
