"""Unit tests for O-B5: B-class drift sanity-check + actual-diff replay.

Covers:
- ``CommitReplayer.replay_clean_commits`` uses ``diff_files_between`` to
  track *actually* changed files instead of trusting ``commit.files``.
- ``AutoMergePhase._b_class_sanity_check`` flags worktree files whose
  blob sha differs from upstream.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.phases.auto_merge import AutoMergePhase
from src.models.diff import FileChangeCategory
from src.tools.commit_replayer import CommitReplayer


class TestOB5ReplayClean_UsesActualDiff:
    @pytest.mark.asyncio
    async def test_uses_diff_files_between_not_commit_files(self):
        git_tool = MagicMock()
        git_tool.get_head_sha.side_effect = ["before_sha", "after_sha"]
        git_tool.cherry_pick_strategy_ladder.return_value = (True, "default")
        git_tool.diff_files_between.return_value = ["actually.py"]

        replayer = CommitReplayer()
        commit = {
            "sha": "abc123",
            "files": ["actually.py", "skipped_by_X_theirs.py"],
            "message": "feat: x",
        }
        state = MagicMock()
        result = await replayer.replay_clean_commits(git_tool, [commit], state)

        assert result.replayed_files == ["actually.py"]
        assert "skipped_by_X_theirs.py" not in result.replayed_files
        git_tool.diff_files_between.assert_called_once_with(
            "before_sha", "after_sha"
        )

    @pytest.mark.asyncio
    async def test_empty_cherry_pick_yields_no_replayed_files(self):
        git_tool = MagicMock()
        git_tool.get_head_sha.side_effect = ["same_sha", "same_sha"]
        git_tool.cherry_pick_strategy_ladder.return_value = (True, "default")
        git_tool.diff_files_between.return_value = []

        replayer = CommitReplayer()
        commit = {"sha": "abc", "files": ["f1.py", "f2.py"], "message": ""}
        state = MagicMock()
        result = await replayer.replay_clean_commits(git_tool, [commit], state)

        assert result.replayed_shas == ["abc"]
        assert result.replayed_files == []

    @pytest.mark.asyncio
    async def test_failed_cherry_pick_skips_diff_call(self):
        git_tool = MagicMock()
        git_tool.get_head_sha.return_value = "before_sha"
        git_tool.cherry_pick_strategy_ladder.return_value = (False, "default")

        replayer = CommitReplayer()
        commit = {"sha": "bad", "files": ["x.py"], "message": ""}
        state = MagicMock()
        result = await replayer.replay_clean_commits(git_tool, [commit], state)

        assert result.failed_shas == ["bad"]
        assert result.replayed_files == []
        git_tool.diff_files_between.assert_not_called()


class TestOB5SanityCheck:
    def _make_phase_with_b_batch(self, files: list[str]):
        batch = MagicMock()
        batch.change_category = FileChangeCategory.B
        batch.file_paths = files
        plan = MagicMock()
        plan.phases = [batch]
        state = MagicMock()
        state.merge_plan = plan
        state.config.upstream_ref = "upstream/main"
        return state

    @pytest.mark.asyncio
    async def test_no_drift_when_hashes_match(self):
        state = self._make_phase_with_b_batch(["a.py", "b.py"])
        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = "deadbeef"
        ctx.git_tool.get_worktree_blob_sha.return_value = "deadbeef"

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []

    @pytest.mark.asyncio
    async def test_drift_when_worktree_differs_from_upstream(self):
        state = self._make_phase_with_b_batch(["a.py", "b.py"])
        ctx = MagicMock()
        ctx.git_tool.get_file_hash.side_effect = ["up1", "up2"]
        ctx.git_tool.get_worktree_blob_sha.side_effect = ["up1", "fork2"]

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == ["b.py"]

    @pytest.mark.asyncio
    async def test_missing_blob_skipped(self):
        state = self._make_phase_with_b_batch(["missing.py"])
        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = None
        ctx.git_tool.get_worktree_blob_sha.return_value = "abc"

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []

    @pytest.mark.asyncio
    async def test_non_b_batches_ignored(self):
        batch_b = MagicMock()
        batch_b.change_category = FileChangeCategory.B
        batch_b.file_paths = ["b.py"]
        batch_c = MagicMock()
        batch_c.change_category = FileChangeCategory.C
        batch_c.file_paths = ["c.py"]
        plan = MagicMock()
        plan.phases = [batch_b, batch_c]
        state = MagicMock()
        state.merge_plan = plan
        state.config.upstream_ref = "upstream/main"

        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = "u"
        ctx.git_tool.get_worktree_blob_sha.return_value = "f"

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == ["b.py"]
        assert ctx.git_tool.get_file_hash.call_count == 1

    @pytest.mark.asyncio
    async def test_no_plan_returns_empty(self):
        state = MagicMock()
        state.merge_plan = None
        ctx = MagicMock()

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []

    @pytest.mark.asyncio
    async def test_skips_files_already_escalated(self):
        """Files that the layer-skip path (or any earlier stage) already
        marked ESCALATE_HUMAN are intentional escalations, not silent
        drift — sanity-check must not double-count them."""
        from src.models.decision import FileDecisionRecord, MergeDecision, DecisionSource
        from src.models.diff import FileStatus

        state = self._make_phase_with_b_batch(["escalated.py", "real_drift.py"])
        state.file_decision_records = {
            "escalated.py": FileDecisionRecord(
                file_path="escalated.py",
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.ESCALATE_HUMAN,
                decision_source=DecisionSource.AUTO_EXECUTOR,
                rationale="layer dep gate",
                phase="auto_merge",
                agent="layer_dep_gate",
            ),
        }

        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = "upstream_sha"
        ctx.git_tool.get_worktree_blob_sha.return_value = "fork_sha"

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)

        assert drift == ["real_drift.py"]
        assert ctx.git_tool.get_file_hash.call_count == 1


class TestOJ3VerifyTakeDecisions:
    """O-J3: deterministic verification of take_target / take_current."""

    def _make_judge(
        self,
        hashes_by_ref: dict[tuple[str, str], str | None],
        worktree_hashes: dict[str, str | None],
    ):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        git_tool = MagicMock()
        git_tool.get_file_hash.side_effect = lambda ref, fp: hashes_by_ref.get(
            (ref, fp)
        )
        git_tool.get_worktree_blob_sha.side_effect = lambda fp: worktree_hashes.get(fp)
        cfg = AgentLLMConfig(
            provider="anthropic", model="claude-opus-4-6", api_key_env="TEST_KEY"
        )
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            return JudgeAgent(cfg, git_tool=git_tool)

    def _make_state(self):
        state = MagicMock()
        state.config.upstream_ref = "upstream/main"
        state.config.fork_ref = "feature/fork"
        return state

    def _make_record(self, decision: str):
        from src.models.decision import (
            DecisionSource,
            FileDecisionRecord,
            MergeDecision,
        )
        from src.models.diff import FileStatus

        return FileDecisionRecord(
            file_path="x.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision(decision),
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="r",
        )

    def _make_diff(self, sensitive: bool = False):
        diff = MagicMock()
        diff.is_security_sensitive = sensitive
        return diff

    def test_take_target_match_is_skipped(self):
        agent = self._make_judge(
            hashes_by_ref={("upstream/main", "x.py"): "sha-a"},
            worktree_hashes={"x.py": "sha-a"},
        )
        records = {"x.py": self._make_record("take_target")}
        skipped, drift = agent._verify_take_decisions(
            self._make_state(), records, {"x.py": self._make_diff()}
        )
        assert skipped == ["x.py"]
        assert drift == []

    def test_take_target_mismatch_emits_drift_issue(self):
        agent = self._make_judge(
            hashes_by_ref={("upstream/main", "x.py"): "sha-a"},
            worktree_hashes={"x.py": "sha-b"},
        )
        records = {"x.py": self._make_record("take_target")}
        skipped, drift = agent._verify_take_decisions(
            self._make_state(), records, {"x.py": self._make_diff()}
        )
        assert skipped == []
        assert len(drift) == 1
        assert drift[0].issue_type == "take_decision_drift"
        assert drift[0].file_path == "x.py"

    def test_take_current_uses_fork_ref(self):
        agent = self._make_judge(
            hashes_by_ref={("feature/fork", "x.py"): "sha-fork"},
            worktree_hashes={"x.py": "sha-fork"},
        )
        records = {"x.py": self._make_record("take_current")}
        skipped, drift = agent._verify_take_decisions(
            self._make_state(), records, {"x.py": self._make_diff()}
        )
        assert skipped == ["x.py"]
        assert drift == []

    def test_security_sensitive_falls_through(self):
        agent = self._make_judge(
            hashes_by_ref={("upstream/main", "x.py"): "sha-a"},
            worktree_hashes={"x.py": "sha-b"},
        )
        records = {"x.py": self._make_record("take_target")}
        skipped, drift = agent._verify_take_decisions(
            self._make_state(), records, {"x.py": self._make_diff(sensitive=True)}
        )
        assert skipped == [] and drift == []  # left for LLM path

    def test_non_take_decision_ignored(self):
        agent = self._make_judge(hashes_by_ref={}, worktree_hashes={})
        records = {"x.py": self._make_record("semantic_merge")}
        skipped, drift = agent._verify_take_decisions(
            self._make_state(), records, {"x.py": self._make_diff()}
        )
        assert skipped == [] and drift == []

    def test_missing_blob_skips_check(self):
        agent = self._make_judge(
            hashes_by_ref={("upstream/main", "x.py"): None},
            worktree_hashes={"x.py": "sha-anything"},
        )
        records = {"x.py": self._make_record("take_target")}
        skipped, drift = agent._verify_take_decisions(
            self._make_state(), records, {"x.py": self._make_diff()}
        )
        assert skipped == [] and drift == []
