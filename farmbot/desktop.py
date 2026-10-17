"""Windows client-area capture and conservative, frame-bound GUI input.

All geometry is in physical desktop pixels. The selected HWND stays fixed for
the session: a closed or replaced game window requires restarting the command.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import re
import time
from typing import Any

from .safety import StopController

log = logging.getLogger(__name__)


class WindowError(RuntimeError):
    pass


class InputSafetyError(WindowError):
    pass


class HoverUnavailable(InputSafetyError):
    """The unchanged, foreground game did not acquire a hovered point in time.

    A caller may retry from a fresh capture and newly recognized target. Focus,
    frame age, geometry and emergency-stop failures retain their own exceptions
    and must not be treated as this transient condition.
    """


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True)
class Frame:
    image: Any
    rect: Rect
    hwnd: int
    captured_at: float


class _Win32:
    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowError("Captura e cliques reais requerem Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        u = self.user32
        signatures = {
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetForegroundWindow": ([], wintypes.HWND),
            "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
            "GetWindowTextW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ClientToScreen": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            func = getattr(u, name)
            func.argtypes, func.restype = args, result
        self._dpi_awareness()

    def _dpi_awareness(self) -> None:
        """Set awareness before MSS/PyAutoGUI import, including embedded hosts."""
        u = self.user32
        try:
            u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
            context = u.GetThreadDpiAwarenessContext()
            if u.GetAwarenessFromDpiAwarenessContext(context) != 2:
                raise WindowError("Nao foi possivel usar pixels fisicos com DPI por monitor.")
        except AttributeError as exc:
            raise WindowError("A automacao requer Windows 10/11 com DPI por monitor.") from exc

    def title(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def find(self, pattern: str, exact: str | None) -> list[int]:
        expression = re.compile(pattern, re.IGNORECASE)
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd, lparam):
            if self.user32.IsWindowVisible(hwnd):
                title = self.title(hwnd)
                if (title == exact if exact is not None else bool(expression.search(title))):
                    found.append(int(hwnd))
            return True

        self.user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        if not self.user32.EnumWindows(callback, 0):
            raise WindowError("Falha ao listar janelas do Windows.")
        return found

    def process_id(self, hwnd: int) -> int:
        result = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(result))
        return result.value

    def rect(self, hwnd: int) -> Rect:
        u = self.user32
        if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            raise WindowError("A janela do jogo foi fechada, ocultada ou minimizada.")
        bounds = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not u.GetClientRect(hwnd, ctypes.byref(bounds)) or not u.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise WindowError("Nao foi possivel obter a area cliente da janela.")
        width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
        if width <= 0 or height <= 0:
            raise WindowError("A area cliente do jogo esta vazia.")
        return Rect(origin.x, origin.y, width, height)

    def foreground(self, hwnd: int) -> bool:
        return self.user32.GetAncestor(self.user32.GetForegroundWindow(), 2) == hwnd

    def owns_point(self, hwnd: int, x: int, y: int) -> bool:
        hit = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        return bool(hit) and self.user32.GetAncestor(hit, 2) == hwnd


class WindowManager:
    def __init__(self, config: dict):
        self.config = config
        self._api = _Win32()
        self._capture = None
        self.last_frame: Frame | None = None
        exact = config.get("exact_title", config.get("title"))
        pattern = config.get("title_regex", r"My Party Is Grinding")
        if not exact and not str(pattern).strip():
            raise WindowError("Configure um titulo especifico para a janela do jogo.")
        matches = self._api.find(pattern, exact)
        if len(matches) != 1:
            raise WindowError(f"Esperada uma janela do jogo; encontradas {len(matches)}. Ajuste window.title_regex/exact_title.")
        self.hwnd = matches[0]
        self._pid = self._api.process_id(self.hwnd)
        self.current()

    def current(self) -> tuple[int, Rect]:
        rect = self._api.rect(self.hwnd)
        if self._api.process_id(self.hwnd) != self._pid:
            raise WindowError("A identidade da janela mudou; reinicie a captura.")
        return self.hwnd, rect

    def assert_foreground(self) -> None:
        if not self._api.foreground(self.hwnd):
            raise InputSafetyError("O jogo deve estar em primeiro plano. Ative a janela manualmente.")

    def request_focus(self) -> None:
        """Explicit startup-only focus request; input guards remain mandatory."""
        self.current()
        self._api.user32.SetForegroundWindow(self.hwnd)

    def capture(self) -> Frame:
        hwnd, rect = self.current()
        self.assert_foreground()
        try:
            import numpy as np
            if self._capture is None:
                from mss import mss
                self._capture = mss()
        except ImportError as exc:
            raise WindowError("Instale as dependencias de requirements.txt para capturar a tela.") from exc
        captured_at = time.monotonic()
        shot = self._capture.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        pixels = np.asarray(shot)[:, :, :3].copy()  # MSS BGRA -> OpenCV BGR.
        if self.current() != (hwnd, rect):
            raise InputSafetyError("A janela mudou durante a captura. Capture novamente.")
        self.assert_foreground()
        self.last_frame = Frame(pixels, rect, hwnd, captured_at)
        return self.last_frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.close()
            self._capture = None

    def __enter__(self) -> WindowManager:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class SafeInput:
    def __init__(self, window: WindowManager, stop: StopController, dry_run: bool = True):
        self.window, self.stop, self.dry_run = window, stop, dry_run
        self.max_frame_age = float(window.config.get("max_frame_age_seconds", window.config.get("capture_max_age", 2.0)))
        if not math.isfinite(self.max_frame_age) or self.max_frame_age <= 0:
            raise ValueError("max_frame_age_seconds deve ser positivo e finito.")
        hover_timeout = window.config.get("hover_ownership_timeout_seconds", 1.0)
        if (isinstance(hover_timeout, bool) or not isinstance(hover_timeout, (int, float))
                or not math.isfinite(hover_timeout) or not 0 < hover_timeout <= 2):
            raise ValueError("hover_ownership_timeout_seconds deve ser positivo e no maximo 2 s.")
        self.hover_ownership_timeout = float(hover_timeout)

    def _guard(self, frame: Frame, match: Any, require_owned: bool = True) -> tuple[int, int]:
        self.stop.check()
        age = time.monotonic() - frame.captured_at
        if not math.isfinite(age) or age < 0 or age > self.max_frame_age:
            raise InputSafetyError("Captura antiga; reconheca novamente o alvo antes de clicar.")
        hwnd, rect = self.window.current()
        if hwnd != frame.hwnd or rect != frame.rect:
            raise InputSafetyError("Janela movida, redimensionada ou substituida depois do reconhecimento.")
        self.window.assert_foreground()
        values = (match.x, match.y, match.width, match.height)
        if not all(math.isfinite(value) for value in values):
            raise InputSafetyError("Coordenadas invalidas para clique.")
        if match.width <= 0 or match.height <= 0 or match.x < 0 or match.y < 0 or match.x + match.width > rect.width or match.y + match.height > rect.height:
            raise InputSafetyError("O alvo ultrapassa a area cliente do jogo.")
        cx, cy = match.center
        x, y = int(rect.left + cx), int(rect.top + cy)
        if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            raise InputSafetyError("Clique fora da area cliente do jogo.")
        if require_owned and not self.window._api.owns_point(hwnd, x, y):
            raise InputSafetyError("Outra janela cobre o ponto de clique.")
        return x, y

    def hover(self, frame: Frame, match: Any, *, deadline: float | None = None) -> bool:
        """Reveal a tooltip without clicking or weakening click ownership checks.

        Some transparent game overlays enable hit testing only after the pointer
        enters their visible content. Moving is allowed after all other guards;
        returning success still requires the game to own the hovered point.
        """
        x, y = self._guard(frame, match, require_owned=False)
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("O prazo de hover deve ser finito.")
        if self.dry_run:
            log.info("DRY RUN: hover em %s (%d, %d); cursor nao movido.", match.label, x, y)
            return False
        try:
            import pyautogui
        except ImportError as exc:
            raise InputSafetyError("Instale PyAutoGUI antes de habilitar hover.") from exc
        pyautogui.FAILSAFE = True
        self._guard(frame, match, require_owned=False)
        if deadline is not None and time.monotonic() >= deadline:
            raise HoverUnavailable("O prazo de hover terminou antes do movimento.")
        pyautogui.moveTo(x, y, duration=0)
        acquisition_deadline = time.monotonic() + self.hover_ownership_timeout
        if deadline is not None:
            acquisition_deadline = min(acquisition_deadline, deadline)
        while True:
            self._guard(frame, match, require_owned=False)
            if self.window._api.owns_point(frame.hwnd, x, y):
                self._guard(frame, match)
                log.debug("Hover confirmado no alvo %s em (%d, %d).", match.label, x, y)
                return True
            remaining = acquisition_deadline - time.monotonic()
            if remaining <= 0:
                raise HoverUnavailable(
                    f"O jogo nao assumiu o ponto de hover no prazo disponivel (ate {self.hover_ownership_timeout:.2f} s)."
                )
            self.stop.wait(min(0.05, remaining))

    def click(self, frame: Frame, match: Any) -> bool:
        x, y = self._guard(frame, match)
        if self.dry_run:
            log.info("DRY RUN: alvo %s em (%d, %d); clique nao executado.", match.label, x, y)
            return False
        try:
            import pyautogui
        except ImportError as exc:
            raise InputSafetyError("Instale PyAutoGUI antes de habilitar cliques.") from exc
        pyautogui.FAILSAFE = True
        self._guard(frame, match)
        pyautogui.moveTo(x, y, duration=0)
        # Movement/import may consume time or permit a foreground/window change.
        self._guard(frame, match)
        pyautogui.click(x=x, y=y, button="left")
        self.stop.check()
        log.info("Clique confirmado no alvo %s em (%d, %d).", match.label, x, y)
        return True


def save_frame(frame: Frame, path: str | Path) -> Path:
    """Save BGR capture as PNG, including paths with Unicode on Windows."""
    import cv2
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame.image)
    if not ok:
        raise WindowError("Falha ao codificar screenshot PNG.")
    output.write_bytes(encoded.tobytes())
    return output
