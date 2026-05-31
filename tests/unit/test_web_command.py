"""Tests for ``src.cli.commands.web`` post-PR-3.

Two routing branches that the new ``web_command_impl(repo_path, ...)``
makes — both must work without any browser open / network access:

- ``web/dist/index.html`` missing → clean ``SystemExit`` with
  ``EXIT_UNKNOWN_ERROR`` (and no bridge / server / browser
  side-effects).
- ``.merge/config.yaml`` already exists → fast path that constructs a
  ``MergeState`` and goes straight into ``_serve_with_state``
  (no setup bridge involved).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.cli.exit_codes import EXIT_UNKNOWN_ERROR


class TestWebDistMissing:
    def test_exits_cleanly_when_index_html_missing(self, tmp_path: Path) -> None:
        from src.cli.commands import web as web_mod

        missing_dir = tmp_path / "no_such_dist"

        with (
            patch.object(web_mod, "_resolve_web_dist", return_value=missing_dir),
            patch.object(web_mod, "MergeWSBridge") as mock_bridge_cls,
            patch.object(web_mod, "StaticHTTPServer") as mock_static_cls,
            patch.object(web_mod, "webbrowser") as mock_browser,
            pytest.raises(SystemExit) as excinfo,
        ):
            web_mod.web_command_impl(
                repo_path=str(tmp_path),
                ws_port=8765,
                web_port=5173,
                open_browser=True,
            )

        assert excinfo.value.code == EXIT_UNKNOWN_ERROR
        mock_bridge_cls.assert_not_called()
        mock_static_cls.assert_not_called()
        mock_browser.open.assert_not_called()


class TestExistingConfigFastPath:
    def test_existing_config_goes_straight_to_serve_with_state(
        self, tmp_path: Path
    ) -> None:
        from src.cli.commands import web as web_mod

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "config.yaml").write_text(
            "upstream_ref: upstream/main\nfork_ref: feature/x\n",
            encoding="utf-8",
        )

        serve_mock = AsyncMock(return_value=None)
        with (
            patch.object(web_mod, "_resolve_web_dist", return_value=dist),
            patch.object(
                web_mod,
                "get_config_path",
                return_value=merge_dir / "config.yaml",
            ),
            patch.object(web_mod, "_serve_with_state", side_effect=serve_mock),
        ):
            web_mod.web_command_impl(
                repo_path=str(tmp_path),
                ws_port=8765,
                web_port=5173,
                open_browser=False,
            )

        serve_mock.assert_awaited_once()
        call_kwargs = serve_mock.await_args.kwargs
        # MergeConfig was constructed from the on-disk yaml and threaded
        # through to the fast path.
        assert call_kwargs["config"].upstream_ref == "upstream/main"
        assert call_kwargs["open_browser"] is False
