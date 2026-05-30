"""P1 (Wave 4): "silent gate-skip" alarm.

Deterministic safety gates that read git content degrade-to-skip on a read
failure. Before P1 that was silent — a systemically broken ``git_tool`` could
disable a whole class of gates while the run still reported a clean COMPLETED.
P1 records each such skip into ``state.errors`` (phase ``gate_skip``) so the CI
summary flips to ``partial_failure`` and the interactive/resume terminal prints
"completed WITH WARNINGS".

These tests pin the *producing* side (each instrumented site records a skip on a
genuine read failure, and does NOT record on a healthy read) and the *persisting*
side (judge_review writes the Judge's payload skips into ``state.errors``, deduped).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.models.config import AgentLLMConfig, MergeConfig
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState
from src.tools.gate_skip import GATE_SKIP_PHASE, gate_skip_entry
from src.tools.preservation_auditor import audit_fork_preservation


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _FakeGit:
    """GitTool stand-in with toggleable read failures."""

    def __init__(
        self,
        repo_path: Path,
        *,
        ref_hashes: dict[tuple[str, str], str | None] | None = None,
        worktree_hashes: dict[str, str | None] | None = None,
        ref_contents: dict[tuple[str, str], str | None] | None = None,
        raise_on_content: bool = False,
    ):
        self.repo_path = repo_path
        self._ref_hashes = ref_hashes or {}
        self._worktree_hashes = worktree_hashes or {}
        self._ref_contents = ref_contents or {}
        self._raise_on_content = raise_on_content

    def get_file_hash(self, ref: str, file_path: str) -> str | None:
        return self._ref_hashes.get((ref, file_path))

    def get_worktree_blob_sha(self, file_path: str) -> str | None:
        return self._worktree_hashes.get(file_path)

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        if self._raise_on_content:
            raise RuntimeError("simulated git failure")
        return self._ref_contents.get((ref, file_path))


def _fd(
    file_path: str,
    *,
    fork_added: int = 80,
    fork_deleted: int = 20,
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


def _state_with_c_file(fp: str, tmp_path: Path) -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    state = MergeState(config=config)
    state.merge_base_commit = "base-sha"
    state.merge_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="base-sha",
        phases=[
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MergePhase.AUTO_MERGE,
                file_paths=[fp],
                risk_level=RiskLevel.AUTO_SAFE,
                change_category=FileChangeCategory.C,
            )
        ],
        risk_summary=RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="test",
    )
    state.file_categories = {fp: FileChangeCategory.C}
    state.file_diffs = [_fd(fp)]
    state.file_decision_records = {fp: _decision(fp)}
    return state


# --------------------------------------------------------------------------- #
# gate_skip_entry format
# --------------------------------------------------------------------------- #
def test_gate_skip_entry_shape() -> None:
    entry = gate_skip_entry("my_gate", "src/a.ts", "git read failed")
    assert entry["phase"] == GATE_SKIP_PHASE == "gate_skip"
    assert entry["message"].startswith("GATE_SKIPPED [my_gate] src/a.ts:")
    assert "git read failed" in entry["message"]
    assert "timestamp" in entry


# --------------------------------------------------------------------------- #
# preservation auditor — wholesale + partial skip sites
# --------------------------------------------------------------------------- #
class TestPreservationAuditRecordsSkip:
    def test_unreadable_upstream_blob_records_gate_skip(self, tmp_path: Path) -> None:
        state = _state_with_c_file("src/customizer.py", tmp_path)
        # upstream hash missing → wholesale check cannot run for this C-class file
        git = _FakeGit(
            tmp_path,
            ref_hashes={},  # get_file_hash → None
            worktree_hashes={"src/customizer.py": "wt-sha"},
        )

        losses = audit_fork_preservation(state, git)

        assert losses == []  # return contract unchanged
        skips = [e for e in state.errors if e["phase"] == GATE_SKIP_PHASE]
        assert len(skips) == 1
        assert "preservation_audit" in skips[0]["message"]
        assert "src/customizer.py" in skips[0]["message"]

    def test_unreadable_partial_content_records_line_check_skip(
        self, tmp_path: Path
    ) -> None:
        fp = "src/customizer.py"
        state = _state_with_c_file(fp, tmp_path)
        # hashes resolve and differ (worktree != upstream → partial check runs),
        # but content reads return None → line-level check silently skipped.
        git = _FakeGit(
            tmp_path,
            ref_hashes={("upstream/main", fp): "up-sha"},
            worktree_hashes={fp: "different-sha"},
            ref_contents={},  # get_file_content → None
        )

        losses = audit_fork_preservation(state, git)

        assert losses == []
        skips = [e for e in state.errors if e["phase"] == GATE_SKIP_PHASE]
        assert len(skips) == 1
        assert "preservation_line_check" in skips[0]["message"]

    def test_healthy_read_records_no_skip(self, tmp_path: Path) -> None:
        fp = "src/customizer.py"
        state = _state_with_c_file(fp, tmp_path)
        # worktree byte-equals upstream → wholesale loss flagged, no skip recorded
        git = _FakeGit(
            tmp_path,
            ref_hashes={("upstream/main", fp): "same-sha"},
            worktree_hashes={fp: "same-sha"},
        )
        losses = audit_fork_preservation(state, git)
        assert len(losses) == 1
        assert [e for e in state.errors if e["phase"] == GATE_SKIP_PHASE] == []


# --------------------------------------------------------------------------- #
# executor — fork-export preservation skip on a genuine git exception
# --------------------------------------------------------------------------- #
class TestExecutorForkExportSkip:
    def _executor(self, git_tool):
        from src.agents.executor_agent import ExecutorAgent

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return ExecutorAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)

    def test_records_skip_when_merge_base_read_raises(self, tmp_path: Path) -> None:
        git = _FakeGit(tmp_path, raise_on_content=True)
        executor = self._executor(git)
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"

        # clean merged content (no invented symbol, no dup) so the only thing
        # that can fire is the fork-export check — which is skipped on the raise.
        reason = executor._single_shot_fidelity_issue(
            "src/a.ts",
            "export const x = 1\n",
            "export const x = 1\n",
            "export const x = 1\n",
            state,
        )
        assert reason is None  # check skipped, not a false escalation
        skips = [e for e in state.errors if e["phase"] == GATE_SKIP_PHASE]
        assert len(skips) == 1
        assert "fork_export_preservation" in skips[0]["message"]


# --------------------------------------------------------------------------- #
# judge — deterministic pipeline records into self._gate_skips (read-only)
# --------------------------------------------------------------------------- #
class TestJudgePipelineSkip:
    def _judge(self, git_tool):
        from src.agents.judge_agent import JudgeAgent

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return JudgeAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)

    def test_records_skip_when_git_tool_none(self) -> None:
        judge = self._judge(None)
        judge._gate_skips = []
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"
        restricted = judge.restricted_view(state)

        issues = judge._run_deterministic_pipeline(restricted, {})

        assert issues == []
        assert len(judge._gate_skips) == 1
        assert "judge_deterministic_pipeline" in judge._gate_skips[0]["message"]
        assert "git_tool unavailable" in judge._gate_skips[0]["message"]

    def test_records_skip_when_merge_base_missing(self, tmp_path: Path) -> None:
        git = MagicMock()
        git.repo_path = tmp_path
        judge = self._judge(git)
        judge._gate_skips = []
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = ""  # missing → pipeline cannot run
        restricted = judge.restricted_view(state)

        issues = judge._run_deterministic_pipeline(restricted, {})

        assert issues == []
        assert len(judge._gate_skips) == 1
        assert "merge_base or upstream_ref missing" in judge._gate_skips[0]["message"]

    def test_no_skip_recorded_on_healthy_preconditions(self, tmp_path: Path) -> None:
        git = MagicMock()
        git.repo_path = tmp_path
        judge = self._judge(git)
        judge._gate_skips = []
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"
        restricted = judge.restricted_view(state)

        judge._run_deterministic_pipeline(restricted, {})
        # no files in the plan → no per-file work, but preconditions were fine,
        # so no pipeline-level skip is recorded.
        assert judge._gate_skips == []


# --------------------------------------------------------------------------- #
# judge_review — persists payload skips into state.errors, deduped
# --------------------------------------------------------------------------- #
class TestPersistGateSkips:
    def test_persists_and_dedups(self) -> None:
        from src.core.phases.judge_review import _persist_gate_skips

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)

        e1 = gate_skip_entry("judge_deterministic_pipeline", "(all)", "git down")
        e2 = gate_skip_entry("preservation_audit", "src/x.py", "blob unreadable")

        _persist_gate_skips(state, [e1, e2])
        assert len(state.errors) == 2

        # a second round re-reports the same persistent skip → no duplicate
        _persist_gate_skips(state, [e1])
        assert len(state.errors) == 2

    def test_none_or_empty_is_noop(self) -> None:
        from src.core.phases.judge_review import _persist_gate_skips

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        _persist_gate_skips(state, None)
        _persist_gate_skips(state, [])
        assert state.errors == []
