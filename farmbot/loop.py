"""Serial, interruptible execution on a fixed monotonic schedule."""
from __future__ import annotations

from collections.abc import Callable
import logging
import math
import time
from typing import NoReturn, Protocol


log = logging.getLogger(__name__)


class CycleDeferred(Exception):
    """The caller explicitly deferred this cycle until its next scheduled slot."""


class _StopControl(Protocol):
    def check(self) -> None: ...

    def wait(self, seconds: float) -> None: ...


def run_loop(callback: Callable[[], object], stop: _StopControl,
             interval_seconds: float = 300, *,
             monotonic: Callable[[], float] = time.monotonic) -> NoReturn:
    """Run immediately, then at fixed intervals measured from the first start.

    A callback always finishes before another can start. If it runs beyond one
    or more scheduled starts, those expired slots are skipped without catch-up.
    Finishing exactly on a scheduled start leaves that slot available. Waiting
    is delegated to the same stop controller used by the callback, so ESC can
    interrupt between cycles too. Only an explicit ``CycleDeferred`` defers a
    cycle and continues on the normal schedule. Every other callback error and
    stop request propagates; there is no automatic retry of an unclassified error.
    """
    try:
        interval = float(interval_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_seconds deve ser positivo e finito") from exc
    if isinstance(interval_seconds, bool) or not math.isfinite(interval) or interval <= 0:
        raise ValueError("interval_seconds deve ser positivo e finito")

    next_start: float | None = None
    cycle = 0
    while True:
        stop.check()
        if next_start is not None:
            remaining = next_start - monotonic()
            if remaining > 0:
                log.info("Próximo ciclo em %.1f segundos", remaining)
            while remaining > 0:
                stop.wait(remaining)
                stop.check()
                remaining = next_start - monotonic()
        # Check again immediately before callback, including after a wait that
        # returned without raising but left an emergency stop pending.
        stop.check()
        started = monotonic()
        if next_start is None:
            next_start = started
        cycle += 1
        log.info("Ciclo %d iniciado", cycle)
        try:
            callback()
        except CycleDeferred as exc:
            finished = monotonic()
            log.warning("Ciclo %d adiado após %.1f segundos: %s", cycle, finished - started, exc)
        except BaseException:
            log.info("Ciclo %d interrompido após %.1f segundos", cycle, monotonic() - started)
            raise
        else:
            finished = monotonic()
            log.info("Ciclo %d concluído em %.1f segundos", cycle, finished - started)

        next_start += interval
        if next_start < finished:
            skipped = math.ceil((finished - next_start) / interval)
            next_start += skipped * interval
            log.info("%d horário(s) vencido(s) ignorado(s); sem execução acumulada", skipped)
