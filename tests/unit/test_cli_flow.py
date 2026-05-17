"""Tests for the simplified CLI flow.

Covers post-PR-3 entry surfaces:
- ``_load_repo_env``: project-scoped ``.merge/.env`` must override
  stale shell + global ``.env`` fallbacks before any LLM client is
  constructed.
- ``_auto_detect_fork_ref``: git branch detection + fallback.
- ``merge`` (no args) routes to ``web_command_impl(repo_path='.')``.
- ``merge --ci`` with existing config calls ``run_command_impl``.
- ``merge --ci`` with no config synthesises one via
  ``build_default_payload`` + ``apply_setup_payload``, prints the
  path, and continues into the run.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from src.models.config import MergeConfig


def _minimal_config_yaml(
    upstream: str = "upstream/main", fork: str = "feature/x"
) -> str:
    return yaml.dump({"upstream_ref": upstream, "fork_ref": fork})


class TestLoadRepoEnv:
    """``_load_repo_env`` must override existing os.environ values so that
    project-scoped ``.merge/.env`` always wins over stale fallbacks loaded
    by ``load_env()`` (install-tree .env + ``~/.config`` global fallback).
    """

    def test_project_env_overrides_existing_environ(self, tmp_path: Path) -> None:
        from src.cli.main import _load_repo_env

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / ".env").write_text(
            'OPENAI_BASE_URL="https://project-gateway.example.com/v1"\n'
            'OPENAI_API_KEY="sk-project"\n'
        )
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://stale-fallback.example.com",
                "OPENAI_API_KEY": "sk-stale",
            },
            clear=False,
        ):
            _load_repo_env(str(tmp_path))
            assert (
                os.environ["OPENAI_BASE_URL"]
                == "https://project-gateway.example.com/v1"
            )
            assert os.environ["OPENAI_API_KEY"] == "sk-project"

    def test_no_op_when_env_file_absent(self, tmp_path: Path) -> None:
        from src.cli.main import _load_repo_env

        with patch.dict(
            os.environ,
            {"OPENAI_BASE_URL": "https://shell-only.example.com"},
            clear=False,
        ):
            _load_repo_env(str(tmp_path))
            assert os.environ["OPENAI_BASE_URL"] == "https://shell-only.example.com"


class TestAutoDetectForkRef:
    def test_returns_current_branch(self) -> None:
        from src.cli.commands.setup import _auto_detect_fork_ref

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="feature/my-branch\n", returncode=0
            )
            result = _auto_detect_fork_ref(".")

        assert result == "feature/my-branch"

    def test_falls_back_on_detached_head(self) -> None:
        from src.cli.commands.setup import _auto_detect_fork_ref

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="HEAD\n", returncode=0)
            result = _auto_detect_fork_ref(".")

        assert result == "origin/main"

    def test_falls_back_on_git_error(self) -> None:
        from src.cli.commands.setup import _auto_detect_fork_ref

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _auto_detect_fork_ref(".")

        assert result == "origin/main"


class TestMergeCommandRouting:
    def test_help_is_short_and_describes_two_modes(self) -> None:
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["merge", "--help"])
        assert result.exit_code == 0
        # No more target_branch positional argument
        assert "TARGET_BRANCH" not in result.output
        # Both invocations called out somewhere
        assert "--ci" in result.output

    def test_merge_with_no_args_opens_web_setup(self) -> None:
        from src.cli.main import cli

        runner = CliRunner()
        with patch("src.cli.commands.web.web_command_impl") as mock_web:
            result = runner.invoke(cli, ["merge"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        mock_web.assert_called_once_with(
            repo_path=".",
            ws_port=8765,
            web_port=5173,
            open_browser=True,
        )

    def test_merge_ci_with_existing_config_runs_directly(self, tmp_path: Path) -> None:
        from src.cli.main import cli

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "config.yaml").write_text(_minimal_config_yaml(), encoding="utf-8")

        runner = CliRunner()
        with (
            patch(
                "src.cli.paths.get_config_path",
                return_value=merge_dir / "config.yaml",
            ),
            patch("src.cli.commands.run.run_command_impl") as mock_run,
            patch(
                "src.cli.main.get_project_merge_dir",
                return_value=merge_dir,
            ),
        ):
            result = runner.invoke(cli, ["merge", "--ci"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        config = args[0]
        assert isinstance(config, MergeConfig)
        assert config.upstream_ref == "upstream/main"
        assert kwargs["ci"] is True

    def test_merge_ci_without_config_synthesises_one(self, tmp_path: Path) -> None:
        from src.cli.main import cli

        merge_dir = tmp_path / ".merge"
        # NOTE: do NOT pre-create — this is the first-run path
        runner = CliRunner()
        default_payload_mock = MagicMock()
        default_payload_mock.target_branch = "origin/main"
        default_payload_mock.fork_ref = "feature/x"
        fake_config = MergeConfig(upstream_ref="origin/main", fork_ref="feature/x")

        with (
            patch(
                "src.cli.paths.get_config_path",
                return_value=merge_dir / "config.yaml",
            ),
            patch(
                "src.cli.commands.setup.build_default_payload",
                return_value=default_payload_mock,
            ) as mock_build,
            patch(
                "src.cli.commands.setup.apply_setup_payload",
                return_value=fake_config,
            ) as mock_apply,
            patch("src.cli.commands.run.run_command_impl") as mock_run,
            patch(
                "src.cli.main.get_project_merge_dir",
                return_value=merge_dir,
            ),
        ):
            result = runner.invoke(cli, ["merge", "--ci"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        mock_build.assert_called_once()
        mock_apply.assert_called_once()
        mock_run.assert_called_once()
        # User-visible breadcrumb so operators know where the fresh
        # config landed and can review it before the next --ci run.
        assert "Generated default config" in result.output

    def test_merge_loads_repo_env_before_routing(self, tmp_path: Path) -> None:
        # Regression: the dify-plugins planner_judge silently failed
        # because <repo>/.merge/.env was never loaded before LLM clients
        # were constructed. ``merge_command`` must load it ahead of any
        # downstream code path so OPENAI_BASE_URL lands in os.environ.
        from src.cli.main import cli

        sentinel_key = "MERGE_TEST_REPO_ENV_SENTINEL"
        sentinel_val = "loaded_from_repo_env"

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        env_file = merge_dir / ".env"
        env_file.write_text(f'{sentinel_key}="{sentinel_val}"\n', encoding="utf-8")

        runner = CliRunner()
        os.environ.pop(sentinel_key, None)
        try:
            with (
                patch("src.cli.commands.web.web_command_impl"),
                patch(
                    "src.cli.main.get_project_merge_dir",
                    return_value=merge_dir,
                ),
            ):
                result = runner.invoke(cli, ["merge"], catch_exceptions=False)

            assert result.exit_code == 0, result.output
            assert os.environ.get(sentinel_key) == sentinel_val
        finally:
            os.environ.pop(sentinel_key, None)
