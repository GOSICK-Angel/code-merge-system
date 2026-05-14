"""Static HTTP server for the Web UI bundle.

This module serves the compiled Vite output (``web/dist/``) and falls back to
``index.html`` for unknown paths (SPA routing). It has **no** API surface — the
WebSocket bridge (`src.web.ws_bridge`) is the only data channel between the
backend and the browser.
"""

from __future__ import annotations

import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

logger = logging.getLogger(__name__)


class _SPAHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with SPA fallback + path-traversal guard."""

    root: Path = Path()

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        root_resolved = self.root.resolve()
        target = (self.root / clean).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            return str(root_resolved / "index.html")
        if target.is_dir() or not target.exists():
            return str(root_resolved / "index.html")
        return str(target)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info(fmt, *args)


class StaticHTTPServer:
    """Threaded stdlib HTTP server that serves a single root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    async def start(self, host: str = "localhost", port: int = 5173) -> None:
        handler_cls = type("_BoundSPAHandler", (_SPAHandler,), {"root": self.root})
        self._server = ThreadingHTTPServer((host, port), handler_cls)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(
            "Static server listening on http://%s:%d (root=%s)",
            host,
            port,
            self.root,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
