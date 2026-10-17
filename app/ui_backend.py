# create by Ortseam
"""Backend UI lokal: penyimpanan pengaturan, validasi, dan supervisi run engine."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

import config
import catalog
from accounts import Account, load_accounts
from browser_session import ProfileBusyError, _profile_lock, profile_directory
from jobs import _unique_object
from login_flow import IDENTITY_FILE
from results import account_inventory_lock, account_startup_lock, read_ledger
from utils import read_state_bytes, read_state_text, state_file_lock

MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".avif": "image/avif",
    ".heic": "image/heic", ".heif": "image/heif", ".mp4": "video/mp4", ".mov": "video/quicktime",
}
MEDIA_EXTENSIONS = frozenset(MEDIA_TYPES)
HISTORY_LIMIT = 100
MEDIA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,119}\Z")
DEFAULT_SETTINGS = {"mode": "login", "workers": config.DEFAULT_WORKERS, "manual_timeout": config.MANUAL_TIMEOUT_SECS, "headless": False}
EMPTY_MANIFEST = {"version": 2, "contents": []}
FINAL_STATUSES = {"login": "success", "publish": "published"}


@contextmanager
def _staged_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        yield Path(temporary)
    finally:
        Path(temporary).unlink(missing_ok=True)


@state_file_lock()
def _atomic_write(path: Path, content: str) -> None:
    with _staged_write(path, content) as temporary:
        os.replace(temporary, path)


def _account_error(exc: ValueError) -> str:
    return str(exc)


def list_accounts() -> tuple[list[Account], str | None]:
    if not config.INPUT_ACCOUNTS_FILE.is_file():
        return [], None
    try:
        return load_accounts(config.INPUT_ACCOUNTS_FILE), None
    except (OSError, UnicodeError, ValueError) as exc:
        return [], _account_error(exc) if isinstance(exc, ValueError) else "File akun tidak dapat dibaca."


def account_rows() -> tuple[list[dict], str | None]:
    """Identitas akun plus keadaan session lokal; tidak pernah memuat password."""
    accounts, error = list_accounts()
    rows = []
    for account in accounts:
        profile = profile_directory(account.identifier)
        rows.append({
            "identifier": account.identifier,
            "profile_dir": str(profile),
            "profile_exists": profile.is_dir(),
            "identity_bound": (profile / IDENTITY_FILE).is_file(),
        })
    return rows, error


def _split_account_records(text: str) -> list[str]:
    """Pisahkan hanya CR/LF seperti pembaca akun, bukan separator Unicode pada password."""
    records = re.split(r"\r\n?|\n", text)
    return records[:-1] if records[-1] == "" else records


def _account_lines() -> list[str]:
    if not config.INPUT_ACCOUNTS_FILE.is_file():
        return []
    return _split_account_records(read_state_text(config.INPUT_ACCOUNTS_FILE, encoding="utf-8-sig"))


def _write_accounts(lines: list[str]) -> None:
    _atomic_write(config.INPUT_ACCOUNTS_FILE, "".join(line + "\n" for line in lines))


def _validate_identifier(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise ValueError("Identifier harus berupa teks.")
    identifier = identifier.strip()
    if not identifier or any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "|" for char in identifier):
        raise ValueError("Identifier tidak boleh kosong, berisi spasi, kontrol, atau tanda |.")
    return identifier


def add_account(identifier: str, password: str) -> None:
    identifier = _validate_identifier(identifier)
    if not isinstance(password, str):
        raise ValueError("Password harus berupa teks.")
    if not password or password.isspace() or "\n" in password or "\r" in password:
        raise ValueError("Password tidak boleh kosong atau berisi baris baru.")
    with account_startup_lock(), account_inventory_lock():
        accounts, error = list_accounts()
        if error:
            raise ValueError(f"Perbaiki file akun dahulu: {error}")
        if any(account.identifier.casefold() == identifier.casefold() for account in accounts):
            raise ValueError("Akun dengan identifier itu sudah ada.")
        lines = _account_lines()
        if not lines:
            lines = ["# identifier|password (satu akun per baris)"]
        _write_accounts([*lines, f"{identifier}|{password}"])


def add_accounts_bulk(text: str) -> dict:
    """Impor banyak baris identifier|password sekaligus; satu baris rusak menggagalkan semua."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Tempel minimal satu baris identifier|password.")
    with account_startup_lock(), account_inventory_lock():
        accounts, error = list_accounts()
        if error:
            raise ValueError(f"Perbaiki file akun dahulu: {error}")
        existing = {account.identifier.casefold() for account in accounts}
        added, skipped, batch = [], [], set()
        for number, raw in enumerate(_split_account_records(text), start=1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            identifier, separator, password = raw.partition("|")
            if not separator:
                raise ValueError(f"Baris {number}: tidak ada pemisah | antara identifier dan password.")
            identifier = _validate_identifier(identifier)
            if not password or password.isspace():
                raise ValueError(f"Baris {number}: password kosong.")
            key = identifier.casefold()
            if key in batch:
                raise ValueError(f"Baris {number}: identifier {identifier} muncul dua kali dalam tempelan.")
            batch.add(key)
            if key in existing:
                skipped.append(identifier)
                continue
            added.append(f"{identifier}|{password}")
        if added:
            lines = _account_lines() or ["# identifier|password (satu akun per baris)"]
            _write_accounts([*lines, *added])
        return {"added": [line.partition("|")[0] for line in added], "skipped": skipped}


def remove_account(identifier: str) -> dict:
    """Hapus akun dan profilnya; riwayat identitas posting tetap disimpan."""
    identifier = _validate_identifier(identifier)
    with account_startup_lock(), account_inventory_lock():
        accounts, error = list_accounts()
        if error:
            raise ValueError(f"Perbaiki file akun dahulu: {error}")
        if not any(account.identifier.casefold() == identifier.casefold() for account in accounts):
            raise ValueError("Akun tidak ditemukan.")
        kept = []
        for line in _account_lines():
            head = line.partition("|")[0].strip()
            if line.strip() and not line.lstrip().startswith("#") and head.casefold() == identifier.casefold():
                continue
            kept.append(line)
        profile = profile_directory(identifier)
        profile.parent.mkdir(parents=True, exist_ok=True)
        # Siapkan isi baru dahulu. Akun tetap terdaftar sampai profil berhasil
        # dihapus, sehingga kegagalan/berhenti di tengah tetap bisa dicoba ulang.
        try:
            with _profile_lock(profile.with_suffix(".lock")):
                with _staged_write(config.INPUT_ACCOUNTS_FILE, "".join(line + "\n" for line in kept)) as temporary:
                    removed = profile.exists()
                    if removed:
                        if profile.is_dir():
                            shutil.rmtree(profile)
                        else:
                            profile.unlink()
                    with state_file_lock():
                        os.replace(temporary, config.INPUT_ACCOUNTS_FILE)
                return {"profile_removed": removed}
        except ProfileBusyError:
            raise ValueError("Profil akun sedang dipakai oleh run yang berjalan; hentikan run dahulu.") from None
        except OSError:
            raise ValueError("Penghapusan belum selesai; tutup Chrome akun itu lalu coba hapus lagi. Profil yang terhapus sebagian mungkin perlu login ulang.") from None


def remove_profile(identifier: str) -> bool:
    """Hapus data Chrome tanpa memutus file kunci stabil yang dipakai antarproses."""
    profile = profile_directory(identifier)
    lock_path = profile.with_suffix(".lock")
    if not profile.exists():
        return False
    try:
        with _profile_lock(lock_path):
            if profile.is_dir():
                shutil.rmtree(profile)
            else:
                profile.unlink()
    except ProfileBusyError:
        raise ValueError("Profil akun sedang dipakai oleh run yang berjalan; hentikan run dahulu.") from None
    except OSError:
        raise ValueError("Profil browser tidak dapat dihapus sepenuhnya; tutup Chrome akun itu lalu coba lagi.") from None
    return True


def read_manifest() -> dict:
    document = catalog.load_catalog(config.UI_JOBS_FILE)
    return {"version": 2, "contents": document["contents"]}


def validate_manifest(document: dict) -> list[str]:
    """Validasi konten memakai aturan engine tanpa memerlukan akun."""
    return catalog.validate_manifest(document, config.UI_JOBS_FILE.parent)


def _commit_catalog(document: dict) -> None:
    """Pemanggil memegang kunci startup lalu inventaris; simpan byte lama sebelum migrasi."""
    path = config.UI_JOBS_FILE
    try:
        original = read_state_bytes(path)
    except FileNotFoundError:
        original = None
    if original is not None:
        previous = json.loads(original.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        if previous.get("version") == 1:
            backup = path.with_name(f"{path.stem}.v1-backup{path.suffix}")
            try:
                descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if backup.read_bytes() != original:
                    raise ValueError("Cadangan katalog lama sudah ada dengan isi berbeda; migrasi diblokir.") from None
            else:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
    _atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def save_manifest(document: dict) -> list[str]:
    with account_startup_lock(), account_inventory_lock():
        current = catalog.load_catalog(config.UI_JOBS_FILE)
        document = deepcopy(document)
        content_ids = validate_manifest(document)
        current["contents"] = document["contents"]
        _commit_catalog(current)
        return content_ids


def read_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if config.UI_SETTINGS_FILE.is_file():
        try:
            stored = json.loads(read_state_text(config.UI_SETTINGS_FILE))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("File pengaturan tidak dapat dibaca; perbaiki lewat Pengaturan lanjutan.") from None
        if isinstance(stored, dict):
            settings.update({key: stored[key] for key in DEFAULT_SETTINGS if key in stored})
        else:
            raise ValueError("File pengaturan harus berupa objek JSON.")
    # Migrasi file lama saja; request API dengan mode pensiun tetap ditolak.
    if settings["mode"] == "prepare":
        settings["mode"] = "login"
        return save_settings(settings)
    return normalize_settings(settings)


def normalize_settings(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Pengaturan harus objek JSON.")
    mode = raw.get("mode", "login")
    if not isinstance(mode, str) or mode not in FINAL_STATUSES:
        raise ValueError("Mode harus login atau publish.")
    workers = raw.get("workers", config.DEFAULT_WORKERS)
    if type(workers) is not int or not 1 <= workers <= 10:
        raise ValueError("Worker harus bilangan bulat 1 sampai 10.")
    timeout = raw.get("manual_timeout", config.MANUAL_TIMEOUT_SECS)
    if type(timeout) not in (int, float) or timeout != timeout or timeout < 0 or timeout > 86_400:
        raise ValueError("Batas verifikasi manual harus 0 sampai 86400 detik.")
    headless = raw.get("headless", False)
    if type(headless) is not bool:
        raise ValueError("Headless harus true/false.")
    return {"mode": mode, "workers": workers, "manual_timeout": float(timeout), "headless": headless}


def save_settings(raw: dict) -> dict:
    settings = normalize_settings(raw)
    _atomic_write(config.UI_SETTINGS_FILE, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    return settings


def list_media() -> list[dict]:
    if not config.UI_MEDIA_DIR.is_dir():
        return []
    rows = []
    for path in sorted(config.UI_MEDIA_DIR.iterdir()):
        suffix = path.suffix.lower()
        if path.is_file() and MEDIA_NAME.fullmatch(path.name) and suffix in MEDIA_TYPES and path.resolve().parent == config.UI_MEDIA_DIR.resolve():
            stat = path.stat()
            rows.append({"name": path.name, "path": f"media/{path.name}", "bytes": stat.st_size,
                         "kind": "video" if suffix in {".mp4", ".mov"} else "image",
                         "content_type": MEDIA_TYPES[suffix],
                         "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
    return rows


def media_path(name: str) -> Path:
    """Hanya file langsung di input/media dengan nama yang lolos MEDIA_NAME."""
    if not isinstance(name, str) or not MEDIA_NAME.fullmatch(name) or Path(name).suffix.lower() not in MEDIA_TYPES:
        raise ValueError("Nama media tidak valid.")
    path = config.UI_MEDIA_DIR / name
    if not path.is_file() or path.resolve().parent != config.UI_MEDIA_DIR.resolve():
        raise ValueError("Media tidak ditemukan.")
    return path


def remove_media(name: str) -> dict:
    """Pindahkan ke arsip hanya bila tidak dirujuk konten tersimpan, termasuk alias path."""
    with account_startup_lock(), account_inventory_lock():
        path = media_path(name)
        target_path = path.resolve()
        users = []
        for job in read_manifest()["contents"]:
            if not isinstance(job, dict):
                continue
            media = job.get("media") if isinstance(job.get("media"), list) else []
            references = [job.get("cover_path"), *(item.get("path") for item in media if isinstance(item, dict))]
            if any(isinstance(value, str) and
                   ((Path(value) if Path(value).is_absolute() else config.UI_JOBS_FILE.parent / value).resolve() == target_path)
                   for value in references):
                users.append(job["name"])
        if users:
            raise ValueError(f"Media masih dipakai konten tersimpan: {', '.join(users)}. Lepas dari konten dan simpan dahulu.")
        config.UI_MEDIA_TRASH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = config.UI_MEDIA_TRASH_DIR / f"{stamp}-{path.name}"
        counter = 1
        while target.exists():
            counter += 1
            target = config.UI_MEDIA_TRASH_DIR / f"{stamp}-{counter}-{path.name}"
        shutil.move(str(path), str(target))
        return {"name": path.name, "moved_to": str(target)}


def _import_media_unlocked(path: Path) -> dict:
    """Salin ke staging lokal; pemanggil memegang kunci startup lalu inventaris."""
    if not path.is_file():
        raise ValueError("File media tidak ditemukan.")
    if path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("Ekstensi media tidak didukung.")
    if not MEDIA_NAME.fullmatch(path.name):
        raise ValueError("Nama file media mengandung karakter yang tidak didukung.")
    config.UI_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target = config.UI_MEDIA_DIR / path.name
    if target.exists():
        if target.samefile(path):
            return {"name": target.name, "path": f"media/{target.name}"}
        raise ValueError("Nama file sudah ada di input/media; ganti nama file sumber.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=config.UI_MEDIA_DIR)
    os.close(descriptor)
    try:
        shutil.copy2(path, temporary)
        # Staging satu direktori: nama final baru tampak sesudah seluruh bytes tersedia.
        # Windows rename menolak tujuan yang sudah ada; POSIX link juga eksklusif.
        if os.name == "nt":
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"name": target.name, "path": f"media/{target.name}"}


def _resolve_import_path(source: str) -> Path:
    """Terima path tempelan Explorer tanpa mengubah aturan path katalog."""
    value = str(source).strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return config.resolve_path(value)


def import_media(source: str) -> dict:
    """Salin satu media tanpa bertabrakan dengan simpan, hapus, atau awal run."""
    with account_startup_lock(), account_inventory_lock():
        return _import_media_unlocked(_resolve_import_path(source))


def import_media_batch(sources: list[str]) -> list[dict]:
    """Validasi seluruh pilihan sebelum salin; kunci mutasi tetap dipegang selama rollback."""
    with account_startup_lock(), account_inventory_lock():
        resolved = [_resolve_import_path(source) for source in sources]
        names = set()
        for path in resolved:
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS or not MEDIA_NAME.fullmatch(path.name):
                raise ValueError("Pilihan media tidak valid; tidak ada file diimpor.")
            key = path.name.casefold()
            if key in names:
                raise ValueError("Ada nama file duplikat pada pilihan; tidak ada file diimpor.")
            names.add(key)
            target = config.UI_MEDIA_DIR / path.name
            if target.exists() and not target.samefile(path):
                raise ValueError("Nama file sudah ada di input/media; tidak ada file diimpor.")
        imported = []
        created = []
        try:
            for path in resolved:
                target = config.UI_MEDIA_DIR / path.name
                existed = target.exists()
                imported.append(_import_media_unlocked(path))
                if not existed:
                    created.append(target)
        except BaseException:
            for target in created:
                target.unlink(missing_ok=True)
            raise
        return imported


def pick_media_files() -> list[str]:
    """Dialog file Windows; UI localhost tidak boleh menebak path pengguna."""
    script = (
        "import tkinter as tk\nfrom tkinter import filedialog\n"
        "root=tk.Tk();root.withdraw();root.attributes('-topmost',True)\n"
        "paths=filedialog.askopenfilenames(title='Pilih foto/video',filetypes=[('Media','*.jpg *.jpeg *.png *.avif *.heic *.heif *.mp4 *.mov')])\n"
        "print('\\n'.join(paths))"
    )
    completed = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script],
                               capture_output=True, encoding="utf-8", timeout=600)
    if completed.returncode != 0:
        raise ValueError("Dialog pemilihan file tidak tersedia; masukkan path file secara manual.")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def read_json(path: Path, default):
    try:
        return json.loads(read_state_text(path))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def environment() -> dict:
    try:
        chrome, chrome_ok = str(config.chrome_path()), True
    except ValueError as exc:
        chrome, chrome_ok = str(exc), False
    try:
        playwright_version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        playwright_version = "tidak terpasang"
    return {
        "python": platform.python_version(), "playwright": playwright_version,
        "chrome": chrome, "chrome_ok": chrome_ok,
        "session_root": str(config.session_root()), "project_dir": str(config.PROJECT_DIR),
        "media_trash": str(config.UI_MEDIA_TRASH_DIR), "history_file": str(config.UI_RUN_HISTORY_FILE),
        "ledger_file": str(config.LEDGER_FILE),
    }


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _content_results(rows, document: dict) -> list:
    if not isinstance(rows, list):
        return []
    names = {content["content_id"]: content["name"] for content in document["contents"]}
    owners = {job_id: (content_id, account) for content_id, pairs in document["bindings"].items()
              for account, attempts in pairs.items() for job_id in attempts}
    result = []
    for row in rows:
        owner = owners.get(row.get("job_id")) if isinstance(row, dict) else None
        if owner and isinstance(row.get("account"), str) and row["account"].strip().casefold() == owner[1]:
            row = {**row, "content_id": owner[0], "content_name": names.get(owner[0])}
        result.append(row)
    return result


class RunManager:
    """Satu run engine per waktu dengan pilihan eksplisit dan snapshot privat."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._log_path: Path | None = None
        self._snapshot_path: Path | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._settings: dict | None = None
        self._accounts: list[str] = []
        self._contents: list[str] = []
        self._content_names: list[str] = []
        self._posting_count: int | None = None
        self._repost = False
        self._catalog: dict | None = None
        self._exit_code: int | None = None
        self._results_mtime: float | None = None
        self._recorded = True
        self._stop_requested = False

    def start(self, settings: dict, accounts: list[str], contents: list[str] | None = None, repost: bool = False,
              expected_contents: list[dict] | None = None) -> dict:
        settings = normalize_settings(settings)
        if type(repost) is not bool:
            raise ValueError("Pilihan posting ulang harus true/false.")
        if (not isinstance(accounts, list) or not accounts
                or any(not isinstance(value, str) for value in accounts)):
            raise ValueError("Pilih minimal satu akun tujuan dalam daftar akun.")
        wanted_accounts = [_validate_identifier(value).casefold() for value in accounts]
        if len(wanted_accounts) != len(set(wanted_accounts)):
            raise ValueError("Pilihan akun duplikat.")
        if settings["mode"] == "publish":
            if (not isinstance(contents, list) or not contents
                    or any(not isinstance(value, str) or not catalog.CONTENT_ID.fullmatch(value) for value in contents)
                    or len(contents) != len(set(contents))):
                raise ValueError("Pilih daftar konten yang tidak kosong dan tidak duplikat.")
        elif contents is not None and (not isinstance(contents, list) or contents):
            raise ValueError("Pilihan konten hanya berlaku untuk mode posting.")
        elif repost:
            raise ValueError("Posting ulang hanya berlaku untuk mode posting.")
        if expected_contents is not None:
            if settings["mode"] != "publish":
                raise ValueError("Konfirmasi isi konten hanya berlaku untuk mode posting.")
            if not isinstance(expected_contents, list) or any(not isinstance(item, dict) for item in expected_contents):
                raise ValueError("Konfirmasi isi konten harus berupa daftar konten.")
        selected_contents = list(contents or [])
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise ValueError("Masih ada run yang berjalan; hentikan atau tunggu selesai.")
            process = None
            snapshot_path = ready_file = None
            with account_startup_lock():
                try:
                    with account_inventory_lock():
                        available, error = list_accounts()
                        if error:
                            raise ValueError(f"Perbaiki file akun dahulu: {error}")
                        indexed_accounts = {account.identifier.casefold(): account for account in available}
                        if any(account not in indexed_accounts for account in wanted_accounts):
                            raise ValueError("Akun pilihan tidak ditemukan; muat ulang daftar akun.")
                        selected_accounts = [indexed_accounts[account] for account in wanted_accounts]
                        document = catalog.load_catalog(config.UI_JOBS_FILE)
                        if expected_contents is not None:
                            indexed_contents = {item["content_id"]: item for item in document["contents"]}
                            if expected_contents != [indexed_contents.get(content_id) for content_id in selected_contents]:
                                raise ValueError("Konten pilihan berubah; muat ulang dan periksa isinya sebelum memulai posting.")
                        if self._process is not None and not self._recorded:
                            self._record_finish(self._process)
                        ledger = read_ledger(config.LEDGER_FILE)
                        catalog.content_status(document, ledger)
                        manifest = None
                        if settings["mode"] == "publish":
                            document, manifest = catalog.plan_run(document, selected_accounts, selected_contents,
                                                                  ledger, repost,
                                                                  directory=config.UI_JOBS_FILE.parent)
                            passwords = {account.password for account in available if account.password}
                            if any(password in job["job_id"] for job in manifest["jobs"] for password in passwords):
                                raise ValueError("Identitas posting tidak boleh memuat kredensial akun.")
                            snapshot_path = config.OUTPUT_DIR / f".instagram-run-{uuid4().hex}.json"
                            descriptor = os.open(snapshot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                                json.dump(manifest, handle, ensure_ascii=False, allow_nan=False)
                                handle.write("\n")
                                handle.flush()
                                os.fsync(handle.fileno())
                        _commit_catalog(document)
                    # Lepas inventaris SEBELUM Popen; startup tetap terkunci sampai anak siap.
                    command = [sys.executable, "-B", "-X", "utf8", str(config.BASE_DIR / "main.py"), "--mode", settings["mode"],
                               "--accounts", str(config.INPUT_ACCOUNTS_FILE), "--workers", str(settings["workers"]),
                               "--manual-timeout", str(settings["manual_timeout"])]
                    for account in selected_accounts:
                        command += ["--account", account.identifier]
                    if snapshot_path is not None:
                        command += ["--jobs", str(snapshot_path)]
                    if settings["headless"]:
                        command.append("--headless")
                    ready_file = config.OUTPUT_DIR / f".instagram-inventory-ready-{uuid4().hex}.signal"
                    command += ["--inventory-ready-file", str(ready_file)]
                    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                    log_path = config.LOG_FILE.parent / "ui-run.log"
                    results_mtime = _mtime(config.RESULT_FILE)
                    started_at = datetime.now(timezone.utc).isoformat()
                    creation = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                    with log_path.open("w", encoding="utf-8") as handle:
                        process = subprocess.Popen(command, cwd=config.BASE_DIR, stdout=handle,
                                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                                   creationflags=creation, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            if ready_file.read_bytes() == b"ready\n":
                                break
                        except FileNotFoundError:
                            pass
                        if process.poll() is not None:
                            raise ValueError("Engine berhenti sebelum inventaris akun siap; periksa log sebelum mengulang.")
                        if time.monotonic() >= deadline:
                            raise ValueError("Engine belum mengunci inventaris akun; startup dibatalkan sebelum menjalankan browser.")
                        time.sleep(0.02)
                    ready_file.unlink(missing_ok=True)
                except BaseException:
                    # Kegagalan serah-terima tidak boleh meninggalkan proses atau snapshot aktif.
                    try:
                        if process is not None and process.poll() is None:
                            process.kill()
                            process.wait()
                    finally:
                        if process is None or process.poll() is not None:
                            if snapshot_path is not None:
                                snapshot_path.unlink(missing_ok=True)
                            if ready_file is not None:
                                ready_file.unlink(missing_ok=True)
                    raise
            self._process = process
            self._stop_requested = False
            self._log_path = log_path
            self._snapshot_path = snapshot_path
            self._started_at = started_at
            self._finished_at = None
            self._settings = dict(settings)
            self._accounts = [account.identifier for account in selected_accounts]
            self._contents = selected_contents
            names = {content["content_id"]: content["name"] for content in document["contents"]}
            self._content_names = [names[content_id] for content_id in selected_contents]
            self._posting_count = len(manifest["jobs"]) if manifest is not None else len(selected_accounts)
            self._repost = repost
            self._catalog = document
            self._results_mtime = results_mtime
            self._exit_code = None
            self._recorded = False
            threading.Thread(target=self._watch_finish, args=(process,), daemon=True).start()
            return self._status_locked()

    def _watch_finish(self, process: subprocess.Popen) -> None:
        process.wait()
        with self._lock:
            if self._process is process:
                try:
                    self._record_finish(process)
                except (OSError, ValueError):
                    # Status/start berikutnya mencoba menyimpan riwayat lagi, bukan mengabaikannya.
                    pass

    def stop(self) -> dict:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                raise ValueError("Tidak ada run yang berjalan.")
            if not self._stop_requested:
                if os.name == "nt":
                    process.send_signal(subprocess.signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(subprocess.signal.SIGINT)
                self._stop_requested = True
            return self._status_locked()

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def _log_text(self) -> str:
        if self._log_path and self._log_path.is_file():
            return self._log_path.read_text(encoding="utf-8", errors="replace")[-20_000:]
        return ""

    def _results_fresh(self) -> bool:
        return self._started_at is not None and _mtime(config.RESULT_FILE) != self._results_mtime

    def _record_finish(self, process: subprocess.Popen) -> None:
        self._exit_code = process.returncode
        if self._snapshot_path is not None:
            self._snapshot_path.unlink(missing_ok=True)
            self._snapshot_path = None
        if self._recorded:
            return
        if self._finished_at is None:
            self._finished_at = datetime.now(timezone.utc).isoformat()
        results = read_json(config.RESULT_FILE, []) if self._results_fresh() else []
        entry = {"started_at": self._started_at, "finished_at": self._finished_at, "settings": self._settings,
                 "accounts": self._accounts, "contents": self._contents, "content_names": self._content_names,
                 "posting_count": self._posting_count, "repost": self._repost, "exit_code": self._exit_code,
                 "results": _content_results(results, self._catalog), "log_tail": self._log_text()[-6_000:]}
        history = read_json(config.UI_RUN_HISTORY_FILE, [])
        history = history if isinstance(history, list) else []
        history.append(entry)
        del history[:-HISTORY_LIMIT]
        _atomic_write(config.UI_RUN_HISTORY_FILE, json.dumps(history, ensure_ascii=False, indent=2) + "\n")
        self._recorded = True

    @staticmethod
    def _ledger_rows() -> dict:
        return read_ledger(config.LEDGER_FILE)

    def _status_locked(self) -> dict:
        process = self._process
        running = process is not None and process.poll() is None
        if process is not None and not running:
            self._record_finish(process)
        document = catalog.load_catalog(config.UI_JOBS_FILE)
        ledger = self._ledger_rows()
        results = _content_results(read_json(config.RESULT_FILE, []), document)
        return {"running": running, "stopping": running and self._stop_requested,
                "started_at": self._started_at, "finished_at": self._finished_at,
                "settings": self._settings, "accounts": self._accounts, "contents": self._contents,
                "content_names": self._content_names, "posting_count": self._posting_count, "repost": self._repost,
                "exit_code": self._exit_code, "log": self._log_text(), "results": results,
                "results_fresh": self._results_fresh(), "ledger": ledger,
                "content_status": catalog.content_status(document, ledger), "checked_at": time.time()}


def snapshot() -> dict:
    accounts, error = account_rows()
    manifest_error = None
    try:
        manifest = read_manifest()
    except ValueError as exc:
        manifest, manifest_error = json.loads(json.dumps(EMPTY_MANIFEST)), str(exc)
    history = read_json(config.UI_RUN_HISTORY_FILE, [])
    settings_error = None
    try:
        settings = read_settings()
    except ValueError as exc:
        settings, settings_error = dict(DEFAULT_SETTINGS), str(exc)
    return {
        "accounts": accounts,
        "accounts_error": error,
        "manifest": manifest,
        "manifest_error": manifest_error,
        "media": list_media(),
        "settings": settings,
        "settings_error": settings_error,
        "history": history if isinstance(history, list) else [],
        "environment": environment(),
        "paths": {"accounts": str(config.INPUT_ACCOUNTS_FILE), "jobs": str(config.UI_JOBS_FILE), "media": str(config.UI_MEDIA_DIR),
                  "results": str(config.RESULT_FILE), "log": str(config.LOG_FILE)},
        "browser": {"scale": config.BROWSER_SCALE_FACTOR, "width": config.BROWSER_WIDTH, "height": config.BROWSER_HEIGHT,
                    "columns": config.GRID_COLUMNS, "rows": config.GRID_ROWS},
    }
