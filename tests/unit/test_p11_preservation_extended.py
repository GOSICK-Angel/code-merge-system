"""#11 tests: strengthened fork-preservation auditing.

Extends the P1-1 byte-equality gate with:
- auditing ``original_file_paths`` (native-3way-drained files);
- a configurable ``preservation_min_fork_lines`` forced to 0 for
  security-sensitive files;
- a line-level partial-drop check for C-class files whose worktree is NOT
  byte-equal to upstream (the wholesale-drop check cannot see these).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.models.config import MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState
from src.tools.preservation_auditor import (
    audit_fork_preservation,
    fork_distinctive_lines,
    fork_survival_shortfall,
)


class _FakeGit:
    def __init__(
        self,
        repo_path: Path,
        ref_blobs: dict[tuple[str, str], str],
        worktree_blobs: dict[str, str],
        ref_contents: dict[tuple[str, str], str] | None = None,
    ):
        self.repo_path = repo_path
        self._ref_blobs = ref_blobs
        self._worktree_blobs = worktree_blobs
        self._ref_contents = ref_contents or {}

    def get_file_hash(self, ref: str, file_path: str) -> str | None:
        return self._ref_blobs.get((ref, file_path))

    def get_worktree_blob_sha(self, file_path: str) -> str | None:
        return self._worktree_blobs.get(file_path)

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        return self._ref_contents.get((ref, file_path))


def _fd(
    file_path: str,
    *,
    fork_added: int = 0,
    fork_deleted: int = 0,
    security: bool = False,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        change_category=FileChangeCategory.C,
        lines_added=fork_added,
        lines_deleted=fork_deleted,
        is_security_sensitive=security,
    )


def _decision(file_path: str) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.9,
        rationale="test",
        timestamp=datetime.now(),
    )


def _state(
    file_diffs: list[FileDiff],
    *,
    file_paths: list[str],
    original_file_paths: list[str] | None = None,
    decisions: dict[str, FileDecisionRecord] | None = None,
) -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    state = MergeState(config=config)
    state.merge_base_commit = "base-sha"
    batch = PhaseFileBatch(
        batch_id=str(uuid4()),
        phase=MergePhase.AUTO_MERGE,
        file_paths=list(file_paths),
        risk_level=RiskLevel.AUTO_SAFE,
        change_category=FileChangeCategory.C,
    )
    if original_file_paths is not None:
        batch.original_file_paths = list(original_file_paths)
    state.merge_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="base-sha",
        phases=[batch],
        risk_summary=RiskSummary(
            total_files=len(file_diffs),
            auto_safe_count=len(file_diffs),
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="test",
    )
    state.file_diffs = file_diffs
    state.file_decision_records = decisions or {}
    return state


class TestAuditsOriginalFilePaths:
    def test_native_3way_drained_file_still_audited(self, tmp_path: Path) -> None:
        # file_paths is drained (native-3way already applied) but the file
        # remains in original_file_paths and must still be audited.
        fp = "src/customizer.py"
        state = _state(
            [_fd(fp, fork_added=80, fork_deleted=20)],
            file_paths=[],
            original_file_paths=[fp],
            decisions={fp: _decision(fp)},
        )
        git = _FakeGit(
            repo_path=tmp_path,
            ref_blobs={("upstream/main", fp): "u"},
            worktree_blobs={fp: "u"},  # worktree == upstream → wholesale drop
        )
        losses = audit_fork_preservation(state, git)
        assert [loss.file_path for loss in losses] == [fp]


class TestSecuritySensitiveZeroesThreshold:
    def test_small_delta_security_file_is_audited(self, tmp_path: Path) -> None:
        fp = "src/auth/token.py"
        state = _state(
            [_fd(fp, fork_added=2, fork_deleted=2, security=True)],  # 4 < 50
            file_paths=[fp],
            decisions={fp: _decision(fp)},
        )
        git = _FakeGit(
            repo_path=tmp_path,
            ref_blobs={("upstream/main", fp): "u"},
            worktree_blobs={fp: "u"},
        )
        losses = audit_fork_preservation(state, git)
        assert [loss.file_path for loss in losses] == [fp]

    def test_small_delta_non_security_file_is_skipped(self, tmp_path: Path) -> None:
        fp = "src/util.py"
        state = _state(
            [_fd(fp, fork_added=2, fork_deleted=2, security=False)],
            file_paths=[fp],
            decisions={fp: _decision(fp)},
        )
        git = _FakeGit(
            repo_path=tmp_path,
            ref_blobs={("upstream/main", fp): "u"},
            worktree_blobs={fp: "u"},
        )
        assert audit_fork_preservation(state, git) == []


class TestLineLevelPartialDrop:
    _BASE = "def common():\n    return 1\n"
    _FORK = (
        "def common():\n    return 1\n"
        "def fork_feature_alpha():\n    return 'alpha_value_here'\n"
        "def fork_feature_beta():\n    return 'beta_value_here'\n"
        "def fork_feature_gamma():\n    return 'gamma_value_here'\n"
        "FORK_CONSTANT_ONE = 'distinctive_one'\n"
        "FORK_CONSTANT_TWO = 'distinctive_two'\n"
    )
    _UPSTREAM = "def common():\n    return 2\n"

    def _setup(self, tmp_path: Path, merged: str) -> tuple[MergeState, _FakeGit]:
        fp = "src/feature.py"
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / fp).write_text(merged, encoding="utf-8")
        state = _state(
            [_fd(fp, fork_added=60, fork_deleted=0)],
            file_paths=[fp],
            decisions={fp: _decision(fp)},
        )
        git = _FakeGit(
            repo_path=tmp_path,
            ref_blobs={("upstream/main", fp): "u"},
            worktree_blobs={fp: "merged-differs"},  # != upstream → line-level path
            ref_contents={
                ("base-sha", fp): self._BASE,
                ("feature/fork", fp): self._FORK,
                ("upstream/main", fp): self._UPSTREAM,
            },
        )
        return state, git

    def test_partial_drop_is_flagged(self, tmp_path: Path) -> None:
        # merged adopts upstream and keeps NONE of the fork's distinctive lines.
        state, git = self._setup(tmp_path, merged=self._UPSTREAM)
        losses = audit_fork_preservation(state, git)
        assert len(losses) == 1
        assert "fork-distinctive lines absent" in losses[0].reason

    def test_fork_lines_surviving_is_not_flagged(self, tmp_path: Path) -> None:
        # merged keeps all the fork's distinctive lines → no loss.
        state, git = self._setup(tmp_path, merged=self._FORK + "def added_by_up():\n")
        assert audit_fork_preservation(state, git) == []


class TestPureLineHelpers:
    def test_distinctive_excludes_base_and_upstream_and_trivia(self) -> None:
        base = "shared = 1\n"
        fork = "shared = 1\nfork_only_symbol = 'xyz'\n}\n   \nupstream_thing = 2\n"
        upstream = "shared = 1\nupstream_thing = 2\n"
        distinctive = fork_distinctive_lines(base, fork, upstream)
        assert "fork_only_symbol = 'xyz'" in distinctive
        assert "shared = 1" not in distinctive  # in base
        assert "upstream_thing = 2" not in distinctive  # in upstream
        assert "}" not in distinctive  # trivial punctuation filtered

    def test_shortfall_returns_zero_below_min_distinctive(self) -> None:
        # fewer than _MIN_DISTINCTIVE_LINES distinctive lines → unjudgeable.
        base, fork, up = "a=1\n", "a=1\nfork_line_one = 1\n", "a=1\n"
        assert fork_survival_shortfall(base, fork, up, "a=1\n") == (0, 0)
