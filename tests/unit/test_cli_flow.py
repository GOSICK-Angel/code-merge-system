"""Tests for Phase D: CLI one-stop flow.

Covers:
- detect_or_setup: repeat-run path (config exists)
- detect_or_setup: first-run path (interactive wizard)
- _resolve_api_keys: three-tier resolution chain
- _auto_detect_fork_ref: git branch detection + fallback
- merge subcommand routing (TUI vs CI vs no-tui)
- _DefaultGroup: unknown command forwarded to 'merge'
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

        # No .merge directory — should silently no-op without raising.
        with patch.dict(
            os.environ,
            {"OPENAI_BASE_URL": "https://shell-only.example.com"},
            clear=False,
        ):
            _load_repo_env(str(tmp_path))
            assert os.environ["OPENAI_BASE_URL"] == "https://shell-only.example.com"


class TestResolveApiKeys:
    def test_env_var_takes_priority(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _resolve_api_keys

        project_env = tmp_path / ".merge" / ".env"
        project_env.parent.mkdir(parents=True)
        project_env.write_text('ANTHROPIC_API_KEY="from-file"\n', encoding="utf-8")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "from-env"}, clear=False):
            result = _resolve_api_keys(str(tmp_path))

        assert result["ANTHROPIC_API_KEY"] == "from-env"

    def test_project_env_overrides_global(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _resolve_api_keys

        global_env = tmp_path / "global.env"
        global_env.write_text('ANTHROPIC_API_KEY="global"\n', encoding="utf-8")

        project_dir = tmp_path / "project"
        project_env = project_dir / ".merge" / ".env"
        project_env.parent.mkdir(parents=True)
        project_env.write_text('ANTHROPIC_API_KEY="project"\n', encoding="utf-8")

        env_without_key = {
            k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"
        }
        with (
            patch(
                "src.cli.commands.setup.get_global_env_path", return_value=global_env
            ),
            patch.dict(os.environ, env_without_key, clear=True),
        ):
            result = _resolve_api_keys(str(project_dir))

        assert result["ANTHROPIC_API_KEY"] == "project"

    def test_global_env_used_as_fallback(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import _resolve_api_keys

        global_env = tmp_path / "global.env"
        global_env.write_text('ANTHROPIC_API_KEY="global"\n', encoding="utf-8")

        repo = tmp_path / "repo"
        repo.mkdir()

        env_without_key = {
            k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"
        }
        with (
            patch(
                "src.cli.commands.setup.get_global_env_path", return_value=global_env
            ),
            patch.dict(os.environ, env_without_key, clear=True),
        ):
            result = _resolve_api_keys(str(repo))

        assert result.get("ANTHROPIC_API_KEY") == "global"


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


class TestDetectOrSetup:
    def test_repeat_run_loads_existing_config(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import detect_or_setup

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        config_file = merge_dir / "config.yaml"
        config_file.write_text(
            _minimal_config_yaml(upstream="old/main", fork="feature/x"),
            encoding="utf-8",
        )

        with patch("src.cli.commands.setup._ask", return_value=""):
            result = detect_or_setup("new/upstream", repo_path=str(tmp_path))

        assert isinstance(result, MergeConfig)
        assert result.upstream_ref == "new/upstream"
        assert result.fork_ref == "feature/x"

    def test_repeat_run_reconfigure_flag_triggers_wizard(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import detect_or_setup

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "config.yaml").write_text(
            _minimal_config_yaml(fork="feature/x"), encoding="utf-8"
        )

        wizard_config = MergeConfig(upstream_ref="up/main", fork_ref="new/branch")
        with patch(
            "src.cli.commands.setup._interactive_setup", return_value=wizard_config
        ) as mock_wizard:
            result = detect_or_setup(
                "up/main", repo_path=str(tmp_path), reconfigure=True
            )

        mock_wizard.assert_called_once_with("up/main", str(tmp_path))
        assert result is wizard_config

    def test_first_run_calls_interactive_setup(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import detect_or_setup

        wizard_config = MergeConfig(upstream_ref="up/main", fork_ref="feature/y")
        with patch(
            "src.cli.commands.setup._interactive_setup", return_value=wizard_config
        ) as mock_wizard:
            result = detect_or_setup("up/main", repo_path=str(tmp_path))

        mock_wizard.assert_called_once_with("up/main", str(tmp_path))
        assert result is wizard_config

    def test_corrupt_config_falls_back_to_wizard(self, tmp_path: Path) -> None:
        from src.cli.commands.setup import detect_or_setup

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "config.yaml").write_text("{ invalid yaml: [", encoding="utf-8")

        wizard_config = MergeConfig(upstream_ref="up/main", fork_ref="feature/z")
        with patch(
            "src.cli.commands.setup._interactive_setup", return_value=wizard_config
        ) as mock_wizard:
            result = detect_or_setup("up/main", repo_path=str(tmp_path))

        mock_wizard.assert_called_once()
        assert result is wizard_config


class TestMergeCommand:
    def test_merge_subcommand_help(self) -> None:
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["merge", "--help"])
        assert result.exit_code == 0
        assert "TARGET_BRANCH" in result.output

    def test_merge_routes_to_web_by_default(self) -> None:
        from src.cli.main import cli

        fake_config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
        runner = CliRunner()
        with (
            patch("src.cli.commands.setup.detect_or_setup", return_value=fake_config),
            patch("src.cli.commands.web.web_command_impl") as mock_web,
        ):
            runner.invoke(cli, ["merge", "upstream/main"])

        mock_web.assert_called_once_with(
            fake_config,
            ws_port=8765,
            web_port=5173,
            dry_run=False,
            open_browser=True,
        )

    def test_merge_no_web_routes_to_run(self) -> None:
        from src.cli.main import cli

        fake_config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
        runner = CliRunner()
        with (
            patch("src.cli.commands.setup.detect_or_setup", return_value=fake_config),
            patch("src.cli.commands.run.run_command_impl") as mock_run,
        ):
            runner.invoke(cli, ["merge", "upstream/main", "--no-web"])

        mock_run.assert_called_once_with(
            fake_config, False, ci=False, auto_decisions=None
        )

    def test_merge_no_tui_alias_routes_to_run(self) -> None:
        from src.cli.main import cli

        fake_config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
        runner = CliRunner()
        with (
            patch("src.cli.commands.setup.detect_or_setup", return_value=fake_config),
            patch("src.cli.commands.run.run_command_impl") as mock_run,
        ):
            result = runner.invoke(cli, ["merge", "upstream/main", "--no-tui"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once_with(
            fake_config, False, ci=False, auto_decisions=None
        )

    def test_merge_ci_flag_routes_to_run(self) -> None:
        from src.cli.main import cli

        fake_config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
        runner = CliRunner()
        with (
            patch("src.cli.commands.setup.detect_or_setup", return_value=fake_config),
            patch("src.cli.commands.run.run_command_impl") as mock_run,
        ):
            runner.invoke(cli, ["merge", "upstream/main", "--ci"])

        mock_run.assert_called_once_with(
            fake_config, False, ci=True, auto_decisions=None
        )

    def test_merge_loads_repo_env_before_setup(self, tmp_path) -> None:
        # Regression: the dify-plugins planner_judge silently failed
        # because <repo>/.merge/.env was never loaded before LLM clients
        # were constructed. ``merge_command`` must load it ahead of
        # detect_or_setup so OPENAI_BASE_URL et al. land in os.environ.
        #
        # We use a unique sentinel key (not OPENAI_BASE_URL) so the
        # assertion isn't shadowed by an install-tree .env or a developer
        # shell that already exports the production keys.
        import os as _os

        from src.cli.main import cli

        sentinel_key = "MERGE_TEST_REPO_ENV_SENTINEL"
        sentinel_val = "loaded_from_repo_env"

        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        env_file = merge_dir / ".env"
        env_file.write_text(f'{sentinel_key}="{sentinel_val}"\n', encoding="utf-8")

        fake_config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
        runner = CliRunner()
        _os.environ.pop(sentinel_key, None)

        try:
            with (
                patch(
                    "src.cli.commands.setup.detect_or_setup", return_value=fake_config
                ),
                patch("src.cli.commands.run.run_command_impl"),
                patch(
                    "src.cli.main.get_project_merge_dir",
                    return_value=tmp_path / ".merge",
                ),
            ):
                result = runner.invoke(cli, ["merge", "upstream/main", "--no-tui"])

            assert result.exit_code == 0, result.output
            assert _os.environ.get(sentinel_key) == sentinel_val
        finally:
            _os.environ.pop(sentinel_key, None)


class TestDefaultGroup:
    def test_unknown_command_forwarded_to_merge(self) -> None:
        from src.cli.main import cli

        fake_config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/x")
        runner = CliRunner()
        with (
            patch("src.cli.commands.setup.detect_or_setup", return_value=fake_config),
            patch("src.cli.commands.web.web_command_impl"),
        ):
            result = runner.invoke(cli, ["upstream/main"])

        assert result.exit_code == 0

    def test_known_subcommand_not_affected(self) -> None:
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["resume", "--help"])
        assert result.exit_code == 0
        assert "--run-id" in result.output
