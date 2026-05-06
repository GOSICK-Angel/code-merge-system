"""P1-1 tests: fork preservation gate.

Even after P0-1 closes the C/AUTO_SAFE silent ``TAKE_TARGET`` shortcut, fork
content can still vanish through other paths — a SEMANTIC_MERGE LLM output
that happens to byte-equal upstream, a future regression that re-introduces
silent ``TAKE_TARGET`` for some category, etc. The preservation auditor
catches the symptom (worktree blob == upstream blob for a C-class file with
non-trivial fork-side delta) regardless of which executor branch caused it.

Contract:
- Iterate every C-class file declared in ``state.merge_plan``.
- Skip files whose ``FileDecisionRecord`` already escalated to human.
- Skip files with no material fork-side delta (``fork_lines_changed`` below
  the threshold ``min_fork_lines``).
- For the rest, compare worktree blob sha against upstream blob sha. If they
  match, fork's distinctive lines have been silently dropped — record a
  ``PreservationLoss`` so the orchestrator can re-route to ConflictAnalyst
  or escalate to human.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.models.config import MergeConfig
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState
from src.tools.preservation_auditor import (
    PreservationLoss,
    audit_fork_preservation,
)


class _FakeGit:
    """Minimal GitTool stand-in: only the two methods the auditor calls."""

    def __init__(
        self,
        repo_path: Path | None = None,
        ref_blobs: dict[tuple[str, str], str] | None = None,
        worktree_blobs: dict[str, str] | None = None,
    ):
        self.repo_path = repo_path or Path("/tmp")
        self._ref_blobs = ref_blobs or {}
        self._worktree_blobs = worktree_blobs or {}

    def get_file_hash(self, ref: str, file_path: str) -> str | None:
        return self._ref_blobs.get((ref, file_path))

    def get_worktree_blob_sha(self, file_path: str) -> str | None:
        return self._worktree_blobs.get(file_path)


def _make_state(
    *,
    plan_files: dict[str, FileChangeCategory],
    file_diffs: list[FileDiff],
    decisions: dict[str, FileDecisionRecord] | None = None,
) -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    state = MergeState(config=config)
    state.merge_base_commit = "base-sha"

    by_category: dict[FileChangeCategory, list[str]] = {}
    for fp, cat in plan_files.items():
        by_category.setdefault(cat, []).append(fp)

    batches: list[PhaseFileBatch] = []
    for cat, paths in by_category.items():
        batches.append(
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MergePhase.AUTO_MERGE,
                file_paths=paths,
                risk_level=RiskLevel.AUTO_SAFE,
                change_category=cat,
            )
        )

    state.merge_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="base-sha",
        phases=batches,
        risk_summary=RiskSummary(
            total_files=len(plan_files),
            auto_safe_count=len(plan_files),
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="test",
    )
    state.file_categories = dict(plan_files)
    state.file_diffs = file_diffs
    state.file_decision_records = decisions or {}
    return state


def _fd(
    file_path: str,
    *,
    fork_added: int = 0,
    fork_deleted: int = 0,
    category: FileChangeCategory = FileChangeCategory.C,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        change_category=category,
        lines_added=fork_added,
        lines_deleted=fork_deleted,
    )


def _decision(
    file_path: str,
    decision: MergeDecision = MergeDecision.SEMANTIC_MERGE,
) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=decision,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.9,
        rationale="test",
        timestamp=datetime.now(),
    )


class TestFlagsLossWhenWorktreeEqualsUpstream:
    def test_c_class_with_large_fork_delta_and_worktree_eq_upstream(self) -> None:
        state = _make_state(
            plan_files={"src/customizer.py": FileChangeCategory.C},
            file_diffs=[_fd("src/customizer.py", fork_added=80, fork_deleted=20)],
            decisions={"src/customizer.py": _decision("src/customizer.py")},
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/customizer.py"): "upstream-sha"},
            worktree_blobs={"src/customizer.py": "upstream-sha"},
        )

        losses = audit_fork_preservation(state, git)

        assert len(losses) == 1
        loss = losses[0]
        assert isinstance(loss, PreservationLoss)
        assert loss.file_path == "src/customizer.py"
        assert loss.fork_lines_changed == 100
        assert loss.decision == MergeDecision.SEMANTIC_MERGE


class TestSkipsBelowThreshold:
    def test_small_fork_delta_does_not_trigger(self) -> None:
        state = _make_state(
            plan_files={"src/tiny.py": FileChangeCategory.C},
            file_diffs=[_fd("src/tiny.py", fork_added=5, fork_deleted=5)],
            decisions={"src/tiny.py": _decision("src/tiny.py")},
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/tiny.py"): "upstream-sha"},
            worktree_blobs={"src/tiny.py": "upstream-sha"},
        )
        assert audit_fork_preservation(state, git) == []


class TestSkipsWhenWorktreeDiffersFromUpstream:
    def test_worktree_not_equal_upstream_means_some_merge_happened(self) -> None:
        state = _make_state(
            plan_files={"src/merged.py": FileChangeCategory.C},
            file_diffs=[_fd("src/merged.py", fork_added=80, fork_deleted=20)],
            decisions={"src/merged.py": _decision("src/merged.py")},
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/merged.py"): "upstream-sha"},
            worktree_blobs={"src/merged.py": "merged-sha"},
        )
        assert audit_fork_preservation(state, git) == []


class TestSkipsHumanEscalated:
    def test_already_escalated_files_are_not_audited(self) -> None:
        state = _make_state(
            plan_files={"src/escalated.py": FileChangeCategory.C},
            file_diffs=[_fd("src/escalated.py", fork_added=80, fork_deleted=20)],
            decisions={
                "src/escalated.py": _decision(
                    "src/escalated.py", MergeDecision.ESCALATE_HUMAN
                )
            },
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/escalated.py"): "upstream-sha"},
            worktree_blobs={"src/escalated.py": "upstream-sha"},
        )
        assert audit_fork_preservation(state, git) == []


class TestSkipsNonCClass:
    def test_b_class_does_not_run_through_preservation_audit(self) -> None:
        state = _make_state(
            plan_files={"src/upstream_only.py": FileChangeCategory.B},
            file_diffs=[
                _fd(
                    "src/upstream_only.py",
                    fork_added=0,
                    fork_deleted=0,
                    category=FileChangeCategory.B,
                )
            ],
            decisions={"src/upstream_only.py": _decision("src/upstream_only.py")},
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/upstream_only.py"): "upstream-sha"},
            worktree_blobs={"src/upstream_only.py": "upstream-sha"},
        )
        assert audit_fork_preservation(state, git) == []


class TestEdgeCases:
    def test_no_plan_returns_empty(self) -> None:
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        assert audit_fork_preservation(state, _FakeGit()) == []

    def test_missing_upstream_blob_does_not_flag(self) -> None:
        state = _make_state(
            plan_files={"src/fork_only.py": FileChangeCategory.C},
            file_diffs=[_fd("src/fork_only.py", fork_added=80, fork_deleted=20)],
            decisions={"src/fork_only.py": _decision("src/fork_only.py")},
        )
        git = _FakeGit(
            ref_blobs={},
            worktree_blobs={"src/fork_only.py": "some-sha"},
        )
        assert audit_fork_preservation(state, git) == []

    def test_multiple_files_all_lost(self) -> None:
        state = _make_state(
            plan_files={f"src/file_{i}.py": FileChangeCategory.C for i in range(3)},
            file_diffs=[
                _fd(f"src/file_{i}.py", fork_added=60, fork_deleted=10)
                for i in range(3)
            ],
            decisions={
                f"src/file_{i}.py": _decision(f"src/file_{i}.py") for i in range(3)
            },
        )
        git = _FakeGit(
            ref_blobs={
                ("upstream/main", f"src/file_{i}.py"): f"up-sha-{i}" for i in range(3)
            },
            worktree_blobs={f"src/file_{i}.py": f"up-sha-{i}" for i in range(3)},
        )
        losses = audit_fork_preservation(state, git)
        assert len(losses) == 3
        assert {loss.file_path for loss in losses} == {
            f"src/file_{i}.py" for i in range(3)
        }

    def test_threshold_override(self) -> None:
        state = _make_state(
            plan_files={"src/tiny.py": FileChangeCategory.C},
            file_diffs=[_fd("src/tiny.py", fork_added=8, fork_deleted=4)],
            decisions={"src/tiny.py": _decision("src/tiny.py")},
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/tiny.py"): "upstream-sha"},
            worktree_blobs={"src/tiny.py": "upstream-sha"},
        )
        assert audit_fork_preservation(state, git) == []
        losses = audit_fork_preservation(state, git, min_fork_lines=10)
        assert len(losses) == 1


class TestReasonField:
    def test_reason_mentions_worktree_upstream_equality(self) -> None:
        state = _make_state(
            plan_files={"src/customizer.py": FileChangeCategory.C},
            file_diffs=[_fd("src/customizer.py", fork_added=70, fork_deleted=30)],
            decisions={"src/customizer.py": _decision("src/customizer.py")},
        )
        git = _FakeGit(
            ref_blobs={("upstream/main", "src/customizer.py"): "upstream-sha"},
            worktree_blobs={"src/customizer.py": "upstream-sha"},
        )
        losses = audit_fork_preservation(state, git)
        reason = losses[0].reason.lower()
        assert "fork" in reason
        assert "100" in reason
        assert "upstream" in reason
