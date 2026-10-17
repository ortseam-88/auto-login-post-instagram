# create by Ortseam
"""Ledger durable tanpa retry ambigu; satu kunci mencakup seluruh lifecycle run."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config
from models import PublishResult
from utils import read_state_text, state_file_lock

if os.name == "nt":
    import msvcrt
else:
    import fcntl

BLOCKED_STATUSES = frozenset({"submitting", "published", "unknown_after_submit", "unknown"})
MACHINE_FIELDS = frozenset({
    "job_id", "status", "digest", "kind", "mode", "cleanup", "timestamp", "updated_at",
    "version", "reused", "submitted",
})
SECRET_FIELDS = frozenset({"password", "passwd", "token", "access_token", "cookie", "cookies", "otp"})
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Ledger tidak valid: key duplikat; publikasi diblokir.")
        obj[key] = value
    return obj


@state_file_lock()
def _atomic_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

@contextmanager
def account_inventory_lock():
    """Serialize account mutations with CLI runs, including queued accounts."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # This inode/path must survive every release: unlinking permits split locks.
    with (config.OUTPUT_DIR / ".instagram-accounts.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ValueError("Inventaris akun sedang dipakai proses lain.") from None
            raise
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

@contextmanager
def account_startup_lock():
    """Keep account mutations out of the gap before a spawned run locks inventory."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Keep this path stable across releases; unlinking can create split locks.
    with (config.OUTPUT_DIR / ".instagram-startup.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ValueError("Startup run Instagram sedang dipakai proses lain.") from None
            raise
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


def read_ledger(path: Path) -> dict:
    """Baca catatan publikasi yang persisten tanpa mengambil kunci publikasi."""
    try:
        document = json.loads(read_state_text(path), object_pairs_hook=_unique_object)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Ledger tidak dapat dibaca/korup; publikasi diblokir, jangan hapus ledger.") from None
    if (not isinstance(document, dict) or set(document) != {"version", "jobs"}
            or type(document["version"]) is not int or document["version"] != 1
            or not isinstance(document["jobs"], dict)):
        raise ValueError("Struktur ledger tidak dikenal; publikasi diblokir.")
    required = {"digest", "account", "status", "detail", "permalink", "updated_at"}
    for job_id, record in document["jobs"].items():
        if (not isinstance(job_id, str) or not job_id
                or not isinstance(record, dict) or set(record) != required
                or not isinstance(record["digest"], str) or not DIGEST.fullmatch(record["digest"])
                or not isinstance(record["account"], str) or not record["account"]
                or not isinstance(record["status"], str) or record["status"] not in BLOCKED_STATUSES
                or not isinstance(record["detail"], str)
                or not isinstance(record["updated_at"], str)
                or (record["permalink"] is not None and not isinstance(record["permalink"], str))):
            raise ValueError("Entri/status ledger tidak dikenal; publikasi diblokir.")
    return document["jobs"]


class RunStore:
    """Kunci nonblocking lintas proses; ledger hanya intent/hasil pasca-Share.

    allow() tidak menulis apa pun. Kegagalan sebelum Share tetap dapat dicoba
    eksplisit pada run lain. mark_submitting() wajib selesai sebelum klik Share.
    Status tidak dikenal pada ledger gagal tertutup, tidak diperlakukan retryable.
    """

    def __init__(self, passwords: list[str]):
        self._passwords = sorted({value for value in passwords if value}, key=len, reverse=True)
        self._lock = None
        self._jobs = {}

    def _safe_job_id(self, job_id):
        if any(password in job_id for password in self._passwords):
            raise ValueError("Job ID tidak boleh memuat kredensial akun.")


    def __enter__(self):
        if self._lock is not None:
            raise RuntimeError("RunStore sudah aktif.")
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        handle = (config.OUTPUT_DIR / ".instagram-run.lock").open("a+b")
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ValueError("Run Instagram lain masih aktif; run kedua tidak dijalankan.") from None
            raise
        self._lock = handle
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            self._load()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        handle, self._lock = self._lock, None
        if handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                if exc_type is None:
                    raise
            finally:
                handle.close()
        return False

    def _active(self):
        if self._lock is None:
            raise RuntimeError("RunStore harus digunakan di dalam with.")

    def _redact(self, value):
        if isinstance(value, str):
            for password in self._passwords:
                value = value.replace(password, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "job_id" and isinstance(item, str):
                    self._safe_job_id(item)
            return {
                key: ("[REDACTED]" if key.casefold() in SECRET_FIELDS else
                      item if key in MACHINE_FIELDS else self._redact(item))
                for key, item in value.items()
            }
        return value

    def _load(self):
        self._jobs = read_ledger(config.LEDGER_FILE)
        for job_id in self._jobs:
            self._safe_job_id(job_id)

    def _identity(self, job_id, digest):
        self._active()
        if not isinstance(job_id, str) or not job_id or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("Identitas job/digest tidak valid.")
        self._safe_job_id(job_id)

    def allow(self, job_id: str, digest: str) -> None:
        self._identity(job_id, digest)
        previous = self._jobs.get(job_id)
        if previous is None:
            return
        if previous["digest"] != digest:
            raise ValueError("Job ID sudah tercatat dengan payload berbeda; publikasi diblokir.")
        raise ValueError(f"Job berstatus {previous['status']}; jangan publikasi ulang tanpa rekonsiliasi manual.")

    def _save(self):
        _atomic_json(config.LEDGER_FILE, {"version": 1, "jobs": self._jobs})

    def mark_submitting(self, job_id: str, digest: str, account: str) -> None:
        self.allow(job_id, digest)
        if not isinstance(account, str) or not account:
            raise ValueError("Account job tidak valid.")
        self._jobs[job_id] = {
            "digest": digest, "account": account, "status": "submitting",
            "detail": "Intent Share tersimpan; hasil belum terkonfirmasi.", "permalink": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Jangan rollback state memori jika I/O gagal: os.replace mungkin sudah
        # berhasil sebelum kegagalan fsync. Run ini tetap fail closed.
        self._save()

    def finish(self, job_id: str, digest: str, account: str, result: PublishResult) -> None:
        self._identity(job_id, digest)
        previous = self._jobs.get(job_id)
        if previous is None or previous["digest"] != digest or previous["account"] != account:
            raise ValueError("Hasil tidak cocok dengan intent Share yang tersimpan.")
        if result.status not in {"published", "unknown_after_submit"}:
            raise ValueError("Setelah intent Share hanya published/unknown_after_submit boleh dicatat.")
        if previous["status"] == "published":
            if result.status == "published":
                # Pemanggilan finish eksplisit juga menyimpan bukti memori jika
                # penyimpanan sebelumnya gagal; jangan ganti bukti aslinya.
                self._save()
                return
            raise ValueError("Bukti published tidak boleh diturunkan menjadi status lain.")
        if previous["status"] not in {"submitting", "unknown_after_submit"}:
            raise ValueError("Status ledger tidak dapat diselesaikan otomatis.")
        if not isinstance(result.detail, str) or (result.permalink is not None and not isinstance(result.permalink, str)):
            raise ValueError("Detail/permalink hasil tidak valid.")
        self._jobs[job_id] = {
            **previous, "status": result.status, "detail": self._redact(result.detail),
            "permalink": self._redact(result.permalink),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def write_summary(self, rows: list[dict]) -> None:
        self._active()
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Ringkasan harus array objek hasil.")
        _atomic_json(config.RESULT_FILE, self._redact(rows))
