"""Unit tests for the TUI dependency-bootstrap helper.

The helper exists because first-run users hit a silent hang: with
``tui/node_modules`` absent, ``npx tsx`` exits immediately while the
Python side has already muted stdio and is waiting on the WebSocket
handshake. ``_ensure_tui_deps`` runs ``npm install`` *before* muting
and surfaces failures clearly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.cli.commands.tui import _ensure_tui_deps


class TestEnsureTuiDeps:
    def test_node_modules_present_skips_install(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules").mkdir()
        with patch("src.cli.commands.tui.subprocess.run") as run:
            assert _ensure_tui_deps(tmp_path, stdout_fd=1) is True
            run.assert_not_called()

    def test_npm_missing_returns_false(self, tmp_path: Path) -> None:
        with patch("src.cli.commands.tui.shutil.which", return_value=None):
            assert _ensure_tui_deps(tmp_path, stdout_fd=1) is False

    def test_install_runs_when_node_modules_absent(self, tmp_path: Path) -> None:
        with (
            patch("src.cli.commands.tui.shutil.which", return_value="/usr/bin/npm"),
            patch("src.cli.commands.tui.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0)
            assert _ensure_tui_deps(tmp_path, stdout_fd=1) is True
            run.assert_called_once()
            args, kwargs = run.call_args
            assert args[0] == ["/usr/bin/npm", "install"]
            assert kwargs["cwd"] == str(tmp_path)
            # npm output must reach the live terminal, not be muted.
            assert kwargs["stdout"] == 1
            assert kwargs["stderr"] == 1

    def test_install_failure_returns_false(self, tmp_path: Path) -> None:
        with (
            patch("src.cli.commands.tui.shutil.which", return_value="/usr/bin/npm"),
            patch("src.cli.commands.tui.subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=1)
            assert _ensure_tui_deps(tmp_path, stdout_fd=1) is False

    def test_install_oserror_returns_false(self, tmp_path: Path) -> None:
        with (
            patch("src.cli.commands.tui.shutil.which", return_value="/usr/bin/npm"),
            patch(
                "src.cli.commands.tui.subprocess.run",
                side_effect=OSError("permission denied"),
            ),
        ):
            assert _ensure_tui_deps(tmp_path, stdout_fd=1) is False


def test_subprocess_module_imported_for_test_mocking() -> None:
    # Sanity check: the helper imports the actual stdlib subprocess
    # module so the patch targets above resolve correctly.
    from src.cli.commands import tui

    assert tui.subprocess is subprocess
