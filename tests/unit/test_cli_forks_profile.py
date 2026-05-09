"""CLI tests for `merge forks-profile validate` / `schema` / `init` / `diff`.

Synthetic yaml fixtures plus a tiny real git repo for `init` and `diff`
so the GitTool path is exercised end-to-end.
"""

from __future__ import annotations

import json
import subprocess
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
        # Only user-authored keys are exposed as schema properties.
        for key in (
            "version",
            "fork",
            "removed_domains",
            "rewritten_modules",
        ):
            assert key in props, f"missing top-level property: {key}"
        # fork_only_features and migration_policy are auto-computed at
        # runtime; they must not appear as authorable yaml properties.
        assert "fork_only_features" not in props
        assert "migration_policy" not in props

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


def _git(repo: Path, *args: str) -> str:
    """Wrapper that fails loudly so test debugging is easier."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _init_git_repo_with_divergence(repo: Path) -> tuple[str, str, str]:
    """Build a tiny repo with one upstream branch and one fork branch.

    Layout planted at the merge-base:
      - kept.py                   (unchanged in fork; survives)
      - svc/payments/api.py       (deleted in fork → FORK_DELETED)
      - svc/payments/billing.py   (deleted in fork → FORK_DELETED)
      - svc/payments/refunds.py   (deleted in fork → FORK_DELETED)
      - svc/auth/login.py         (heavily rewritten in fork → FORK_MODIFIED)

    Fork-only additions:
      - pkg/dashboard/widget1.py  (FORK_ONLY)
      - pkg/dashboard/widget2.py  (FORK_ONLY)
      - pkg/dashboard/widget3.py  (FORK_ONLY)
      - db/migrations/100_fork.sql (FORK_ONLY migration)

    Returns ``(merge_base_sha, upstream_ref, fork_ref)``.
    """
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "Test")

    (repo / "kept.py").write_text("print('shared')\n", encoding="utf-8")
    (repo / "svc" / "payments").mkdir(parents=True)
    (repo / "svc" / "payments" / "api.py").write_text("api\n", encoding="utf-8")
    (repo / "svc" / "payments" / "billing.py").write_text("billing\n", encoding="utf-8")
    (repo / "svc" / "payments" / "refunds.py").write_text("refunds\n", encoding="utf-8")
    (repo / "svc" / "auth").mkdir(parents=True)
    auth_baseline = "\n".join(f"line_{i}" for i in range(300)) + "\n"
    (repo / "svc" / "auth" / "login.py").write_text(auth_baseline, encoding="utf-8")
    (repo / "db" / "migrations").mkdir(parents=True)
    (repo / "db" / "migrations" / "001_init.sql").write_text(
        "-- 001\n", encoding="utf-8"
    )
    (repo / "db" / "migrations" / "002_users.sql").write_text(
        "-- 002\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "merge-base")
    base = _git(repo, "rev-parse", "HEAD").strip()

    # upstream branch — single new file, leaves shared layout intact
    _git(repo, "checkout", "-q", "-b", "upstream-main")
    (repo / "upstream_only.py").write_text("u\n", encoding="utf-8")
    _git(repo, "add", "upstream_only.py")
    _git(repo, "commit", "-q", "-m", "upstream extends")

    # fork branch — drop payments, rewrite auth, add dashboard + migration
    _git(repo, "checkout", "-q", "-b", "fork-main", base)
    _git(repo, "rm", "-rq", "svc/payments")
    _git(repo, "commit", "-q", "-m", "remove billing layer")

    rewritten = "\n".join(f"NEW_{i}" for i in range(300)) + "\n"
    (repo / "svc" / "auth" / "login.py").write_text(rewritten, encoding="utf-8")
    _git(repo, "add", "svc/auth/login.py")
    _git(repo, "commit", "-q", "-m", "rewrite auth login")

    (repo / "pkg" / "dashboard").mkdir(parents=True)
    (repo / "pkg" / "dashboard" / "widget1.py").write_text("w1\n", encoding="utf-8")
    (repo / "pkg" / "dashboard" / "widget2.py").write_text("w2\n", encoding="utf-8")
    (repo / "pkg" / "dashboard" / "widget3.py").write_text("w3\n", encoding="utf-8")
    (repo / "db" / "migrations" / "100_fork.sql").write_text(
        "-- 100\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fork-only features + migration")

    return base, "upstream-main", "fork-main"


class TestInit:
    def test_stdout_yaml_is_loadable_and_carries_findings(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "--rewrite-min-fork-commits",
                "1",
                "--migration-glob",
                "**/migrations/*.sql",
                # Explicit stdout sentinel — without it the command now
                # writes to <repo>/.merge/forks-profile.yaml by default.
                "-o",
                "-",
            ],
        )
        assert result.exit_code == 0, result.output
        # stdout must be valid yaml the validator accepts
        path = tmp_path / "drafted.yaml"
        path.write_text(result.output, encoding="utf-8")
        validate = runner.invoke(
            cli, ["forks-profile", "validate", "--path", str(path)]
        )
        assert validate.exit_code == 0, validate.output

        text = result.output
        assert "removed_domains:" in text
        assert "svc/payments/**" in text
        # fork_only_features and migration_policy are surfaced as
        # informational comments only — they're not user-authored.
        assert "# fork_only_features (auto-computed at runtime)" in text
        assert "pkg/dashboard/**" in text
        # rewrite heuristic catches auth/login.py either via low retention
        # or fork-only commit count
        assert "svc/auth" in text
        # migration policy emitted because fork holds 100 vs upstream max 2
        assert "# migration_policy (auto-computed at runtime)" in text
        assert "upstream_take_target_max=2" in text

    def test_output_to_existing_file_refuses(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        out = tmp_path / "exists.yaml"
        out.write_text("placeholder\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 2
        assert out.read_text(encoding="utf-8") == "placeholder\n"

    def test_default_refs_come_from_config_yaml(self, tmp_path: Path):
        # Init must default --upstream/--fork to MergeConfig.upstream_ref
        # / fork_ref so the drafter's base computation aligns with what
        # `merge <target>` actually uses; otherwise (issue surfaced
        # 2026-05-08) base drift makes upstream-only-added files look
        # fork-deleted in the rendered yaml.
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "config.yaml").write_text(
            f"upstream_ref: {upstream}\nfork_ref: {fork}\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "--rewrite-min-fork-commits",
                "1",
                "--migration-glob",
                "**/migrations/*.sql",
                "-o",
                "-",
            ],
        )
        assert result.exit_code == 0, result.output
        # Header line is `# Inputs: <upstream>..<fork> (merge-base ...)`
        assert f"# Inputs: {upstream}..{fork}" in result.output

    def test_explicit_flags_override_config(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        (merge_dir / "config.yaml").write_text(
            "upstream_ref: should-be-ignored\nfork_ref: should-be-ignored\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "-o",
                "-",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"# Inputs: {upstream}..{fork}" in result.output

    def test_default_output_path_writes_to_repo_merge_dir(self, tmp_path: Path):
        # No -o: the draft must land at <repo>/.merge/forks-profile.yaml
        # so the runtime loader picks it up without further user action.
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "--rewrite-min-fork-commits",
                "1",
                "--migration-glob",
                "**/migrations/*.sql",
            ],
        )
        assert result.exit_code == 0, result.output
        out_path = tmp_path / ".merge" / "forks-profile.yaml"
        assert out_path.exists(), result.output
        content = out_path.read_text(encoding="utf-8")
        assert "version: 1" in content
        assert "removed_domains:" in content

    def test_default_output_path_refuses_when_present(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        # Pre-create the canonical path; init must refuse to overwrite.
        existing = tmp_path / ".merge" / "forks-profile.yaml"
        existing.parent.mkdir(parents=True)
        existing.write_text("placeholder\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
            ],
        )
        assert result.exit_code == 2
        assert existing.read_text(encoding="utf-8") == "placeholder\n"

    def test_output_to_new_file_writes(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        out = tmp_path / "drafted.yaml"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "version: 1" in out.read_text(encoding="utf-8")


class TestDiff:
    def test_missing_profile_exits_two(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "diff",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
            ],
        )
        assert result.exit_code == 2

    def test_perfect_match_after_init_reports_agreement(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        out = merge_dir / "forks-profile.yaml"
        runner = CliRunner()
        init_result = runner.invoke(
            cli,
            [
                "forks-profile",
                "init",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "--rewrite-min-fork-commits",
                "1",
                "--migration-glob",
                "**/migrations/*.sql",
                "-o",
                str(out),
            ],
        )
        assert init_result.exit_code == 0, init_result.output

        diff_result = runner.invoke(
            cli,
            [
                "forks-profile",
                "diff",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "--rewrite-min-fork-commits",
                "1",
                "--migration-glob",
                "**/migrations/*.sql",
            ],
        )
        assert diff_result.exit_code == 0, diff_result.output
        assert "agree" in diff_result.output

    def test_exit_non_zero_on_diff_flag(self, tmp_path: Path):
        base, upstream, fork = _init_git_repo_with_divergence(tmp_path)
        merge_dir = tmp_path / ".merge"
        merge_dir.mkdir()
        # An empty profile so heuristic findings show up as drift.
        (merge_dir / "forks-profile.yaml").write_text("version: 1\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "forks-profile",
                "diff",
                "--repo",
                str(tmp_path),
                "--upstream",
                upstream,
                "--fork",
                fork,
                "--merge-base",
                base,
                "--cluster-min-files",
                "3",
                "--rewrite-min-fork-commits",
                "1",
                "--exit-non-zero-on-diff",
            ],
        )
        assert result.exit_code == 1
        assert "➕" in result.output
