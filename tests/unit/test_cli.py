import yaml
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from click.testing import CliRunner

from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")


def _make_state(status: SystemStatus = SystemStatus.COMPLETED) -> MergeState:
    state = MergeState(config=_make_config())
    state.status = status
    return state


def _write_config_file(path: Path, extra: dict | None = None) -> Path:
    config_data = {
        "upstream_ref": "upstream/main",
        "fork_ref": "feature/fork",
        "repo_path": ".",
    }
    if extra:
        config_data.update(extra)
    config_file = path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))
    return config_file


class TestCLIGroup:
    def test_cli_help(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_cli_run_help(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output

    def test_cli_resume_help(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["resume", "--help"])
        assert result.exit_code == 0

    def test_cli_validate_help(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--help"])
        assert result.exit_code == 0

    def test_cli_report_help(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["report", "--help"])
        assert result.exit_code == 0


class TestRunCommand:
    def test_run_command_missing_config_fails(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--config", "/nonexistent/config.yaml"])
        assert result.exit_code != 0

    def test_run_command_invalid_config_exits_1(self, tmp_path):
        from src.cli.main import cli

        config_file = tmp_path / "bad.yaml"
        config_file.write_text("not: valid: yaml: [")
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--config", str(config_file)])
        assert result.exit_code == 1

    def test_run_command_config_missing_required_fields(self, tmp_path):
        from src.cli.main import cli

        config_file = tmp_path / "incomplete.yaml"
        config_file.write_text("repo_path: .")
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--config", str(config_file)])
        assert result.exit_code == 1

    def test_run_command_completed_status(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.COMPLETED)

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert result.exit_code == 0
        assert "completed" in result.output.lower()

    def test_run_command_awaiting_human_status(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.AWAITING_HUMAN)

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert result.exit_code == 0
        assert "human" in result.output.lower()
        assert final_state.run_id in result.output

    def test_run_command_failed_status_exits_1(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.FAILED)
        final_state.errors = [{"message": "Something went wrong"}]

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert result.exit_code == 1

    def test_run_command_other_status(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.PAUSED)

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert result.exit_code == 0
        assert "paused" in result.output.lower()

    def test_run_command_dry_run_flag(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.COMPLETED)

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(
                cli, ["run", "--config", str(config_file), "--dry-run"]
            )

        assert result.exit_code == 0
        assert "dry run" in result.output.lower()

    def test_run_command_prints_upstream_and_fork(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.COMPLETED)

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert "upstream/main" in result.output
        assert "feature/fork" in result.output

    def test_run_command_prints_run_id(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        fixed_state = _make_state(SystemStatus.COMPLETED)

        runner = CliRunner()
        with patch("src.cli.commands.run.MergeState", return_value=fixed_state):
            with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=fixed_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert fixed_state.run_id in result.output

    def test_run_command_failed_prints_errors(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)
        final_state = _make_state(SystemStatus.FAILED)
        final_state.errors = [{"message": "Disk full"}, {"message": "Git error"}]

        runner = CliRunner()
        with patch("src.cli.commands.run.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run = AsyncMock(return_value=final_state)
            mock_orch_cls.return_value = mock_orch

            result = runner.invoke(cli, ["run", "--config", str(config_file)])

        assert "Disk full" in result.output or "Git error" in result.output


class TestResumeCommand:
    def test_resume_requires_run_id_or_checkpoint(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["resume"])
        assert result.exit_code == 1
        assert "required" in result.output.lower()

    def test_resume_with_nonexistent_checkpoint_fails(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["resume", "--checkpoint", "/nonexistent/path.json"]
        )
        assert result.exit_code != 0

    def test_resume_with_run_id_no_checkpoint_found(self):
        from src.cli.main import cli

        runner = CliRunner()

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = None
            mock_cp_cls.return_value = mock_cp

            result = runner.invoke(cli, ["resume", "--run-id", "nonexistent-id"])

        assert result.exit_code == 1
        assert "No checkpoint" in result.output

    def test_resume_with_run_id_completed_state(self):
        from src.cli.main import cli

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.COMPLETED)

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            result = runner.invoke(cli, ["resume", "--run-id", existing_state.run_id])

        assert result.exit_code == 0
        assert "terminal" in result.output.lower()

    def test_resume_with_run_id_failed_state(self):
        from src.cli.main import cli

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.FAILED)

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            result = runner.invoke(cli, ["resume", "--run-id", existing_state.run_id])

        assert result.exit_code == 0
        assert "terminal" in result.output.lower()

    def test_resume_continues_awaiting_human_to_completed(self):
        from src.cli.main import cli

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.AWAITING_HUMAN)
        final_state = _make_state(SystemStatus.COMPLETED)

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.commands.resume.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=final_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(
                    cli, ["resume", "--run-id", existing_state.run_id]
                )

        assert result.exit_code == 0
        assert "completed" in result.output.lower()

    def test_resume_with_run_id_awaiting_human_prints_pending(self):
        from src.cli.main import cli
        from src.models.human import HumanDecisionRequest
        from src.models.decision import MergeDecision

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.AWAITING_HUMAN)
        final_state = _make_state(SystemStatus.AWAITING_HUMAN)
        req = HumanDecisionRequest(
            file_path="src/auth.py",
            priority=5,
            conflict_points=[],
            context_summary="Test",
            upstream_change_summary="X",
            fork_change_summary="Y",
            analyst_recommendation=MergeDecision.TAKE_TARGET,
            analyst_confidence=0.8,
            analyst_rationale="OK",
            options=[],
            created_at=datetime.now(),
            human_decision=None,
        )
        final_state.human_decision_requests["src/auth.py"] = req

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.commands.resume.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=final_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(
                    cli, ["resume", "--run-id", existing_state.run_id]
                )

        assert "pending" in result.output.lower()

    def test_resume_with_run_id_failed_exits_1(self):
        from src.cli.main import cli

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.AWAITING_HUMAN)
        final_state = _make_state(SystemStatus.FAILED)
        final_state.errors = [{"message": "Merge conflict unresolvable"}]

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.commands.resume.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=final_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(
                    cli, ["resume", "--run-id", existing_state.run_id]
                )

        assert result.exit_code == 1

    def test_resume_with_checkpoint_file(self, tmp_path):
        from src.cli.main import cli

        runner = CliRunner()

        checkpoint_file = tmp_path / "checkpoint.json"
        checkpoint_file.write_text('{"run_id": "test"}')

        existing_state = _make_state(SystemStatus.AWAITING_HUMAN)
        final_state = _make_state(SystemStatus.COMPLETED)

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.commands.resume.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=final_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(
                    cli, ["resume", "--checkpoint", str(checkpoint_file)]
                )

        assert result.exit_code == 0

    def test_resume_with_run_id_other_status(self):
        from src.cli.main import cli

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.AWAITING_HUMAN)
        final_state = _make_state(SystemStatus.PAUSED)

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.commands.resume.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=final_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(
                    cli, ["resume", "--run-id", existing_state.run_id]
                )

        assert result.exit_code == 0
        assert "paused" in result.output.lower()

    def test_resume_prints_current_state_run_id(self):
        from src.cli.main import cli

        runner = CliRunner()

        existing_state = _make_state(SystemStatus.AWAITING_HUMAN)
        final_state = _make_state(SystemStatus.COMPLETED)

        with patch("src.cli.commands.resume.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = existing_state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.commands.resume.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.run = AsyncMock(return_value=final_state)
                mock_orch_cls.return_value = mock_orch

                result = runner.invoke(
                    cli, ["resume", "--run-id", existing_state.run_id]
                )

        assert existing_state.run_id in result.output


class TestValidateCommand:
    def test_validate_missing_config_fails(self):
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(
            cli, ["validate", "--config", "/nonexistent/config.yaml"]
        )
        assert result.exit_code != 0

    def test_validate_invalid_yaml_exits_1(self, tmp_path):
        from src.cli.main import cli

        config_file = tmp_path / "bad.yaml"
        config_file.write_text("not: valid: [yaml")
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--config", str(config_file)])
        assert result.exit_code == 1

    def test_validate_valid_config_with_all_env_vars(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)

        runner = CliRunner()
        env = {
            "ANTHROPIC_API_KEY": "fake-anthropic-key",
            "OPENAI_API_KEY": "fake-openai-key",
        }
        with patch("src.cli.main.validate_config_and_env", return_value=[]):
            result = runner.invoke(
                cli, ["validate", "--config", str(config_file)], env=env
            )

        assert "valid" in result.output.lower()
        assert result.exit_code == 0

    def test_validate_missing_env_vars_exits_1(self, tmp_path):
        from src.cli.main import cli

        config_file = _write_config_file(tmp_path)

        runner = CliRunner()
        with patch(
            "src.cli.main.validate_config_and_env",
            return_value=[
                "Agent 'planner' requires env var 'ANTHROPIC_API_KEY' (not set)"
            ],
        ):
            result = runner.invoke(cli, ["validate", "--config", str(config_file)])

        assert result.exit_code == 1
        assert "ANTHROPIC_API_KEY" in result.output


class TestReportCommand:
    def test_report_no_checkpoint_found_exits_1(self, tmp_path):
        from src.cli.main import cli

        runner = CliRunner()

        with patch("src.cli.main.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = None
            mock_cp_cls.return_value = mock_cp

            result = runner.invoke(
                cli, ["report", "--run-id", "nonexistent-id", "--output", str(tmp_path)]
            )

        assert result.exit_code == 1
        assert "No checkpoint" in result.output

    def test_report_generates_reports(self, tmp_path):
        from src.cli.main import cli

        runner = CliRunner()

        state = _make_state(SystemStatus.COMPLETED)
        fake_json = tmp_path / "report.json"
        fake_json.write_text("{}")
        fake_md = tmp_path / "report.md"
        fake_md.write_text("# Report")

        with patch("src.cli.main.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = state
            mock_cp_cls.return_value = mock_cp

            with patch(
                "src.cli.main.write_json_report", return_value=fake_json
            ) as mock_json:
                with patch(
                    "src.cli.main.write_markdown_report", return_value=fake_md
                ) as mock_md:
                    result = runner.invoke(
                        cli,
                        ["report", "--run-id", state.run_id, "--output", str(tmp_path)],
                    )

        assert result.exit_code == 0
        mock_json.assert_called_once()
        mock_md.assert_called_once()

    def test_report_handles_write_json_error(self, tmp_path):
        from src.cli.main import cli

        runner = CliRunner()

        state = _make_state(SystemStatus.COMPLETED)
        fake_md = tmp_path / "report.md"
        fake_md.write_text("# Report")

        with patch("src.cli.main.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = state
            mock_cp_cls.return_value = mock_cp

            with patch(
                "src.cli.main.write_json_report",
                side_effect=Exception("JSON write failed"),
            ):
                with patch("src.cli.main.write_markdown_report", return_value=fake_md):
                    result = runner.invoke(
                        cli,
                        ["report", "--run-id", state.run_id, "--output", str(tmp_path)],
                    )

        assert result.exit_code == 0
        assert "JSON report failed" in result.output

    def test_report_handles_write_markdown_error(self, tmp_path):
        from src.cli.main import cli

        runner = CliRunner()

        state = _make_state(SystemStatus.COMPLETED)
        fake_json = tmp_path / "report.json"
        fake_json.write_text("{}")

        with patch("src.cli.main.Checkpoint") as mock_cp_cls:
            mock_cp = MagicMock()
            mock_cp.get_latest.return_value = Path("/fake/checkpoint.json")
            mock_cp.load.return_value = state
            mock_cp_cls.return_value = mock_cp

            with patch("src.cli.main.write_json_report", return_value=fake_json):
                with patch(
                    "src.cli.main.write_markdown_report",
                    side_effect=Exception("MD write failed"),
                ):
                    result = runner.invoke(
                        cli,
                        ["report", "--run-id", state.run_id, "--output", str(tmp_path)],
                    )

        assert result.exit_code == 0
        assert "Markdown report failed" in result.output


class TestValidateConfigAndEnv:
    def test_returns_errors_for_missing_env_vars(self, tmp_path):
        from src.cli.main import validate_config_and_env

        config = _make_config()

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.tools.git_tool.GitTool"):
                errors = validate_config_and_env(config)

        assert any("ANTHROPIC_API_KEY" in e for e in errors)

    def test_returns_empty_when_all_vars_set(self, tmp_path):
        from src.cli.main import validate_config_and_env

        config = _make_config()

        env = {
            "ANTHROPIC_API_KEY": "fake-anthropic",
            "OPENAI_API_KEY": "fake-openai",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("src.tools.git_tool.GitTool") as mock_gt_cls:
                mock_gt = MagicMock()
                mock_gt.repo.git.rev_parse.return_value = "abc123"
                mock_gt_cls.return_value = mock_gt

                errors = validate_config_and_env(config)

        assert errors == []

    def test_returns_error_for_invalid_repo(self):
        from src.cli.main import validate_config_and_env

        config = _make_config()

        env = {
            "ANTHROPIC_API_KEY": "fake-anthropic",
            "OPENAI_API_KEY": "fake-openai",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch(
                "src.tools.git_tool.GitTool",
                side_effect=ValueError("Not a valid git repository"),
            ):
                errors = validate_config_and_env(config)

        assert any("not a valid git repository" in e.lower() for e in errors)

    def test_returns_error_for_invalid_git_refs(self):
        from src.cli.main import validate_config_and_env

        config = _make_config()

        env = {
            "ANTHROPIC_API_KEY": "fake-anthropic",
            "OPENAI_API_KEY": "fake-openai",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("src.tools.git_tool.GitTool") as mock_gt_cls:
                mock_gt = MagicMock()
                mock_gt.repo.git.rev_parse.side_effect = Exception("bad ref")
                mock_gt_cls.return_value = mock_gt

                errors = validate_config_and_env(config)

        assert any("does not exist" in e for e in errors)
