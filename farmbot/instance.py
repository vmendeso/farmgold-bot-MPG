"""Nonblocking OS lock shared by every command that controls the game.

The lock file is deliberately kept on disk after release. Deleting or replacing
it could let two processes lock different file objects using the same pathname.
An abandoned PID is only informational: the OS lock determines availability and
is automatically released when its process exits, including an abrupt crash.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


class InstanceAlreadyRunning(RuntimeError):
    """Another process currently owns the bot's OS lock."""


class InstanceLock:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._handle: BinaryIO | None = None

    @staticmethod
    def _acquire(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            # A byte-range lock can extend beyond EOF. Lock before initializing
            # the file so concurrent starters never write into another lock.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> InstanceLock:
        if self._handle is not None:
            raise RuntimeError("Este bloqueio de instancia ja esta adquirido.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Do not truncate before locking: an existing file may belong to a
        # running process. O_CREAT without O_TRUNC also preserves its inode.
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b")
        try:
            try:
                self._acquire(handle)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise InstanceAlreadyRunning(
                        f"Outro bot já está ativo. Aguarde sua parada antes de iniciar outro ({self.path})."
                    ) from exc
                raise
            # This PID is diagnostic only; never kill a process or infer that
            # an old PID means the lock is still held.
            handle.seek(0)
            handle.write(f"pid={os.getpid()}\n".encode("ascii"))
            handle.truncate()
            handle.flush()
        except BaseException:
            handle.close()  # Closing also releases any acquired OS lock.
            raise
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        # Safe after a failed __enter__, repeated cleanup, or ordinary exit.
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            self._release(handle)
        finally:
            handle.close()
