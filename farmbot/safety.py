"""Cooperative, interruptible waits and a global Windows ESC emergency stop."""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import threading
import time
from collections.abc import Callable


class StopRequested(RuntimeError):
    """The operator requested an immediate stop."""


class StopController:
    def __init__(self, key_check: Callable[[], bool] | None = None, poll_interval: float = 0.025,
                 stop_file: str | Path | None = None):
        self._stop = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._reason = "Parada solicitada."
        self._stop_file = Path(stop_file) if stop_file is not None else None
        self._poll_interval = max(0.01, min(float(poll_interval), 0.1))
        if key_check is not None:
            self._key_check = key_check
        elif os.name == "nt":
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            key_state = user32.GetAsyncKeyState
            key_state.argtypes = [ctypes.c_int]
            key_state.restype = ctypes.c_short
            self._key_check = lambda: bool(key_state(0x1B) & 0x8000)
        else:
            self._key_check = lambda: False

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self, reason: str = "Parada solicitada.") -> None:
        self._reason = reason
        self._stop.set()

    def _poll(self) -> None:
        while not self._shutdown.is_set():
            if self._file_stop():
                return
            if self._key_check():
                self.request_stop("Parada de emergencia: ESC.")
                return
            self._shutdown.wait(self._poll_interval)

    def _file_stop(self) -> bool:
        if self._stop_file is not None and self._stop_file.exists():
            self.request_stop("Parada solicitada pelo controle do bot.")
            # The sticky event survives consuming the signal. Never clear a
            # signal at worker startup: an immediate GUI stop must be honored.
            try:
                self._stop_file.unlink(missing_ok=True)
            except OSError:
                pass
            return True
        return False

    def check(self) -> None:
        self._file_stop()
        if self._key_check():
            self.request_stop("Parada de emergencia: ESC.")
        if self._stop.is_set():
            raise StopRequested(self._reason)

    def wait(self, seconds: float) -> None:
        """Unlike time.sleep, also checks ESC when used without a context manager."""
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Tempo de espera deve ser finito e nao negativo.")
        deadline = time.monotonic() + seconds
        while True:
            self.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(remaining, self._poll_interval))

    def __enter__(self) -> StopController:
        if self._thread is None:
            self._shutdown.clear()
            self._thread = threading.Thread(target=self._poll, name="farmbot-esc", daemon=True)
            self._thread.start()
        self.check()
        return self

    def close(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
