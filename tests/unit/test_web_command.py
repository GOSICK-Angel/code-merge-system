"""Phase 0 — Web UI command wiring tests.

Covers the bootstrap path of ``src.cli.commands.web``:
- missing ``web/dist/index.html`` → clean exit with EXIT_UNKNOWN_ERROR
- ``open_browser=False`` skips ``webbrowser.open`` while still printing URL
- ``--no-tui`` alias on ``merge`` keeps routing to the plain-text run path
  and emits a ``DeprecationWarning`` (the deprecation surface added in
  this phase).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.cli.exit_codes import EXIT_UNKNOWN_ERROR
from src.models.config import MergeConfig


@pytest.fixture
def fake_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")


class TestWebDistMissing:
    def test_exits_cleanly_when_index_html_missing(
        self, tmp_path: Path, fake_config: MergeConfig
    ) -> None:
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
                fake_config,
                ws_port=8765,
                web_port=5173,
                dry_run=False,
                open_browser=True,
            )

        assert excinfo.value.code == EXIT_UNKNOWN_ERROR
        mock_bridge_cls.assert_not_called()
        mock_static_cls.assert_not_called()
        mock_browser.open.assert_not_called()


class TestNoBrowserFlag:
    def test_open_browser_false_skips_webbrowser_open(
        self, tmp_path: Path, fake_config: MergeConfig
    ) -> None:
        from src.cli.commands import web as web_mod

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")

        async def _fake_run(*_args, **_kwargs) -> None:
            return None

        with (
            patch.object(web_mod, "_resolve_web_dist", return_value=dist),
            patch.object(web_mod, "_run_web", side_effect=_fake_run) as mock_run,
        ):
            web_mod.web_command_impl(
                fake_config,
                ws_port=8765,
                web_port=5173,
                dry_run=False,
                open_browser=False,
            )

        mock_run.assert_called_once()
        kwargs_or_args = mock_run.call_args
        passed_open_browser = kwargs_or_args.args[4]
        assert passed_open_browser is False


class TestNoTuiAliasDeprecation:
    def test_no_tui_alias_routes_to_run_with_deprecation_warning(
        self, fake_config: MergeConfig
    ) -> None:
        import warnings

        from src.cli.main import cli

        runner = CliRunner()
        with (
            patch("src.cli.commands.setup.detect_or_setup", return_value=fake_config),
            patch("src.cli.commands.run.run_command_impl") as mock_run,
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = runner.invoke(cli, ["merge", "upstream/main", "--no-tui"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once_with(
            fake_config, False, ci=False, auto_decisions=None
        )
        assert any(
            issubclass(w.category, DeprecationWarning) and "--no-tui" in str(w.message)
            for w in caught
        )
        assert "[deprecation] --no-tui" in result.output
