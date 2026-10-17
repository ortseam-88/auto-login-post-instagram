# create by Ortseam
"""Server localhost untuk UI: hanya loopback, JSON API, dan halaman statis."""
from __future__ import annotations

import json
import secrets
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import config
import ui_backend

UI_DIR = Path(__file__).resolve().parent / "ui"
MAX_BODY = 2_000_000
FONT_FILES = frozenset({"/fonts/roboto.woff2", "/fonts/material-symbols-outlined.woff2"})


class UIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address):
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(24)
        self.runs = ui_backend.RunManager()


class Handler(BaseHTTPRequestHandler):
    server: UIServer

    def log_message(self, format, *args):  # noqa: A002 - tanda tangan stdlib
        return

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str, content_type: str) -> None:
        path = UI_DIR / name
        if not path.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Berkas UI tidak ditemukan."})
            return
        self._bytes(path.read_bytes(), content_type, csp=True)

    def _bytes(self, body: bytes, content_type: str, *, csp: bool = False) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if csp:
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; font-src 'self'; form-action 'self'")
        else:
            self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _trusted_request(self) -> bool:
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in hosts and (origin is None or origin in {f"http://{value}" for value in hosts})

    def _authorized(self) -> bool:
        # Token per proses mencegah situs lain di browser memicu run/perubahan.
        return self.headers.get("X-UI-Token") == self.server.token

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("Ukuran permintaan tidak valid.")
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Body harus objek JSON.")
        return payload

    def do_GET(self):
        if not self._trusted_request():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Host atau Origin UI tidak cocok."})
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._static("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
        elif path == "/app.css":
            self._static("app.css", "text/css; charset=utf-8")
        elif path in FONT_FILES:
            self._static(f"fonts/{path[len('/fonts/'):]}", "font/woff2")
        elif path == "/api/bootstrap":
            self._json(HTTPStatus.OK, {"token": self.server.token, **ui_backend.snapshot()})
        elif path == "/api/state":
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Token UI tidak cocok."})
                return
            self._json(HTTPStatus.OK, ui_backend.snapshot())
        elif path == "/api/run":
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Token UI tidak cocok."})
                return
            try:
                status = self.server.runs.status()
            except (ValueError, OSError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc) if isinstance(exc, ValueError) else "Hasil run tidak dapat dibaca."})
                return
            self._json(HTTPStatus.OK, status)
        elif path.startswith("/api/media/file/"):
            # Pratinjau media memakai <img>/<video>, yang tidak dapat mengirim
            # header token; hanya file di input/media yang bisa dibaca.
            try:
                media = ui_backend.media_path(unquote(path[len("/api/media/file/"):]))
            except ValueError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            self._bytes(media.read_bytes(), ui_backend.MEDIA_TYPES[media.suffix.lower()])
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Tidak ditemukan."})

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._trusted_request():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Host atau Origin UI tidak cocok."})
            return
        if not self._authorized():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Token UI tidak cocok; muat ulang halaman."})
            return
        try:
            body = self._body()
            if path == "/api/accounts":
                ui_backend.add_account(body.get("identifier", ""), body.get("password", ""))
                result = {"ok": True}
            elif path == "/api/accounts/bulk":
                result = {"ok": True, **ui_backend.add_accounts_bulk(body.get("text", ""))}
            elif path == "/api/accounts/delete":
                result = {"ok": True, **ui_backend.remove_account(body.get("identifier", ""))}
            elif path == "/api/jobs":
                result = {"ok": True, "content_ids": ui_backend.save_manifest(body.get("manifest"))}
            elif path == "/api/settings":
                result = {"ok": True, "settings": ui_backend.save_settings(body.get("settings"))}
            elif path == "/api/media/pick":
                result = {"ok": True, "imported": ui_backend.import_media_batch(ui_backend.pick_media_files())}
            elif path == "/api/media/import":
                result = {"ok": True, "imported": [ui_backend.import_media(str(body.get("path", "")))]}
            elif path == "/api/media/delete":
                result = {"ok": True, **ui_backend.remove_media(str(body.get("name", "")))}
            elif path == "/api/run/start":
                if set(body) - {"settings", "accounts", "contents", "repost", "confirm", "expected_contents"}:
                    raise ValueError("Gunakan pilihan accounts dan contents; format run lama tidak didukung.")
                settings = ui_backend.normalize_settings(body["settings"] if "settings" in body else ui_backend.read_settings())
                if settings["mode"] == "publish" and body.get("confirm") != "PUBLISH":
                    raise ValueError("Mode publish memerlukan konfirmasi PUBLISH.")
                result = self.server.runs.start(settings, body.get("accounts"), body.get("contents"),
                                                body.get("repost", False), body.get("expected_contents"))
            elif path == "/api/run/stop":
                result = self.server.runs.stop()
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Tidak ditemukan."})
                return
        except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc) if isinstance(exc, ValueError) else "Operasi file gagal."})
            return
        self._json(HTTPStatus.OK, result)


def find_port(host: str, preferred: int) -> int:
    for candidate in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise ValueError("Tidak ada port localhost yang bebas.")


def create_server(host: str = config.UI_HOST, port: int | None = None) -> UIServer:
    return UIServer((host, port if port is not None else find_port(host, config.UI_PORT)))
