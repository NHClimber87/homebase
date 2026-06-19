"""The loopback HTTP server (§2b keystone).

Every response carries the strict, directive-complete CSP (AC-PRIV-6) + hardening headers.
Every request is checked against a Host allow-list (anti-DNS-rebinding, AC-PRIV-5) and
every state-changing POST requires same-origin (anti-CSRF). No CORS header is ever emitted.
Request URLs/queries are never written to any log file (AC-PRIV-13).

The bind address comes from the validated bind enum; binding a routable interface is
impossible by construction (config clamps it, and we only ever pass a loopback address here).
"""
from __future__ import annotations

import json
import mimetypes
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .app import App

STATIC_DIR = Path(__file__).with_name("static")

CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self'; script-src 'self'; "
    "style-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)

_STATIC_WHITELIST = {"index.html", "app.js", "style.css", "favicon.ico"}


def bind_host(bind: str) -> str:
    if bind == "::1":
        return "::1"
    # "loopback" and "127.0.0.1" both bind the IPv4 loopback (the homepage uses 127.0.0.1).
    return "127.0.0.1"


def _allowed_hosts(port: int):
    return {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}", f"::1:{port}"}


def _allowed_origins(port: int):
    return {
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeBase"
    protocol_version = "HTTP/1.1"

    # --- never write request URLs/queries to a log (AC-PRIV-13) ----------------------
    def log_message(self, *args, **kwargs):  # noqa: D401
        return

    # --- security gates ---------------------------------------------------------------
    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def _port(self) -> int:
        return self.app.config.port

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        return host in _allowed_hosts(self._port())

    def _same_origin_ok(self) -> bool:
        """Anti-CSRF gate for state-changing POSTs. Requires a POSITIVE same-origin signal:
        an allow-listed Origin, or Sec-Fetch-Site=same-origin/none (a browser-forbidden
        header page JS cannot forge). If BOTH are absent, the request is refused — a
        same-origin browser fetch always sends at least one of them."""
        port = self._port()
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin in _allowed_origins(port)
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs is not None:
            return sfs in ("same-origin", "none")
        return False  # neither signal present -> refuse the state change

    def _send_headers(self, status: int, ctype: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        # NB: deliberately NO Access-Control-Allow-Origin (no CORS).
        self.end_headers()

    def _respond(self, status: int, body: bytes, ctype: str) -> None:
        self._send_headers(status, ctype, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._respond(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _deny(self, status: int, msg: str) -> None:
        self._respond(status, json.dumps({"error": msg}).encode("utf-8"), "application/json; charset=utf-8")

    # --- routing ----------------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_allowed():
            return self._deny(403, "host not allowed (anti-DNS-rebinding)")
        path = urlsplit(self.path).path
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/state":
            return self._json(self.app.state_dict())
        if path == "/api/config":
            return self._json(self.app.config.to_dict())
        if path == "/favicon.ico":
            return self._respond(204, b"", "image/x-icon")
        return self._deny(404, "not found")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if not self._host_allowed():
            return self._deny(403, "host not allowed (anti-DNS-rebinding)")
        if not self._same_origin_ok():
            return self._deny(403, "cross-origin request refused (anti-CSRF)")
        path = urlsplit(self.path).path
        if path == "/api/refresh":
            ok, msg = self.app.force_refresh()
            return self._json({"ok": ok, "message": msg}, status=200 if ok else 429)
        if path == "/api/config":
            body = self._read_json()
            if body is None:
                return self._deny(400, "invalid JSON body")
            try:
                return self._json(self.app.update_config(body))
            except Exception as exc:  # validation failure -> 400, never crash
                return self._deny(400, f"config rejected: {exc}")
        if path == "/api/config/reset":
            return self._json(self.app.reset_config())
        return self._deny(404, "not found")

    # --- helpers ----------------------------------------------------------------------
    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > 1_000_000:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return None

    def _serve_static(self, name: str) -> None:
        name = name.split("?", 1)[0].strip("/")
        if name not in _STATIC_WHITELIST:
            return self._deny(404, "not found")
        fpath = STATIC_DIR / name
        if not fpath.is_file():
            return self._deny(404, "not found")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".js"):
            ctype = "application/javascript"
        self._respond(200, fpath.read_bytes(), ctype)


class HomeBaseServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, app: App):
        self.app = app
        host = bind_host(app.config.bind)
        # Set the address family BEFORE binding (server_bind runs in __init__).
        self.address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        super().__init__((host, app.config.port), Handler)


def serve(app: App) -> HomeBaseServer:
    """Construct + return a server bound to loopback. Caller runs serve_forever()."""
    return HomeBaseServer(app)
