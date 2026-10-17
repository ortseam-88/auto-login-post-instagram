# create by Ortseam
"""Satu konfigurasi mesin Instagram; tidak mempunyai efek samping saat import."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
INPUT_ACCOUNTS_FILE = PROJECT_DIR / "input" / "akun.txt"
OUTPUT_DIR = PROJECT_DIR / "output"
LOG_FILE = PROJECT_DIR / "logs" / "instagram.log"
RESULT_FILE = OUTPUT_DIR / "instagram_results.json"
LEDGER_FILE = OUTPUT_DIR / "publication_ledger.json"
# Pengaturan yang disunting UI lokal; disimpan di luar folder rahasia akun.
UI_JOBS_FILE = PROJECT_DIR / "input" / "jobs.json"
UI_MEDIA_DIR = PROJECT_DIR / "input" / "media"
UI_SETTINGS_FILE = PROJECT_DIR / "input" / "ui-settings.json"
# Media yang dihapus dari UI dipindahkan, tidak dihapus permanen.
UI_MEDIA_TRASH_DIR = PROJECT_DIR / "archives" / "media-dihapus"
UI_RUN_HISTORY_FILE = OUTPUT_DIR / "ui-run-history.json"
UI_HOST = "127.0.0.1"
UI_PORT = 8765
INSTAGRAM_LOGIN_URL = "https://www.instagram.com/?hl=en"
TIMEOUT_NAVIGATE_MS = 45_000
TIMEOUT_ELEMENT_MS = 15_000
LOGIN_RESULT_TIMEOUT_SECS = 45
MANUAL_TIMEOUT_SECS = 300
DEFAULT_WORKERS = 5
PUBLISH_TIMEOUT_SECS = 120
CLOSE_TIMEOUT_SECS = 20
SLOW_MO = 30

# Ukuran/posisi Chromium memakai pixel logis sebelum faktor skala. Pada 0.3,
# jendela 1300x1040 sekitar 390x312 pixel fisik: editor tetap layout desktop.
BROWSER_SCALE_FACTOR = 0.3
BROWSER_WIDTH = 1300
BROWSER_HEIGHT = 1040
GRID_COLUMNS = 4
GRID_ROWS = 3
WINDOW_GAP = 20
WINDOW_LEFT = 20
WINDOW_TOP = 20
# Headless tidak mempunyai jendela OS, sehingga viewport tetap eksplisit.
HEADLESS_VIEWPORT = {"width": 1280, "height": 900}


def resolve_path(value: str | Path) -> Path:
    path = Path(os.path.expandvars(str(value))).expanduser()
    return (path if path.is_absolute() else PROJECT_DIR / path).resolve()


def chrome_path() -> Path:
    value = os.environ.get("CHROME_PATH", "").strip().strip('"')
    if value:
        explicit = resolve_path(value)
        if explicit.is_file():
            return explicit
        raise ValueError(f"CHROME_PATH disetel ke '{value}' namun berkas tidak ditemukan.")

    portable = BASE_DIR / "chrome-portable" / "chrome-win64" / "chrome.exe"
    if portable.is_file():
        return portable

    # Fallback ke Google Chrome standar pada Windows jika folder portable tidak ada
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise ValueError(
        "Chrome tidak ditemukan. Silakan pasang Google Chrome, setel environment "
        "variable CHROME_PATH, atau unduh paket portable lengkap dari GitHub Releases: "
        "https://github.com/ortseam-88/auto-login-post-instagram/releases"
    )


def session_root() -> Path:
    value = os.environ.get("IG_SESSION_DIR", "").strip().strip('"')
    if value:
        return resolve_path(value)
    return PROJECT_DIR / "sessions"
