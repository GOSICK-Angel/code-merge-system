"""Phase 4 — ``StaticHTTPServer`` ``/runs/<run_id>/<file>`` route tests.

Covers the L5 Report artifact path:
- Allow-listed suffixes (.md / .json / .yaml / .yml / .txt / .log) resolve
  to real files under ``runs_root``
- Path traversal is blocked (``..`` escapes the runs_root → 404)
- Unknown suffixes (e.g. ``.py``) are blocked (404)
- Missing files return 404 (NOT the SPA fallback — otherwise the L5
  Report view renders ``index.html`` as markdown gibberish)
- Without ``runs_root`` configured the route is a no-op (always SPA)
"""

from __future__ import annotations

import socket
from http.client import HTTPConnection
from pathlib import Path

import pytest

from src.web.static_server import StaticHTTPServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return int(s.getsockname()[1])


def _get(port: int, path: str) -> tuple[int, str]:
    """Issue a GET via stdlib ``HTTPConnection`` so the request bypasses
    system proxies (some dev machines route ``localhost`` through a
    debug proxy which fails on threaded local servers)."""
    conn = HTTPConnection("localhost", port, timeout=2.0)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", errors="replace")
    finally:
        conn.close()


def _write(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.fixture
def server_with_runs(tmp_path: Path):
    """Boot a StaticHTTPServer with a Vite-like ``dist/`` and a populated
    ``runs/`` tree. Yields ``(server, dist, runs, port)``."""
    import asyncio

    dist = tmp_path / "dist"
    runs = tmp_path / "runs"
    _write(dist / "index.html", "<html><body>SPA</body></html>")
    _write(dist / "assets" / "app.js", "console.log(1);")
    _write(runs / "r1" / "merge_report.md", "# Hello\n\nrun r1 report")
    _write(runs / "r1" / "checkpoint.json", '{"v":1}')
    _write(runs / "r1" / "secret.py", "should_not_be_served = True")

    port = _free_port()
    server = StaticHTTPServer(dist, runs_root=runs)
    asyncio.run(server.start("localhost", port))
    yield server, dist, runs, port
    asyncio.run(server.stop())


class TestRunsRoute:
    def test_serves_markdown_artifact(self, server_with_runs) -> None:
        _server, _dist, _runs, port = server_with_runs
        status, body = _get(port, "/runs/r1/merge_report.md")
        assert status == 200
        assert body.startswith("# Hello")

    def test_serves_checkpoint_json(self, server_with_runs) -> None:
        _server, _dist, _runs, port = server_with_runs
        status, body = _get(port, "/runs/r1/checkpoint.json")
        assert status == 200
        assert body == '{"v":1}'

    def test_blocks_disallowed_suffix(self, server_with_runs) -> None:
        _server, _dist, _runs, port = server_with_runs
        # ``.py`` is not in the allow-list → hard 404 so callers can
        # tell the artifact is absent rather than getting the SPA
        # HTML back as a 200.
        status, body = _get(port, "/runs/r1/secret.py")
        assert status == 404
        assert "should_not_be_served" not in body

    def test_blocks_path_traversal(self, server_with_runs, tmp_path: Path) -> None:
        _server, _dist, _runs, port = server_with_runs
        # Escape via .. — resolved path is outside runs_root → 404.
        status, _body = _get(port, "/runs/r1/../../dist/index.html")
        assert status == 404

    def test_missing_run_id_returns_404(self, server_with_runs) -> None:
        _server, _dist, _runs, port = server_with_runs
        status, _body = _get(port, "/runs/no_such_run/merge_report.md")
        assert status == 404

    def test_missing_artifact_in_existing_run_returns_404(
        self, server_with_runs
    ) -> None:
        """Regression: the L5 Report view used to render the SPA's
        index.html as markdown when the artifact filename it
        requested didn't exist. The route must return 404 so the
        frontend can show a clear "report not available" error."""
        _server, _dist, _runs, port = server_with_runs
        # ``r1`` exists; this filename does not (the real one carries
        # the run_id suffix: ``merge_report_<run_id>.md``).
        status, body = _get(port, "/runs/r1/merge_report_does_not_exist.md")
        assert status == 404
        assert "SPA" not in body


class TestRunsRouteDisabled:
    def test_no_runs_root_treats_path_as_spa(self, tmp_path: Path) -> None:
        import asyncio

        dist = tmp_path / "dist"
        _write(dist / "index.html", "<html>SPA-only</html>")
        port = _free_port()
        server = StaticHTTPServer(dist, runs_root=None)
        asyncio.run(server.start("localhost", port))
        try:
            status, body = _get(port, "/runs/r1/merge_report.md")
            assert status == 200
            assert "SPA-only" in body
        finally:
            asyncio.run(server.stop())
