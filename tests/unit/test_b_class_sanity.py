"""Unit tests for O-B5: B-class drift sanity-check + actual-diff replay.

Covers:
- ``CommitReplayer.replay_clean_commits`` uses ``diff_files_between`` to
  track *actually* changed files instead of trusting ``commit.files``.
- ``AutoMergePhase._b_class_sanity_check`` flags worktree files whose
  blob sha differs from upstream.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.phases.auto_merge import AutoMergePhase
from src.models.diff import FileChangeCategory
from src.tools.commit_replayer import CommitReplayer
from src.tools.git_tool import GitReadStatus


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
        git_tool.diff_files_between.assert_called_once_with("before_sha", "after_sha")

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
        ctx.git_tool.get_file_hash_checked.return_value = ("deadbeef", GitReadStatus.OK)
        ctx.git_tool.get_worktree_blob_sha_checked.return_value = (
            "deadbeef",
            GitReadStatus.OK,
        )

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []

    @pytest.mark.asyncio
    async def test_drift_when_worktree_differs_from_upstream(self):
        state = self._make_phase_with_b_batch(["a.py", "b.py"])
        ctx = MagicMock()
        ctx.git_tool.get_file_hash_checked.side_effect = [
            ("up1", GitReadStatus.OK),
            ("up2", GitReadStatus.OK),
        ]
        ctx.git_tool.get_worktree_blob_sha_checked.side_effect = [
            ("up1", GitReadStatus.OK),
            ("fork2", GitReadStatus.OK),
        ]

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == ["b.py"]

    @pytest.mark.asyncio
    async def test_missing_blob_skipped(self):
        state = self._make_phase_with_b_batch(["missing.py"])
        ctx = MagicMock()
        # absent (not git-broken) → silent skip, no drift, no alarm.
        ctx.git_tool.get_file_hash_checked.return_value = (None, GitReadStatus.ABSENT)
        ctx.git_tool.get_worktree_blob_sha_checked.return_value = (
            "abc",
            GitReadStatus.OK,
        )

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
        ctx.git_tool.get_file_hash_checked.return_value = ("u", GitReadStatus.OK)
        ctx.git_tool.get_worktree_blob_sha_checked.return_value = (
            "f",
            GitReadStatus.OK,
        )

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == ["b.py"]
        assert ctx.git_tool.get_file_hash_checked.call_count == 1

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
        from src.models.decision import (
            FileDecisionRecord,
            MergeDecision,
            DecisionSource,
        )
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
        ctx.git_tool.get_file_hash_checked.return_value = (
            "upstream_sha",
            GitReadStatus.OK,
        )
        ctx.git_tool.get_worktree_blob_sha_checked.return_value = (
            "fork_sha",
            GitReadStatus.OK,
        )

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)

        assert drift == ["real_drift.py"]
        assert ctx.git_tool.get_file_hash_checked.call_count == 1


class TestOB5SanityCheckDMissing:
    """O-B5 must also cover D-missing (upstream-new) files: new feature files
    added across several upstream commits land in D-missing batches, and a
    later commit's failed replay leaves them drifted while still take_target."""

    def _state_with_batch(self, files, category, div_map=None):
        batch = MagicMock()
        batch.change_category = category
        batch.file_paths = files
        plan = MagicMock()
        plan.phases = [batch]
        state = MagicMock()
        state.merge_plan = plan
        state.config.upstream_ref = "upstream/main"
        state.file_decision_records = {}
        state.fork_divergence_map = div_map or {}
        return state

    @pytest.mark.asyncio
    async def test_d_missing_drift_detected(self):
        state = self._state_with_batch(["new_feature.go"], FileChangeCategory.D_MISSING)
        ctx = MagicMock()
        ctx.git_tool.get_file_hash_checked.return_value = (
            "upstream_sha",
            GitReadStatus.OK,
        )
        ctx.git_tool.get_worktree_blob_sha_checked.return_value = (
            "intermediate_sha",
            GitReadStatus.OK,
        )

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == ["new_feature.go"]

    @pytest.mark.asyncio
    async def test_fork_deleted_d_missing_not_flagged(self):
        from src.models.diff import ForkDivergence

        state = self._state_with_batch(
            ["removed_by_fork.go"],
            FileChangeCategory.D_MISSING,
            div_map={"removed_by_fork.go": ForkDivergence.FORK_DELETED.value},
        )
        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = "upstream_sha"
        ctx.git_tool.get_worktree_blob_sha.return_value = "different_sha"

        drift = await AutoMergePhase()._b_class_sanity_check(state, ctx)
        assert drift == []


class TestOB5RepairBClassDrift:
    """O-B5 repair: drifted B-class files are rewritten to upstream content
    (take_target) so a multi-commit replay gap is not left for Judge / lost
    silently when the file is auto_safe."""

    def _record(self, decision: str, fp: str = "x.go"):
        from src.models.decision import (
            DecisionSource,
            FileDecisionRecord,
            MergeDecision,
        )
        from src.models.diff import FileStatus

        return FileDecisionRecord(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision(decision),
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="Cherry-picked cleanly from upstream commit",
        )

    def _state(self, fp: str):
        state = MagicMock()
        state.config.upstream_ref = "origin/forgejo"
        state.file_decision_records = {fp: self._record("take_target", fp)}
        return state

    @pytest.mark.asyncio
    async def test_repairs_drift_to_upstream(self):
        fp = "routers/web/user/setting/authorized_integrations.go"
        state = self._state(fp)
        ctx = MagicMock()
        # After the rewrite the worktree blob equals upstream.
        ctx.git_tool.get_file_hash.return_value = "up_sha"
        ctx.git_tool.get_worktree_blob_sha.return_value = "up_sha"
        executor = MagicMock()
        executor.execute_auto_merge = AsyncMock(
            return_value=self._record("take_target", fp)
        )

        repaired = await AutoMergePhase()._repair_b_class_drift(
            state, ctx, [fp], {fp: MagicMock()}, executor
        )

        assert repaired == {fp}
        executor.execute_auto_merge.assert_awaited_once()
        assert "drift repair" in state.file_decision_records[fp].rationale

    @pytest.mark.asyncio
    async def test_skips_file_without_diff(self):
        fp = "x.go"
        state = self._state(fp)
        ctx = MagicMock()
        executor = MagicMock()
        executor.execute_auto_merge = AsyncMock()

        repaired = await AutoMergePhase()._repair_b_class_drift(
            state, ctx, [fp], {}, executor
        )

        assert repaired == set()
        executor.execute_auto_merge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_repaired_when_still_drifts_after_rewrite(self):
        fp = "x.go"
        state = self._state(fp)
        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = "up_sha"
        ctx.git_tool.get_worktree_blob_sha.return_value = "still_other_sha"
        executor = MagicMock()
        executor.execute_auto_merge = AsyncMock(
            return_value=self._record("take_target", fp)
        )

        repaired = await AutoMergePhase()._repair_b_class_drift(
            state, ctx, [fp], {fp: MagicMock()}, executor
        )

        assert repaired == set()

    @pytest.mark.asyncio
    async def test_not_repaired_when_executor_escalates(self):
        fp = "x.go"
        state = self._state(fp)
        ctx = MagicMock()
        ctx.git_tool.get_file_hash.return_value = "up_sha"
        ctx.git_tool.get_worktree_blob_sha.return_value = "up_sha"
        executor = MagicMock()
        executor.execute_auto_merge = AsyncMock(
            return_value=self._record("escalate_human", fp)
        )

        repaired = await AutoMergePhase()._repair_b_class_drift(
            state, ctx, [fp], {fp: MagicMock()}, executor
        )

        assert repaired == set()


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

        def _hash_checked(ref: str, fp: str):
            val = hashes_by_ref.get((ref, fp))
            return val, GitReadStatus.OK if val is not None else GitReadStatus.ABSENT

        def _worktree_checked(fp: str):
            val = worktree_hashes.get(fp)
            return val, GitReadStatus.OK if val is not None else GitReadStatus.ABSENT

        git_tool.get_file_hash_checked.side_effect = _hash_checked
        git_tool.get_worktree_blob_sha_checked.side_effect = _worktree_checked
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
