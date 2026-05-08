"""Unit tests for ForksProfile schema + loader.

Synthetic fixtures only — no project-specific paths or names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.cli.paths import get_forks_profile_path
from src.models.forks_profile import (
    DEPRECATED_YAML_FIELDS,
    ForksProfile,
    ForksProfileYaml,
    MigrationCollisionAction,
    RewriteMergePolicy,
)
from src.tools.forks_profile_loader import (
    ForksProfileError,
    find_removed_domain_match,
    find_rewritten_module_match,
    load_forks_profile,
    summarize_for_log,
)


def _write_profile(repo_root: Path, body: str) -> Path:
    merge_dir = repo_root / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    profile_path = merge_dir / "forks-profile.yaml"
    profile_path.write_text(body, encoding="utf-8")
    return profile_path


class TestSchema:
    def test_minimal_valid_profile_loads(self):
        profile = ForksProfile.model_validate({"version": 1})
        assert profile.version == 1
        assert profile.removed_domains == []
        assert profile.rewritten_modules == []
        assert profile.fork_only_features == []
        assert profile.migration_policy is None
        assert profile.is_empty()

    def test_full_profile_round_trip(self):
        data = {
            "version": 1,
            "fork": {"name": "demo-fork", "upstream": "owner/repo"},
            "removed_domains": [
                {
                    "name": "alpha",
                    "paths": ["svc/alpha/**", "tests/alpha_*.py"],
                    "reason": "out of scope",
                    "removed_in": "abc1234",
                }
            ],
            "rewritten_modules": [
                {
                    "path": "svc/auth/**",
                    "policy": "escalate_human",
                    "note": "custom SSO",
                    "examples": ["svc/auth/login.py"],
                },
                {
                    "path": "pkg/registry.json",
                    "policy": "take_current_with_diff_note",
                },
            ],
            "fork_only_features": [
                {"path": "svc/extras/**", "note": "fork-only addon"}
            ],
            "migration_policy": {
                "fork_owns_numbers_above": 100,
                "upstream_take_target_max": 99,
                "on_collision": {"action": "escalate_human", "note": "manual"},
            },
        }
        profile = ForksProfile.model_validate(data)
        assert not profile.is_empty()
        assert profile.fork.name == "demo-fork"
        assert len(profile.removed_domains) == 1
        assert profile.removed_domains[0].paths == [
            "svc/alpha/**",
            "tests/alpha_*.py",
        ]
        assert profile.rewritten_modules[0].policy == RewriteMergePolicy.ESCALATE_HUMAN
        assert (
            profile.rewritten_modules[1].policy
            == RewriteMergePolicy.TAKE_CURRENT_WITH_DIFF_NOTE
        )
        assert profile.migration_policy is not None
        assert profile.migration_policy.fork_owns_numbers_above == 100
        assert (
            profile.migration_policy.on_collision is not None
            and profile.migration_policy.on_collision.action
            == MigrationCollisionAction.ESCALATE_HUMAN
        )

    def test_empty_paths_globs_are_stripped(self):
        domain = ForksProfile.model_validate(
            {"removed_domains": [{"name": "x", "paths": [" ", "", "a/**", "  b/**  "]}]}
        ).removed_domains[0]
        assert domain.paths == ["a/**", "b/**"]

    def test_unknown_top_level_key_rejected(self):
        with pytest.raises(Exception):
            ForksProfile.model_validate({"version": 1, "unknown_key": 1})

    def test_invalid_policy_rejected(self):
        with pytest.raises(Exception):
            ForksProfile.model_validate(
                {"rewritten_modules": [{"path": "x/**", "policy": "totally_invalid"}]}
            )

    def test_fork_identity_accepts_extra_fields(self):
        profile = ForksProfile.model_validate(
            {
                "fork": {
                    "name": "demo",
                    "registry": "private-registry",
                    "distribution": "private-channel",
                }
            }
        )
        assert profile.fork.name == "demo"


class TestForksProfileYamlSchema:
    def test_minimal_yaml_loads(self):
        yp = ForksProfileYaml.model_validate({"version": 1})
        assert yp.version == 1
        assert yp.removed_domains == []
        assert yp.rewritten_modules == []

    def test_yaml_rejects_fork_only_features(self):
        with pytest.raises(Exception):
            ForksProfileYaml.model_validate(
                {"fork_only_features": [{"path": "pkg/extra/**"}]}
            )

    def test_yaml_rejects_migration_policy(self):
        with pytest.raises(Exception):
            ForksProfileYaml.model_validate(
                {
                    "migration_policy": {
                        "path_globs": ["db/*.sql"],
                        "fork_owns_numbers_above": 10,
                    }
                }
            )

    def test_deprecated_fields_constant_exposes_both(self):
        assert "fork_only_features" in DEPRECATED_YAML_FIELDS
        assert "migration_policy" in DEPRECATED_YAML_FIELDS


class TestLoader:
    def test_missing_file_returns_none(self, tmp_path: Path):
        assert load_forks_profile(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path):
        _write_profile(tmp_path, "")
        assert load_forks_profile(tmp_path) is None

    def test_yaml_only_comments_returns_none(self, tmp_path: Path):
        _write_profile(tmp_path, "# only comments\n")
        assert load_forks_profile(tmp_path) is None

    def test_loads_minimal_profile(self, tmp_path: Path):
        _write_profile(tmp_path, "version: 1\n")
        profile = load_forks_profile(tmp_path)
        assert profile is not None
        assert profile.is_empty()

    def test_invalid_yaml_raises(self, tmp_path: Path):
        _write_profile(tmp_path, "version: 1\n  bad: indent: ::")
        with pytest.raises(ForksProfileError):
            load_forks_profile(tmp_path)

    def test_root_must_be_mapping(self, tmp_path: Path):
        _write_profile(tmp_path, "- 1\n- 2\n")
        with pytest.raises(ForksProfileError):
            load_forks_profile(tmp_path)

    def test_schema_violation_raises(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            "rewritten_modules:\n  - path: x/**\n    policy: not_a_policy\n",
        )
        with pytest.raises(ForksProfileError):
            load_forks_profile(tmp_path)

    def test_path_resolution_uses_get_forks_profile_path(self, tmp_path: Path):
        path = get_forks_profile_path(str(tmp_path))
        assert path == tmp_path / ".merge" / "forks-profile.yaml"

    def test_deprecated_fork_only_features_rejected_with_migration_message(
        self, tmp_path: Path
    ):
        _write_profile(
            tmp_path,
            ('version: 1\nfork_only_features:\n  - path: "pkg/legacy/**"\n'),
        )
        with pytest.raises(ForksProfileError) as exc:
            load_forks_profile(tmp_path)
        msg = str(exc.value)
        assert "fork_only_features" in msg
        assert "auto-computed" in msg

    def test_deprecated_migration_policy_rejected_with_migration_message(
        self, tmp_path: Path
    ):
        _write_profile(
            tmp_path,
            (
                "migration_policy:\n"
                '  path_globs: ["db/*.sql"]\n'
                "  fork_owns_numbers_above: 10\n"
            ),
        )
        with pytest.raises(ForksProfileError) as exc:
            load_forks_profile(tmp_path)
        msg = str(exc.value)
        assert "migration_policy" in msg
        assert "auto-computed" in msg

    def test_loader_returns_runtime_profile_with_empty_auto_fields(
        self, tmp_path: Path
    ):
        _write_profile(
            tmp_path,
            ('removed_domains:\n  - name: alpha\n    paths: ["svc/alpha/**"]\n'),
        )
        profile = load_forks_profile(tmp_path)
        assert profile is not None
        assert len(profile.removed_domains) == 1
        # auto-computed fields stay empty until initialize phase fills them
        assert profile.fork_only_features == []
        assert profile.migration_policy is None


class TestMatchHelpers:
    def _profile(self) -> ForksProfile:
        return ForksProfile.model_validate(
            {
                "removed_domains": [
                    {"name": "alpha", "paths": ["svc/alpha/**"]},
                    {
                        "name": "beta",
                        "paths": ["pkg/beta/**", "tests/beta_*.py"],
                    },
                ],
                "rewritten_modules": [
                    {"path": "svc/auth/**", "policy": "escalate_human"},
                    {
                        "path": "pkg/registry.json",
                        "policy": "take_current_with_diff_note",
                    },
                ],
            }
        )

    def test_removed_domain_glob_match(self):
        profile = self._profile()
        m = find_removed_domain_match(profile, "svc/alpha/login.py")
        assert m is not None and m.name == "alpha"

    def test_removed_domain_basename_glob(self):
        profile = self._profile()
        m = find_removed_domain_match(profile, "tests/beta_smoke.py")
        assert m is not None and m.name == "beta"

    def test_removed_domain_no_match_returns_none(self):
        profile = self._profile()
        assert find_removed_domain_match(profile, "svc/gamma/foo.py") is None

    def test_rewritten_module_glob_match(self):
        profile = self._profile()
        m = find_rewritten_module_match(profile, "svc/auth/handler.py")
        assert m is not None
        assert m.policy == RewriteMergePolicy.ESCALATE_HUMAN

    def test_rewritten_module_exact_path_match(self):
        profile = self._profile()
        m = find_rewritten_module_match(profile, "pkg/registry.json")
        assert m is not None
        assert m.policy == RewriteMergePolicy.TAKE_CURRENT_WITH_DIFF_NOTE

    def test_rewritten_module_no_match(self):
        profile = self._profile()
        assert find_rewritten_module_match(profile, "svc/alpha/login.py") is None

    def test_first_match_wins_in_iteration_order(self):
        profile = ForksProfile.model_validate(
            {
                "removed_domains": [
                    {"name": "early", "paths": ["foo/**"]},
                    {"name": "late", "paths": ["foo/**"]},
                ]
            }
        )
        m = find_removed_domain_match(profile, "foo/bar.py")
        assert m is not None and m.name == "early"


class TestSummary:
    def test_summary_includes_counts(self):
        profile = ForksProfile.model_validate(
            {
                "fork": {"name": "demo"},
                "removed_domains": [{"name": "a", "paths": ["x/**"]}],
                "rewritten_modules": [{"path": "y/**", "policy": "escalate_human"}],
            }
        )
        summary = summarize_for_log(profile)
        assert "demo" in summary
        assert "removed_domains=1" in summary
        assert "rewritten_modules=1" in summary
        assert "migration_policy=no" in summary

    def test_summary_with_unnamed_fork(self):
        profile = ForksProfile.model_validate({})
        summary = summarize_for_log(profile)
        assert "<unnamed>" in summary
