"""Static HTTP server for the Web UI bundle.

Serves the compiled Vite output (``web/dist/``) and falls back to
``index.html`` for unknown paths (SPA routing). Also serves the
per-run artifact tree (``<repo>/.merge/runs/<run_id>/``) under the
``/runs/<run_id>/<filename>`` URL prefix when ``runs_root`` is
provided — used by the L5 Report view to fetch ``merge_report.md`` and
``checkpoint.json``. Allowed file extensions for the runs tree are
locked down to documentation + checkpoint formats so a malformed URL
can't read arbitrary repo files.
"""

from __future__ import annotations

import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, BinaryIO
from io import BytesIO

logger = logging.getLogger(__name__)

_RUNS_URL_PREFIX = "/runs/"
_RUNS_ALLOWED_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".txt", ".log"}


class _SPAHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with SPA fallback, ``/runs/`` route,
    and path-traversal guard on both roots."""

    root: Path = Path()
    runs_root: Path | None = None

    def send_head(self) -> BytesIO | BinaryIO | None:
        # Short-circuit the ``/runs/`` route with a hard 404 when the
        # requested artifact is missing / disallowed / path-traversal,
        # instead of returning the SPA index.html. The SPA fallback
        # was confusing the L5 Report view: a missing report came
        # back as 200 + HTML and rendered as markdown gibberish.
        #
        # ``send_error`` writes the status line + headers + body and
        # marks the connection ``Connection: close``; returning
        # ``None`` matches the stdlib pattern (``do_GET`` / ``do_HEAD``
        # skip ``copyfile`` when the value is falsy) so the response
        # is fully framed and the socket isn't reset.
        path = (self.path or "").split("?", 1)[0].split("#", 1)[0]
        if (
            self.runs_root is not None
            and path.startswith(_RUNS_URL_PREFIX)
            and self._translate_runs_path(path) is None
        ):
            self.send_error(404, "Run artifact not found")
            return None
        return super().send_head()

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if self.runs_root is not None and clean.startswith(_RUNS_URL_PREFIX):
            translated = self._translate_runs_path(clean)
            if translated is not None:
                return translated
            # send_head already responded with 404 for the misses; this
            # branch is only hit by callers bypassing send_head (none
            # in production), so still return something safe.
            return str(self.root.resolve() / "index.html")
        clean_path = clean.lstrip("/")
        root_resolved = self.root.resolve()
        target = (self.root / clean_path).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            return str(root_resolved / "index.html")
        if target.is_dir() or not target.exists():
            return str(root_resolved / "index.html")
        return str(target)

    def _translate_runs_path(self, clean: str) -> str | None:
        """Resolve ``/runs/<run_id>/<path>`` → file under ``runs_root``.

        Returns ``None`` (caller falls back to SPA) when:
        - run_id is empty
        - resolved path escapes ``runs_root``
        - the file's suffix isn't in the allow-list
        - the file doesn't exist
        """
        assert self.runs_root is not None
        rel = clean[len(_RUNS_URL_PREFIX) :]
        if not rel:
            return None
        runs_resolved = self.runs_root.resolve()
        target = (self.runs_root / rel).resolve()
        try:
            target.relative_to(runs_resolved)
        except ValueError:
            return None
        if not target.exists() or not target.is_file():
            return None
        if target.suffix.lower() not in _RUNS_ALLOWED_SUFFIXES:
            return None
        return str(target)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info(fmt, *args)


class StaticHTTPServer:
    """Threaded stdlib HTTP server that serves a Vite ``dist/`` root and
    an optional ``runs/`` artifact tree."""

    def __init__(self, root: Path, runs_root: Path | None = None) -> None:
        self.root = root
        self.runs_root = runs_root
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    async def start(self, host: str = "localhost", port: int = 5173) -> None:
        bound_root = self.root
        bound_runs_root = self.runs_root

        class _BoundSPAHandler(_SPAHandler):
            root = bound_root
            runs_root = bound_runs_root

        self._server = ThreadingHTTPServer((host, port), _BoundSPAHandler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(
            "Static server listening on http://%s:%d (root=%s, runs_root=%s)",
            host,
            port,
            self.root,
            self.runs_root,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
