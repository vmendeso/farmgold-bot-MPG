"""One isolated live-search cycle for the serial scheduler and desktop UI."""
from __future__ import annotations

import logging
import time

from .config import output_path
from .desktop import Frame, SafeInput, WindowError, WindowManager, save_frame
from .loop import CycleDeferred
from .map_navigation import MapNavigator
from .navigation import CalibrationError, DryRunComplete, NavigationError, Navigator
from .runner import recent_stats, run_search
from .storage import export_ranking
from .vision import VisionError

log = logging.getLogger(__name__)


class SearchCycle:
    """Reacquire the game each cycle; never reuse an old HWND or input session.

    An explicit focus request is consumed on the first invocation, including a
    failed one. Later rounds wait for the user to bring the game to foreground.
    """

    def __init__(self, config, vision, store, stop, *, dry_run, focus=False):
        self.config = config
        self.vision = vision
        self.store = store
        self.stop = stop
        self.dry_run = bool(dry_run)
        self._focus_pending = bool(focus)

    @staticmethod
    def _recoverable(exc: BaseException) -> bool:
        return isinstance(exc, (WindowError, VisionError, NavigationError)) and not isinstance(
            exc, (CalibrationError, DryRunComplete)
        )

    def _save_previous_frame(self, window) -> None:
        """Saving diagnostics must not capture, hover or request focus."""
        if window is None:
            return
        frame = getattr(window, "last_frame", None)
        if not isinstance(frame, Frame):
            return
        shape = getattr(frame.image, "shape", None)
        if not isinstance(shape, tuple) or len(shape) < 2 or min(shape[:2]) <= 0:
            return
        try:
            path = output_path(self.config, "artifacts") / f"cycle-last-frame-{time.time_ns()}.png"
            save_frame(frame, path)
            log.warning("Diagnóstico da última captura válida da rodada: %s", path)
        except Exception as exc:
            log.warning("Não foi possível salvar a última captura da rodada: %s", exc)

    def _finish(self, window, failure: BaseException | None) -> None:
        """Export accumulated records and release capture resources on all exits."""
        cleanup_error = None
        try:
            export_ranking(recent_stats(self.store, self.config), output_path(self.config, "ranking"))
        except Exception as exc:
            cleanup_error = exc
        finally:
            if window is not None:
                try:
                    window.close()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
                    else:
                        log.error("Falha adicional ao fechar a captura: %s", exc)
        if cleanup_error is not None:
            if failure is not None and not self._recoverable(failure):
                # In particular, never replace ESC/Ctrl+C/fail-safe or a fatal
                # calibration/programming error with an export/close failure.
                log.error("Falha ao finalizar a rodada já interrompida: %s", cleanup_error)
            else:
                # Disk/database/capture-cleanup failures are not classified as
                # transient navigation failures and must stop the scheduler.
                raise cleanup_error

    def __call__(self):
        window = None
        failure = None
        request_focus = self._focus_pending
        self._focus_pending = False
        try:
            self.stop.check()
            window = WindowManager(self.config["window"])
            inputs = SafeInput(window, self.stop, dry_run=self.dry_run)
            navigation_class = MapNavigator if self.config["navigation"].get("mode") == "icon_map" else Navigator
            navigator = navigation_class(window, self.vision, inputs, self.stop, self.config["navigation"])
            navigator.preflight()
            self.stop.check()
            if request_focus:
                window.request_focus()
                self.stop.wait(0.25)
            # Reject a background game before any capture, hover, menu action
            # or attempt to restore a stage. SafeInput repeats the same guard.
            window.assert_foreground()
            self.stop.check()
            return run_search(window, self.vision, navigator, inputs, self.stop, self.config, self.store)
        except DryRunComplete as exc:
            print(str(exc))
            return []
        except BaseException as exc:
            failure = exc
            self._save_previous_frame(window)
            if self._recoverable(exc):
                raise CycleDeferred(f"{type(exc).__name__}: {exc}") from exc
            raise
        finally:
            self._finish(window, failure)
