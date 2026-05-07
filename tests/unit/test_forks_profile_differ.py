"""Unit tests for `forks_profile_differ.diff_profile_vs_heuristic`."""

from __future__ import annotations

from src.models.forks_profile import (
    ForksProfile,
    ForkOnlyFeature,
    RemovedDomain,
    RewriteMergePolicy,
    RewrittenModule,
)
from src.tools.forks_profile_differ import (
    diff_profile_vs_heuristic,
    format_profile_diff,
)
from src.tools.forks_profile_drafter import (
    DraftedForkOnlyFeature,
    DraftedProfile,
    DraftedRemovedDomain,
    DraftedRewrittenModule,
)


def _drafted(
    *,
    removed: tuple[DraftedRemovedDomain, ...] = (),
    rewritten: tuple[DraftedRewrittenModule, ...] = (),
    fork_only: tuple[DraftedForkOnlyFeature, ...] = (),
) -> DraftedProfile:
    return DraftedProfile(
        upstream_ref="upstream/main",
        fork_ref="HEAD",
        merge_base="abc1234",
        fork_only_features=fork_only,
        removed_domains=removed,
        rewritten_modules=rewritten,
        migration_policy=None,
        stats={
            "D_MISSING": 0,
            "D_EXTRA": 0,
            "B-rewritten": 0,
            "migration-collisions": 0,
        },
    )


class TestUnmatchedDeclarations:
    def test_removed_domain_no_longer_detected(self) -> None:
        profile = ForksProfile(
            removed_domains=[
                RemovedDomain(name="smtp", paths=["svc/mail/**"]),
            ]
        )
        diff = diff_profile_vs_heuristic(profile, _drafted())
        assert len(diff.unmatched_declarations) == 1
        e = diff.unmatched_declarations[0]
        assert e.category == "removed_domain"
        assert e.identifier == "smtp"
        assert "no FORK_DELETED files" in e.rationale

    def test_rewritten_module_no_longer_detected(self) -> None:
        profile = ForksProfile(
            rewritten_modules=[
                RewrittenModule(
                    path="svc/auth/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                ),
            ]
        )
        diff = diff_profile_vs_heuristic(profile, _drafted())
        assert len(diff.unmatched_declarations) == 1
        assert diff.unmatched_declarations[0].category == "rewritten_module"

    def test_fork_only_feature_no_longer_detected(self) -> None:
        profile = ForksProfile(
            fork_only_features=[ForkOnlyFeature(path="pkg/extra/**")]
        )
        diff = diff_profile_vs_heuristic(profile, _drafted())
        assert len(diff.unmatched_declarations) == 1
        assert diff.unmatched_declarations[0].category == "fork_only_feature"


class TestUnmatchedHeuristics:
    def test_new_removed_domain_candidate(self) -> None:
        drafted = _drafted(
            removed=(
                DraftedRemovedDomain(
                    name="payments",
                    paths=("svc/payments/**",),
                    reason="TODO",
                    removed_in="abc1234",
                ),
            ),
        )
        diff = diff_profile_vs_heuristic(ForksProfile(), drafted)
        assert len(diff.unmatched_heuristics) == 1
        e = diff.unmatched_heuristics[0]
        assert e.category == "removed_domain"
        assert "payments" in e.identifier

    def test_new_rewritten_module_candidate(self) -> None:
        drafted = _drafted(
            rewritten=(
                DraftedRewrittenModule(
                    path="backend/services/notifications/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="retention=18%, lines_changed=320",
                ),
            ),
        )
        diff = diff_profile_vs_heuristic(ForksProfile(), drafted)
        assert len(diff.unmatched_heuristics) == 1
        e = diff.unmatched_heuristics[0]
        assert e.category == "rewritten_module"
        assert "notifications" in e.identifier
        assert "retention=18%" in e.rationale

    def test_already_declared_glob_not_reported(self) -> None:
        profile = ForksProfile(
            rewritten_modules=[
                RewrittenModule(
                    path="backend/services/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                ),
            ]
        )
        drafted = _drafted(
            rewritten=(
                DraftedRewrittenModule(
                    path="backend/services/notifications/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="retention=18%",
                ),
            ),
        )
        diff = diff_profile_vs_heuristic(profile, drafted)
        assert diff.unmatched_heuristics == ()

    def test_new_fork_only_feature_candidate(self) -> None:
        drafted = _drafted(
            fork_only=(DraftedForkOnlyFeature(path="pkg/visualizer/**", note=""),),
        )
        diff = diff_profile_vs_heuristic(ForksProfile(), drafted)
        assert len(diff.unmatched_heuristics) == 1
        assert diff.unmatched_heuristics[0].category == "fork_only_feature"


class TestClassificationMismatch:
    def test_yaml_downgraded_but_heuristic_says_escalate(self) -> None:
        profile = ForksProfile(
            rewritten_modules=[
                RewrittenModule(
                    path="svc/auth/**",
                    policy=RewriteMergePolicy.SEMANTIC_MERGE_WITH_ALERT,
                ),
            ]
        )
        drafted = _drafted(
            rewritten=(
                DraftedRewrittenModule(
                    path="svc/auth/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="retention=12%",
                ),
            ),
        )
        diff = diff_profile_vs_heuristic(profile, drafted)
        assert diff.unmatched_declarations == ()
        assert diff.unmatched_heuristics == ()
        assert len(diff.classification_mismatches) == 1
        m = diff.classification_mismatches[0]
        assert "semantic_merge_with_alert" in m.rationale
        assert "retention=12%" in m.rationale

    def test_matching_escalate_policy_no_mismatch(self) -> None:
        profile = ForksProfile(
            rewritten_modules=[
                RewrittenModule(
                    path="svc/auth/**", policy=RewriteMergePolicy.ESCALATE_HUMAN
                ),
            ]
        )
        drafted = _drafted(
            rewritten=(
                DraftedRewrittenModule(
                    path="svc/auth/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="retention=12%",
                ),
            ),
        )
        diff = diff_profile_vs_heuristic(profile, drafted)
        assert diff.is_empty()


class TestFormatAndEdges:
    def test_empty_diff_renders_agreement_line(self) -> None:
        text = format_profile_diff(
            diff_profile_vs_heuristic(ForksProfile(), _drafted())
        )
        assert "agree" in text

    def test_format_includes_three_section_headers_when_each_class_present(
        self,
    ) -> None:
        profile = ForksProfile(
            removed_domains=[RemovedDomain(name="gone", paths=["x/**"])],
            rewritten_modules=[
                RewrittenModule(
                    path="svc/m/**",
                    policy=RewriteMergePolicy.SEMANTIC_MERGE_WITH_ALERT,
                ),
            ],
        )
        drafted = _drafted(
            rewritten=(
                DraftedRewrittenModule(
                    path="svc/m/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="retention=10%",
                ),
                DraftedRewrittenModule(
                    path="svc/new/**",
                    policy=RewriteMergePolicy.ESCALATE_HUMAN,
                    note="retention=8%",
                ),
            ),
        )
        diff = diff_profile_vs_heuristic(profile, drafted)
        text = format_profile_diff(diff)
        assert "📋" in text
        assert "➕" in text
        assert "🔄" in text

    def test_none_profile_treated_as_empty(self) -> None:
        drafted = _drafted(
            fork_only=(DraftedForkOnlyFeature(path="pkg/x/**", note=""),)
        )
        diff = diff_profile_vs_heuristic(None, drafted)
        assert len(diff.unmatched_heuristics) == 1
        assert diff.unmatched_declarations == ()
