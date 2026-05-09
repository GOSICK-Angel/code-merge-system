"""Unit tests for the pure heuristic layer of `forks_profile_drafter`.

Synthetic divergence maps and retention info — no GitTool required.
"""

from __future__ import annotations

import yaml as _yaml
from pydantic import ValidationError

from src.models.diff import ForkDivergence
from src.models.forks_profile import RewriteMergePolicy
from src.tools.forks_profile_drafter import (
    DraftedForkOnlyFeature,
    DraftedMigrationPolicy,
    DraftedProfile,
    DraftedRemovedDomain,
    DraftedRewrittenModule,
    RetentionInfo,
    _suppress_removed_overlapping_rewritten,
    cluster_paths,
    draft_fork_only_features,
    draft_migration_policy,
    draft_removed_domains,
    draft_rewritten_modules,
    extract_owner_repo,
    render_profile_yaml,
)


class TestClusterPaths:
    def test_empty_input(self) -> None:
        assert cluster_paths([]) == ()

    def test_small_set_clusters_at_min_three(self) -> None:
        paths = [f"a/b/c/file_{i}.py" for i in range(10)]
        out = cluster_paths(paths)
        assert len(out) == 1
        assert out[0].glob == "a/b/c/**"
        assert out[0].count == 10

    def test_large_set_uses_adaptive_threshold(self) -> None:
        paths: list[str] = []
        for outer in range(6):
            for inner in range(50):
                paths.append(f"a/b/c/d{outer}/leaf_{inner}.py")
        out = cluster_paths(paths)
        globs = sorted(c.glob for c in out)
        assert globs == [f"a/b/c/d{n}/**" for n in range(6)]
        for c in out:
            assert c.count == 50

    def test_orphans_emitted_with_full_path(self) -> None:
        paths = ["a.py", "x/y/z.py", "x/y/q.py"]
        out = cluster_paths(paths)
        assert {c.glob for c in out} == {"a.py", "x/y/z.py", "x/y/q.py"}
        for c in out:
            assert c.count == 1

    def test_explicit_min_files_override(self) -> None:
        paths = ["a/b/x.py", "a/b/y.py"]
        out = cluster_paths(paths, min_files=2)
        assert len(out) == 1
        assert out[0].glob == "a/b/**"

    def test_mixed_depth_keeps_orphans(self) -> None:
        paths = ["loose.py"] + [f"deep/dir/file_{i}.py" for i in range(5)]
        out = cluster_paths(paths, min_files=3)
        globs = sorted(c.glob for c in out)
        assert "deep/dir/**" in globs
        assert "loose.py" in globs

    def test_min_depth_default_suppresses_top_level_rollup(self) -> None:
        # All five files live directly under ``tools/`` — at the legacy
        # ``min_depth=1`` setting the algorithm would emit ``tools/**``,
        # but the new default (``min_depth=2``) keeps them as orphans
        # so the maintainer doesn't accidentally drop the entire tree.
        paths = [f"tools/file_{i}.py" for i in range(5)]
        out = cluster_paths(paths, min_files=3)
        globs = sorted(c.glob for c in out)
        assert "tools/**" not in globs
        assert globs == sorted(paths)

    def test_min_depth_one_restores_legacy_rollup(self) -> None:
        paths = [f"tools/file_{i}.py" for i in range(5)]
        out = cluster_paths(paths, min_files=3, min_depth=1)
        assert len(out) == 1
        assert out[0].glob == "tools/**"

    def test_min_depth_two_still_clusters_at_subtree(self) -> None:
        paths = [f"tools/comfyui/file_{i}.py" for i in range(5)]
        out = cluster_paths(paths, min_files=3)
        assert len(out) == 1
        assert out[0].glob == "tools/comfyui/**"


class TestDraftForkOnlyFeatures:
    def test_picks_only_fork_only(self) -> None:
        divergence = {
            "a/b/c/x1.py": ForkDivergence.FORK_ONLY,
            "a/b/c/x2.py": ForkDivergence.FORK_ONLY,
            "a/b/c/x3.py": ForkDivergence.FORK_ONLY,
            "other.py": ForkDivergence.UPSTREAM_ONLY_CHANGE,
            "deleted.py": ForkDivergence.FORK_DELETED,
        }
        out = draft_fork_only_features(divergence)
        assert len(out) == 1
        assert out[0].path == "a/b/c/**"
        assert out[0].note == ""

    def test_no_fork_only_returns_empty(self) -> None:
        divergence = {"a.py": ForkDivergence.UNCHANGED}
        assert draft_fork_only_features(divergence) == ()


class TestDraftRemovedDomains:
    def test_uses_delete_commit_lookup(self) -> None:
        divergence = {
            f"svc/payments/file{i}.py": ForkDivergence.FORK_DELETED for i in range(4)
        }
        lookup_calls: list[str] = []

        def lookup(path: str) -> tuple[str, str] | None:
            lookup_calls.append(path)
            return ("abcdef1234", "remove billing layer")

        kept, filtered = draft_removed_domains(divergence, delete_commit_lookup=lookup)
        assert len(kept) == 1
        domain = kept[0]
        assert domain.name == "payments"
        assert domain.paths == ("svc/payments/**",)
        assert domain.removed_in == "abcdef1234"
        assert "remove billing layer" in domain.reason
        assert "abcdef1" in domain.reason
        assert lookup_calls == ["svc/payments/file0.py"]
        assert filtered == ()

    def test_no_lookup_callable(self) -> None:
        divergence = {f"x/y/f{i}.py": ForkDivergence.FORK_DELETED for i in range(3)}
        kept, filtered = draft_removed_domains(divergence, delete_commit_lookup=None)
        assert len(kept) == 1
        assert kept[0].removed_in == ""
        # Empty when no commit evidence — drafter no longer emits a
        # "TODO" sentinel that would leak into LLM prompts.
        assert kept[0].reason == ""
        # No lookup → cannot determine evidence → no filtering.
        assert filtered == ()

    def test_no_deleted_returns_empty(self) -> None:
        divergence = {"a.py": ForkDivergence.FORK_ONLY}
        assert draft_removed_domains(divergence) == ((), ())

    def test_duplicate_domain_names_get_disambiguated(self) -> None:
        # Two distinct cluster subtrees share the basename ``tools``;
        # uniquification walks up the path so the yaml is unambiguous.
        divergence = {
            **{f"tools/a/b/f{i}.py": ForkDivergence.FORK_DELETED for i in range(4)},
            **{
                f"tools/comfyui/tools/f{i}.py": ForkDivergence.FORK_DELETED
                for i in range(4)
            },
        }
        kept, _ = draft_removed_domains(divergence, delete_commit_lookup=None)
        names = sorted(d.name for d in kept)
        # The two clusters cannot both be ``tools``.
        assert len(names) == len(set(names))

    def test_clusters_without_evidence_are_filtered(self) -> None:
        # Models a real dify-plugins case: tree-diff says fork is missing
        # this file, but no fork commit ever deleted it (it's an
        # upstream-added file the fork hasn't pulled). With evidence
        # required (default), the cluster is dropped from the kept set
        # and surfaced via the filtered list instead.
        divergence = {f"x/y/file{i}.py": ForkDivergence.FORK_DELETED for i in range(4)}

        def lookup(_path: str) -> tuple[str, str] | None:
            return None

        kept, filtered = draft_removed_domains(divergence, delete_commit_lookup=lookup)
        assert kept == ()
        assert filtered == ("x/y/**",)

    def test_partial_evidence_keeps_cluster(self) -> None:
        # Single member with a delete commit is enough to count as
        # genuine fork-policy evidence.
        divergence = {f"x/y/file{i}.py": ForkDivergence.FORK_DELETED for i in range(4)}

        def lookup(path: str) -> tuple[str, str] | None:
            return (
                ("abcdef1", "intentional drop") if path.endswith("file0.py") else None
            )

        kept, filtered = draft_removed_domains(divergence, delete_commit_lookup=lookup)
        assert len(kept) == 1
        assert kept[0].removed_in == "abcdef1"
        assert filtered == ()

    def test_evidence_filter_can_be_disabled(self) -> None:
        # CLI escape hatch: keep all clusters even without evidence.
        divergence = {f"x/y/file{i}.py": ForkDivergence.FORK_DELETED for i in range(4)}

        def lookup(_path: str) -> tuple[str, str] | None:
            return None

        kept, filtered = draft_removed_domains(
            divergence,
            delete_commit_lookup=lookup,
            require_commit_evidence=False,
        )
        assert len(kept) == 1
        assert filtered == ()


class TestDraftRewrittenModules:
    def test_low_retention_with_enough_lines_qualifies(self) -> None:
        retention = [
            RetentionInfo(
                path="svc/auth/handler.py",
                lines_at_base=200,
                lines_changed=180,
                retention=0.10,
                fork_only_commits=2,
            )
        ]
        out = draft_rewritten_modules(retention)
        assert len(out) == 1
        assert out[0].path == "svc/auth/handler.py"
        assert out[0].policy == RewriteMergePolicy.ESCALATE_HUMAN
        assert "10%" in out[0].note

    def test_above_threshold_filtered(self) -> None:
        retention = [
            RetentionInfo(
                path="svc/util/small.py",
                lines_at_base=200,
                lines_changed=180,
                retention=0.50,
                fork_only_commits=1,
            )
        ]
        assert draft_rewritten_modules(retention) == ()

    def test_low_retention_but_few_lines_filtered(self) -> None:
        retention = [
            RetentionInfo(
                path="x.py",
                lines_at_base=20,
                lines_changed=10,
                retention=0.10,
                fork_only_commits=1,
            )
        ]
        assert draft_rewritten_modules(retention) == ()

    def test_many_commits_alone_qualifies(self) -> None:
        retention = [
            RetentionInfo(
                path="svc/active/area.py",
                lines_at_base=500,
                lines_changed=20,
                retention=0.95,
                fork_only_commits=8,
            )
        ]
        out = draft_rewritten_modules(retention)
        assert len(out) == 1
        assert out[0].path == "svc/active/area.py"
        # High retention triggered only by commit churn → light policy
        # so AI flow still runs (with stash) instead of forcing human.
        assert out[0].policy == RewriteMergePolicy.TAKE_CURRENT_WITH_DIFF_NOTE

    def test_many_commits_with_zero_diff_filtered(self) -> None:
        # Real dify case: ``.gitignore`` was touched by >=5 fork commits
        # but never actually modified (retention=100%, lines_changed=0).
        # It must NOT be flagged as rewritten — there's nothing to rewrite,
        # so the file should flow through the default AI path instead of
        # being short-circuited to ``take_current_with_diff_note``.
        retention = [
            RetentionInfo(
                path=".gitignore",
                lines_at_base=42,
                lines_changed=0,
                retention=1.0,
                fork_only_commits=8,
            )
        ]
        assert draft_rewritten_modules(retention) == ()

    def test_partial_retention_picks_semantic_merge_alert(self) -> None:
        retention = [
            RetentionInfo(
                path="svc/auth/handler.py",
                lines_at_base=200,
                lines_changed=120,
                retention=0.55,
                fork_only_commits=6,
            )
        ]
        out = draft_rewritten_modules(retention)
        assert len(out) == 1
        assert out[0].policy == RewriteMergePolicy.SEMANTIC_MERGE_WITH_ALERT

    def test_clusters_multiple_qualifying_files(self) -> None:
        retention = [
            RetentionInfo(
                path=f"svc/auth/file_{i}.py",
                lines_at_base=200,
                lines_changed=180,
                retention=0.10,
                fork_only_commits=1,
            )
            for i in range(5)
        ]
        out = draft_rewritten_modules(retention, cluster_min_files=3)
        assert len(out) == 1
        assert out[0].path == "svc/auth/**"
        assert "5 file(s)" in out[0].note


class TestSuppressRemovedOverlappingRewritten:
    def test_drops_removed_when_rewritten_covers_same_subtree(self) -> None:
        removed = (
            DraftedRemovedDomain(
                name="models",
                paths=("models/**",),
                reason="",
                removed_in="",
            ),
        )
        rewritten = (
            DraftedRewrittenModule(
                path="models/azure_openai/**",
                policy=RewriteMergePolicy.ESCALATE_HUMAN,
                note="",
            ),
        )
        out = _suppress_removed_overlapping_rewritten(removed, rewritten)
        assert out == ()

    def test_keeps_removed_when_rewritten_is_disjoint(self) -> None:
        removed = (
            DraftedRemovedDomain(
                name="bug-template",
                paths=(".github/ISSUE_TEMPLATE/bug_report.yml",),
                reason="",
                removed_in="",
            ),
        )
        rewritten = (
            DraftedRewrittenModule(
                path="models/azure_openai/**",
                policy=RewriteMergePolicy.ESCALATE_HUMAN,
                note="",
            ),
        )
        out = _suppress_removed_overlapping_rewritten(removed, rewritten)
        assert out == removed


class TestExtractOwnerRepo:
    def test_ssh_remote(self) -> None:
        assert (
            extract_owner_repo("git@github.com:cvte/dify-plugins.git")
            == "cvte/dify-plugins"
        )

    def test_https_remote_with_dot_git(self) -> None:
        assert (
            extract_owner_repo("https://github.com/cvte/dify-plugins.git")
            == "cvte/dify-plugins"
        )

    def test_https_remote_no_suffix(self) -> None:
        assert (
            extract_owner_repo("https://github.com/cvte/dify-plugins")
            == "cvte/dify-plugins"
        )

    def test_empty_string_returns_none(self) -> None:
        assert extract_owner_repo("") is None

    def test_unrecognized_url_returns_none(self) -> None:
        assert extract_owner_repo("not a url") is None


class TestDraftMigrationPolicy:
    def test_emits_when_fork_max_above_upstream_max(self) -> None:
        policy = draft_migration_policy(
            base_files=[
                "db/migrations/001_init.sql",
                "db/migrations/002_users.sql",
            ],
            fork_files=[
                "db/migrations/001_init.sql",
                "db/migrations/002_users.sql",
                "db/migrations/100_fork_table.sql",
                "db/migrations/101_fork_index.sql",
            ],
            fork_only_files=[
                "db/migrations/100_fork_table.sql",
                "db/migrations/101_fork_index.sql",
            ],
            path_globs=["**/migrations/*.sql"],
        )
        assert policy is not None
        assert policy.upstream_take_target_max == 2
        assert policy.fork_owns_numbers_above == 2
        assert policy.path_globs == ("**/migrations/*.sql",)
        assert policy.on_collision == "escalate_human"

    def test_returns_none_when_fork_max_equal(self) -> None:
        policy = draft_migration_policy(
            base_files=["db/m/001.sql", "db/m/050.sql"],
            fork_files=["db/m/001.sql", "db/m/050.sql"],
            fork_only_files=[],
            path_globs=["**/m/*.sql"],
        )
        assert policy is None

    def test_returns_none_with_no_fork_only(self) -> None:
        policy = draft_migration_policy(
            base_files=[],
            fork_files=[],
            fork_only_files=[],
            path_globs=["**/m/*.sql"],
        )
        assert policy is None

    def test_returns_none_when_globs_empty(self) -> None:
        policy = draft_migration_policy(
            base_files=["a/m/1.sql"],
            fork_files=["a/m/1.sql", "a/m/2.sql"],
            fork_only_files=["a/m/2.sql"],
            path_globs=[],
        )
        assert policy is None


def _empty_drafted(**overrides) -> DraftedProfile:
    defaults = dict(
        upstream_ref="upstream/main",
        fork_ref="HEAD",
        merge_base="abc1234deadbeef",
        fork_only_features=(),
        removed_domains=(),
        rewritten_modules=(),
        migration_policy=None,
        stats={
            "D_MISSING": 0,
            "D_EXTRA": 0,
            "B-rewritten": 0,
            "migration-collisions": 0,
        },
    )
    defaults.update(overrides)
    return DraftedProfile(**defaults)  # type: ignore[arg-type]


class TestRenderProfileYaml:
    def test_empty_draft_renders_with_todos_and_lists(self) -> None:
        text = render_profile_yaml(_empty_drafted(), today="2026-05-07")
        assert "Auto-drafted by `merge forks-profile init` on 2026-05-07" in text
        assert "version: 1" in text
        assert "removed_domains: []" in text
        assert "rewritten_modules: []" in text
        assert "# TODO: name your fork" in text
        assert "auto-computed" in text
        # Deprecated yaml sections must NOT appear as authorable keys.
        assert "fork_only_features:" not in text
        assert "migration_policy:" not in text

    def test_drafted_yaml_is_loadable_via_user_schema(self) -> None:
        from src.models.forks_profile import ForksProfileYaml

        drafted = _empty_drafted(
            removed_domains=(
                DraftedRemovedDomain(
                    name="payments",
                    paths=("svc/payments/**",),
                    reason="TODO: why?",
                    removed_in="abc1234",
                ),
            ),
            rewritten_modules=(
                DraftedRewrittenModule(
                    path="svc/auth/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="fork retains 12% of merge-base lines",
                ),
            ),
            fork_only_features=(
                DraftedForkOnlyFeature(path="pkg/dashboard/**", note=""),
            ),
            migration_policy=DraftedMigrationPolicy(
                path_globs=("db/migrations/*.sql",),
                fork_owns_numbers_above=25,
                upstream_take_target_max=25,
                on_collision="escalate_human",
            ),
            stats={
                "D_MISSING": 4,
                "D_EXTRA": 1,
                "B-rewritten": 1,
                "migration-collisions": 0,
            },
        )
        text = render_profile_yaml(drafted, today="2026-05-07")
        data = _yaml.safe_load(text)
        try:
            yaml_profile = ForksProfileYaml.model_validate(data)
        except ValidationError as e:
            raise AssertionError(
                f"rendered yaml failed user-schema validation: {e}\n---\n{text}"
            ) from e
        assert len(yaml_profile.removed_domains) == 1
        assert yaml_profile.removed_domains[0].name == "payments"
        assert len(yaml_profile.rewritten_modules) == 1
        assert (
            yaml_profile.rewritten_modules[0].policy
            == RewriteMergePolicy.ESCALATE_HUMAN
        )
        # Auto-computed sections appear only as informational comments.
        assert "# fork_only_features (auto-computed at runtime)" in text
        assert "pkg/dashboard/**" in text
        assert "# migration_policy (auto-computed at runtime)" in text
        assert "upstream_take_target_max=25" in text
