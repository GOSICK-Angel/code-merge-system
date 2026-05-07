"""CLI tests for `merge forks-profile validate` / `schema`.

Synthetic yaml fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from src.cli.main import cli


def _write_profile(repo_root: Path, body: str) -> Path:
    merge_dir = repo_root / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    path = merge_dir / "forks-profile.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestValidate:
    def test_missing_file_exits_with_status_2(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["forks-profile", "validate", "--repo", str(tmp_path)]
        )
        assert result.exit_code == 2
        assert "No forks-profile.yaml found" in " ".join(result.output.split())

    def test_valid_minimal_profile_exits_zero(self, tmp_path: Path):
        _write_profile(tmp_path, "version: 1\n")
        runner = CliRunner()
        result = runner.invoke(
            cli, ["forks-profile", "validate", "--repo", str(tmp_path)]
        )
        assert result.exit_code == 0

    def test_valid_full_profile_exits_zero_and_summarises(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            (
                "fork:\n"
                "  name: demo-fork\n"
                "removed_domains:\n"
                "  - name: alpha\n"
                '    paths: ["svc/alpha/**"]\n'
                "rewritten_modules:\n"
                '  - path: "svc/auth/**"\n'
                "    policy: escalate_human\n"
            ),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli, ["forks-profile", "validate", "--repo", str(tmp_path)]
        )
        assert result.exit_code == 0
        # Rich wraps lines to terminal width — collapse whitespace before asserting.
        flat = " ".join(result.output.split())
        assert "is a valid forks-profile" in flat
        assert "removed_domains=1" in flat
        assert "rewritten_modules=1" in flat

    def test_invalid_policy_value_exits_one(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            ('rewritten_modules:\n  - path: "x/**"\n    policy: not_a_real_policy\n'),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli, ["forks-profile", "validate", "--repo", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "Validation failed" in " ".join(result.output.split())

    def test_yaml_syntax_error_exits_one(self, tmp_path: Path):
        _write_profile(tmp_path, "version: 1\n  bad: indent: ::")
        runner = CliRunner()
        result = runner.invoke(
            cli, ["forks-profile", "validate", "--repo", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "Validation failed" in " ".join(result.output.split())

    def test_explicit_path_overrides_default(self, tmp_path: Path):
        custom = tmp_path / "custom-profile.yaml"
        custom.write_text("fork:\n  name: explicit\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            cli, ["forks-profile", "validate", "--path", str(custom)]
        )
        assert result.exit_code == 0
        # Rich may wrap the path across lines — match basename only.
        assert "custom-profile.yaml" in " ".join(result.output.split())


class TestSchema:
    def test_schema_to_stdout_is_valid_json(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["forks-profile", "schema"])
        assert result.exit_code == 0
        schema = json.loads(result.output)
        assert schema["title"] == "ForksProfile"
        props = schema.get("properties", {})
        for key in (
            "version",
            "fork",
            "removed_domains",
            "rewritten_modules",
            "fork_only_features",
            "migration_policy",
        ):
            assert key in props, f"missing top-level property: {key}"

    def test_schema_writes_to_output_file(self, tmp_path: Path):
        out = tmp_path / "forks-profile.schema.json"
        runner = CliRunner()
        result = runner.invoke(cli, ["forks-profile", "schema", "--output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        schema = json.loads(out.read_text(encoding="utf-8"))
        assert schema["title"] == "ForksProfile"
        assert "$defs" in schema

    def test_schema_includes_rewrite_policy_enum(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["forks-profile", "schema"])
        assert "semantic_merge_with_alert" in result.output
        assert "escalate_human" in result.output
        assert "take_current_with_diff_note" in result.output
