# create by Ortseam
"""Start UI lokal dengan Chrome bawaan dan profil dashboard di folder program."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import config
import ui_server  # noqa: E402


def main() -> int:
    no_browser = "--no-browser" in sys.argv[1:]
    server = ui_server.create_server()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"UI Instagram: {url}\nTekan Ctrl+C untuk berhenti.", flush=True)
    if not no_browser:
        try:
            dashboard_profile = config.session_root() / "dashboard"
            dashboard_profile.mkdir(parents=True, exist_ok=True)
            subprocess.Popen([
                str(config.chrome_path()),
                f"--user-data-dir={dashboard_profile}",
                "--no-first-run", "--no-default-browser-check",
                f"--app={url}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as exc:
            server.server_close()
            print(f"Chrome dashboard gagal dibuka: {exc}", file=sys.stderr)
            return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("UI dihentikan.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
