# create by Ortseam
"""Akses file state singkat dan log yang tidak membocorkan password."""

from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import threading

import config

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_state_thread_lock = threading.Lock()


@contextmanager
def state_file_lock():
    """Cegah pembaca Windows menghalangi replace; tidak mencakup browser/network.

    Urutan: kunci lifecycle/inventaris bila diperlukan, baru kunci state ini.
    File kunci tetap ada; hanya handle yang dilepas setelah I/O selesai.
    """
    with _state_thread_lock:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with (config.OUTPUT_DIR / ".instagram-state.lock").open("a+b") as handle:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_state_text(path: Path, *, encoding: str = "utf-8") -> str:
    with state_file_lock():
        return path.read_text(encoding=encoding)


def read_state_bytes(path: Path) -> bytes:
    with state_file_lock():
        return path.read_bytes()


class _RedactPasswords(logging.Filter):
    def __init__(self, passwords: list[str]):
        super().__init__()
        self.passwords = sorted(set(passwords), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for password in self.passwords:
            if password:
                message = message.replace(password, "[REDACTED]")
        record.msg = message
        record.args = ()
        # Traceback Playwright bisa mengandung nilai fill(); jangan simpan.
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_logging(passwords: list[str]) -> logging.Logger:
    logger = logging.getLogger("instagram")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler(config.LOG_FILE, encoding="utf-8")):
        handler.setFormatter(formatter)
        handler.addFilter(_RedactPasswords(passwords))
        logger.addHandler(handler)
    return logger
