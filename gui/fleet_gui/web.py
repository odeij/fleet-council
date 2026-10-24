"""HTTP front end: the static page and a small read-only JSON API.

Routes:
    GET  /, /app.js, /styles.css, /theme.js   The page.
    GET  /api/state                           Every machine, job and GPU.
    GET  /api/log?rig=NAME&path=PATH&n=80     Tail of one log file.
    POST /api/refresh                         Poll the fleet now.

/api/log only tails paths that the machine's own collector listed, so the
server never runs an arbitrary command on a machine.
"""

from __future__ import annotations

import http
import http.server
import json
import pathlib
import time
import urllib.parse
from typing import Any

from fleet_gui import poller
from fleet_gui import sources
from fleet_gui import state

_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
_DEFAULT_LOG_LINES = 80
_MIN_LOG_LINES = 10
_MAX_LOG_LINES = 400


def make_server(
    port: int,
    fleet_state: state.FleetState,
    source: sources.Source,
    fleet_poller: poller.Poller,
    bind: str = "127.0.0.1",
) -> http.server.ThreadingHTTPServer:
    """Builds the HTTP server. Call serve_forever() on the result."""
    handler = _make_handler(fleet_state, source, fleet_poller)
    server = http.server.ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    return server


def _make_handler(
    fleet_state: state.FleetState,
    source: sources.Source,
    fleet_poller: poller.Poller,
) -> type[http.server.BaseHTTPRequestHandler]:
    """Returns a request handler class bound to this fleet."""

    class Handler(http.server.BaseHTTPRequestHandler):
        """Serves the dashboard."""

        server_version = "fleet-gui"

        # pylint: disable-next=redefined-builtin
        def log_message(self, format: str, *args: Any) -> None:
            del format, args  # The page polls every few seconds; stay quiet.

        def do_GET(self) -> None:  # pylint: disable=invalid-name
            url = urllib.parse.urlsplit(self.path)
            if url.path in _STATIC_FILES:
                self._send_static(*_STATIC_FILES[url.path])
            elif url.path == "/api/state":
                self._send_json(http.HTTPStatus.OK, fleet_state.to_json())
            elif url.path == "/api/log":
                self._send_log(urllib.parse.parse_qs(url.query))
            else:
                self._send_not_found()

        def do_POST(self) -> None:  # pylint: disable=invalid-name
            if urllib.parse.urlsplit(self.path).path == "/api/refresh":
                fleet_poller.refresh()
                self._send_json(http.HTTPStatus.ACCEPTED, {"ok": True})
            else:
                self._send_not_found()

        def _send_log(self, query: dict[str, list[str]]) -> None:
            name = query.get("rig", [""])[0]
            path = query.get("path", [""])[0]
            try:
                lines = int(query.get("n", [str(_DEFAULT_LOG_LINES)])[0])
            except ValueError:
                self._send_json(http.HTTPStatus.BAD_REQUEST,
                                {"error": "n must be a number"})
                return
            lines = max(_MIN_LOG_LINES, min(lines, _MAX_LOG_LINES))
            host, allowed = fleet_state.log_target(name)
            if host is None or path not in allowed:
                self._send_json(http.HTTPStatus.NOT_FOUND, {
                    "error": "unknown machine or log (only logs listed by the "
                             "collector can be tailed)"})
                return
            ok, text = source.tail(host, path, lines)
            self._send_json(http.HTTPStatus.OK, {
                "rig": name, "path": path, "n": lines, "ok": ok, "text": text,
                "fetched": int(time.time())})

        def _send_not_found(self) -> None:
            self._send_json(http.HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _send_static(self, filename: str, content_type: str) -> None:
            self._send(http.HTTPStatus.OK,
                       (_STATIC_DIR / filename).read_bytes(), content_type)

        def _send_json(self, status: http.HTTPStatus, body: Any) -> None:
            self._send(status, json.dumps(body).encode(), "application/json")

        def _send(
            self, status: http.HTTPStatus, data: bytes, content_type: str
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

    return Handler
