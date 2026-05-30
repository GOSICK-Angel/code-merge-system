"""W5 W1: git read-status split — distinguish a legitimately-absent blob from a
genuine git failure, so the P1 gate-skip alarm fires ONLY on a real GIT_ERROR and
stays silent on ABSENT.

Two layers:
- the ``git_tool`` ``_checked`` readers classify OK / ABSENT / GIT_ERROR against a
  REAL temp git repo (validates the stderr-based predicate against actual git);
- each newly-instrumented consumer (B-class drift sanity, executor fork-export,
  judge take-verification) alarms on GIT_ERROR and is silent on ABSENT.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.models.config import AgentLLMConfig, MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState
from src.tools.gate_skip import GATE_SKIP_PHASE
from src.tools.git_tool import GitReadStatus, GitTool


# --------------------------------------------------------------------------- #
# layer 1 — the _checked readers against a REAL git repo
# --------------------------------------------------------------------------- #
def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def real_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("hello\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


class TestCheckedReaders:
    def test_file_hash_ok(self, real_repo: Path) -> None:
        sha, status = GitTool(str(real_repo)).get_file_hash_checked("HEAD", "a.txt")
        assert status == GitReadStatus.OK
        assert sha and len(sha) >= 7

    def test_file_hash_absent_path_is_absent(self, real_repo: Path) -> None:
        sha, status = GitTool(str(real_repo)).get_file_hash_checked("HEAD", "nope.txt")
        assert sha is None
        assert status == GitReadStatus.ABSENT

    def test_file_hash_broken_ref_is_git_error(self, real_repo: Path) -> None:
        sha, status = GitTool(str(real_repo)).get_file_hash_checked("bad_ref", "a.txt")
        assert sha is None
        assert status == GitReadStatus.GIT_ERROR

    def test_file_content_ok_absent_broken(self, real_repo: Path) -> None:
        git = GitTool(str(real_repo))
        content, status = git.get_file_content_checked("HEAD", "a.txt")
        assert content == "hello\n"
        assert status == GitReadStatus.OK
        _c, s_absent = git.get_file_content_checked("HEAD", "nope.txt")
        assert s_absent == GitReadStatus.ABSENT
        _c, s_err = git.get_file_content_checked("bad_ref", "a.txt")
        assert s_err == GitReadStatus.GIT_ERROR

    def test_worktree_blob_sha_absent_vs_ok(self, real_repo: Path) -> None:
        git = GitTool(str(real_repo))
        sha, status = git.get_worktree_blob_sha_checked("a.txt")
        assert sha and status == GitReadStatus.OK
        _s, absent = git.get_worktree_blob_sha_checked("nope.txt")
        assert absent == GitReadStatus.ABSENT


# --------------------------------------------------------------------------- #
# layer 2 — per-consumer alarm-on-GIT_ERROR / silent-on-ABSENT
# --------------------------------------------------------------------------- #
class _StatusGit:
    """git_tool stand-in returning explicit ``(value, status)`` for the W1
    ``_checked`` readers. Unconfigured keys default to ABSENT (silent)."""

    def __init__(
        self,
        repo_path: Path,
        *,
        hashes: dict[tuple[str, str], tuple[str | None, GitReadStatus]] | None = None,
        worktree: dict[str, tuple[str | None, GitReadStatus]] | None = None,
        contents: dict[tuple[str, str], tuple[str | None, GitReadStatus]] | None = None,
    ):
        self.repo_path = repo_path
        self._hashes = hashes or {}
        self._worktree = worktree or {}
        self._contents = contents or {}

    def get_file_hash_checked(
        self, ref: str, file_path: str
    ) -> tuple[str | None, GitReadStatus]:
        return self._hashes.get((ref, file_path), (None, GitReadStatus.ABSENT))

    def get_worktree_blob_sha_checked(
        self, file_path: str
    ) -> tuple[str | None, GitReadStatus]:
        return self._worktree.get(file_path, (None, GitReadStatus.ABSENT))

    def get_file_content_checked(
        self, ref: str, file_path: str
    ) -> tuple[str | None, GitReadStatus]:
        return self._contents.get((ref, file_path), (None, GitReadStatus.ABSENT))


def _b_class_state(fp: str) -> MergeState:
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
                change_category=FileChangeCategory.B,
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
        project_context_summary="t",
    )
    return state


def _gate_skips(state: MergeState) -> list[dict[str, str]]:
    return [e for e in state.errors if e["phase"] == GATE_SKIP_PHASE]


class TestBClassSanityAlarm:
    async def test_alarms_on_git_error(self, tmp_path: Path) -> None:
        from src.core.phases.auto_merge import AutoMergePhase

        fp = "src/up.go"
        state = _b_class_state(fp)
        ctx = MagicMock()
        ctx.git_tool = _StatusGit(
            tmp_path,
            hashes={("upstream/main", fp): (None, GitReadStatus.GIT_ERROR)},
            worktree={fp: (None, GitReadStatus.GIT_ERROR)},
        )
        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []
        skips = _gate_skips(state)
        assert len(skips) == 1
        assert "b_class_drift_sanity" in skips[0]["message"]
        assert fp in skips[0]["message"]

    async def test_silent_on_absent(self, tmp_path: Path) -> None:
        from src.core.phases.auto_merge import AutoMergePhase

        fp = "src/up.go"
        state = _b_class_state(fp)
        ctx = MagicMock()
        ctx.git_tool = _StatusGit(
            tmp_path,
            hashes={("upstream/main", fp): (None, GitReadStatus.ABSENT)},
            worktree={fp: (None, GitReadStatus.ABSENT)},
        )
        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []
        assert _gate_skips(state) == []


class TestExecutorForkExportAbsent:
    def _executor(self, git_tool: object):
        from src.agents.executor_agent import ExecutorAgent

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return ExecutorAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)

    def test_absent_base_blob_is_silent(self, tmp_path: Path) -> None:
        # base blob ABSENT (fork added the file) → nothing to preserve, no alarm.
        executor = self._executor(_StatusGit(tmp_path))
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"
        reason = executor._single_shot_fidelity_issue(
            "src/a.ts",
            "export const x = 1\n",
            "export const x = 1\n",
            "export const x = 1\n",
            state,
        )
        assert reason is None
        assert _gate_skips(state) == []


class TestJudgeTakeVerificationAlarm:
    def _judge(self, git_tool: object):
        from src.agents.judge_agent import JudgeAgent

        with patch("src.llm.client.LLMClientFactory.create", return_value=MagicMock()):
            return JudgeAgent(llm_config=AgentLLMConfig(), git_tool=git_tool)

    def _record(self, fp: str) -> FileDecisionRecord:
        return FileDecisionRecord(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.9,
            rationale="t",
            timestamp=datetime.now(),
        )

    def _fd(self, fp: str) -> FileDiff:
        return FileDiff(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
            lines_added=1,
            lines_deleted=1,
        )

    def test_alarms_on_git_error(self, tmp_path: Path) -> None:
        fp = "src/x.go"
        judge = self._judge(
            _StatusGit(
                tmp_path,
                hashes={("upstream/main", fp): (None, GitReadStatus.GIT_ERROR)},
                worktree={fp: (None, GitReadStatus.GIT_ERROR)},
            )
        )
        judge._gate_skips = []
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        restricted = judge.restricted_view(state)
        skipped, issues = judge._verify_take_decisions(
            restricted, {fp: self._record(fp)}, {fp: self._fd(fp)}
        )
        assert skipped == []
        assert issues == []
        assert len(judge._gate_skips) == 1
        assert "judge_take_verification" in judge._gate_skips[0]["message"]

    def test_silent_on_absent(self, tmp_path: Path) -> None:
        fp = "src/x.go"
        judge = self._judge(
            _StatusGit(
                tmp_path,
                hashes={("upstream/main", fp): (None, GitReadStatus.ABSENT)},
                worktree={fp: (None, GitReadStatus.ABSENT)},
            )
        )
        judge._gate_skips = []
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        restricted = judge.restricted_view(state)
        skipped, issues = judge._verify_take_decisions(
            restricted, {fp: self._record(fp)}, {fp: self._fd(fp)}
        )
        assert skipped == []
        assert issues == []
        assert judge._gate_skips == []
