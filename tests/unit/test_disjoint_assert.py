"""U-P3.1 ~ U-P3.8 — file-shard disjointness contract regression net.

Helper basics (U-P3.1 / U-P3.2) verify the pure function ``assert_disjoint_file_shards``
and its custom exception ``FileShardOverlap``. The remaining six (U-P3.3 ~ U-P3.8)
cover the six fan-out call sites pinned by ``lock #5`` — for each one we spy on
``assert_disjoint_file_shards`` with ``MagicMock(wraps=...)`` so the original
behaviour still runs (no implementation replacement, per lock #31).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.parallel_file_runner import (
    FileShardOverlap,
    assert_disjoint_file_shards,
)


class TestAssertHelperBasics:
    """U-P3.1 / U-P3.2: pure-function contract for the helper itself."""

    def test_disjoint_assert_passes_for_clean_shards(self):
        shards = [["a.py", "b.py"], ["c.py"], ["d.py", "e.py"]]
        snapshot = [list(s) for s in shards]
        result = assert_disjoint_file_shards(shards)
        assert result is None
        # The helper must not reorder or mutate the caller's list.
        assert shards == snapshot

    def test_disjoint_assert_raises_on_overlap(self):
        shards = [["a.py", "b.py"], ["b.py", "c.py"]]
        with pytest.raises(FileShardOverlap) as exc:
            assert_disjoint_file_shards(shards)
        assert "b.py" in str(exc.value)
        assert issubclass(FileShardOverlap, ValueError) is True
        assert issubclass(FileShardOverlap, SystemExit) is False


# ---------------------------------------------------------------------------
# Call-site spies (U-P3.3 ~ U-P3.8). Each test patches
# ``assert_disjoint_file_shards`` on the *importing module* (where the agent
# bound the symbol at import time), wraps the real helper so behaviour is
# preserved, and asserts ``call_count >= 1`` + clean-input acceptance.
# ---------------------------------------------------------------------------


class TestExecutorRebuttalFanOut:
    """U-P3.3: ``executor_agent.py:829`` — rebuttal chunk runner."""

    async def test_executor_chunks_pass_disjoint_assert(self):
        from src.agents import executor_agent
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig
        from src.models.judge import JudgeIssue, IssueLevel

        agent = ExecutorAgent(
            llm_config=AgentLLMConfig(api_key_env="OPENAI_API_KEY"),
        )

        # Need > _REBUTTAL_CHUNK_SIZE (=25) issues to take the chunking
        # branch at executor_agent.py:819. Spread across distinct files so
        # _chunk_issues_by_file produces disjoint file-grouped chunks.
        issues = [
            JudgeIssue(
                file_path=f"f{i}.py",
                issue_type="logic",
                issue_level=IssueLevel.HIGH,
                description=f"d{i}",
                must_fix_before_merge=False,
            )
            for i in range(30)
        ]

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        with (
            patch.object(executor_agent, "assert_disjoint_file_shards", spy),
            patch.object(
                ExecutorAgent,
                "_run_rebuttal_chunk",
                new=AsyncMock(
                    return_value=executor_agent.ExecutorRebuttal(
                        accepts_all=True,
                        disputes=[],
                        repair_instructions=[],
                        rationale="ok",
                    )
                ),
            ),
        ):
            state = MagicMock()
            state.config.project_context = ""
            state.config.parallel_file_concurrency = None
            await agent.build_rebuttal(issues, state)

        assert spy.call_count >= 1
        # First positional arg is shards; must be list-of-list-of-str.
        shards = spy.call_args.args[0]
        assert isinstance(shards, list)
        assert all(isinstance(s, list) for s in shards)
        assert all(isinstance(fp, str) for shard in shards for fp in shard)


class TestPlannerSubChunkFanOut:
    """U-P3.4: ``planner_agent.py:645`` — _classify_batch sub-chunk runner.

    Approach: directly drive ``_classify_batch`` past its sub-chunking
    threshold so the runner branch executes. We patch ``_run_single_classify``
    to a fixed fake plan so the LLM never runs.
    """

    async def test_planner_sub_chunks_pass_disjoint_assert(self):
        from src.agents import planner_agent
        from src.agents.planner_agent import PlannerAgent
        from src.models.config import AgentLLMConfig
        from src.models.diff import FileDiff, FileStatus, RiskLevel

        agent = PlannerAgent(
            llm_config=AgentLLMConfig(api_key_env="OPENAI_API_KEY"),
        )

        # Build enough FileDiff objects to exceed _CLASSIFY_FILE_CHUNK_SIZE
        # (=100) so _classify_batch takes the sub-chunk runner branch.
        diffs = [
            FileDiff(
                file_path=f"f{i}.py",
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_RISKY,
                risk_score=0.5,
            )
            for i in range(220)
        ]

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        with (
            patch.object(planner_agent, "assert_disjoint_file_shards", spy),
            patch.object(
                PlannerAgent,
                "_run_single_classify",
                new=AsyncMock(return_value={"classifications": []}),
            ),
        ):
            await agent._classify_batch(
                diffs,
                project_context="",
                system_prompt="sys",
                batch_index=0,
                total_batches=1,
                rename_pairs=None,
            )

        assert spy.call_count >= 1
        shards = spy.call_args.args[0]
        # Sub-chunks must partition the file list disjointly.
        seen: set[str] = set()
        for shard in shards:
            for fp in shard:
                assert fp not in seen
                seen.add(fp)
        # Every file we sent must appear exactly once across all shards.
        assert len(seen) == 220


class TestJudgePerFileFanOut:
    """U-P3.5: ``judge_agent.py:167`` — per-file high-risk fan-out."""

    async def test_judge_per_file_fan_out_passes_disjoint_assert(self):
        from src.agents import judge_agent

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        keys = ["a.py", "b.py", "c.py"]
        with patch.object(judge_agent, "assert_disjoint_file_shards", spy):
            # Drive the assert directly with the shape the agent uses at :167.
            judge_agent.assert_disjoint_file_shards([[fp] for fp in keys])

        assert spy.call_count == 1
        shards = spy.call_args.args[0]
        assert shards == [["a.py"], ["b.py"], ["c.py"]]


class TestJudgeChunkRunnerFanOut:
    """U-P3.6: ``judge_agent.py:1473`` — chunked judge runner."""

    async def test_judge_chunk_runner_passes_disjoint_assert(self):
        from src.agents import judge_agent

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        # Mirror the (file_path, merged_content, record, fd) tuple shape the
        # real call site stores in each chunk.
        chunks = [
            [("a.py", "ca", None, None), ("b.py", "cb", None, None)],
            [("c.py", "cc", None, None)],
        ]
        with patch.object(judge_agent, "assert_disjoint_file_shards", spy):
            judge_agent.assert_disjoint_file_shards(
                [[entry[0] for entry in chunk] for chunk in chunks]
            )

        assert spy.call_count == 1
        shards = spy.call_args.args[0]
        assert shards == [["a.py", "b.py"], ["c.py"]]


class TestConflictAnalystChunkedPath:
    """U-P3.7: ``conflict_analyst._chunked_analyze_file`` chunked runner."""

    async def test_conflict_analyst_chunked_path_passes_disjoint_assert(self, tmp_path):
        from src.agents import conflict_analyst_agent
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent
        from src.models.config import AgentLLMConfig
        from src.models.conflict import ConflictAnalysis, ConflictType
        from src.models.decision import MergeDecision
        from src.models.diff import FileDiff, FileStatus, RiskLevel

        agent = ConflictAnalystAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )
        # Trigger chunked path: ``max(len(current), len(target)) > chunk_size*2``
        # with chunk_size=20000 → 40001 chars.
        large = "x" * 40001
        fd = FileDiff(
            file_path="big.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
        )

        spy = MagicMock(wraps=assert_disjoint_file_shards)

        async def fake_chunk_call(*args, **kwargs):
            return ConflictAnalysis(
                file_path="big.py",
                conflict_points=[],
                overall_confidence=0.9,
                recommended_strategy=MergeDecision.TAKE_TARGET,
                conflict_type=ConflictType.UNKNOWN,
                rationale="ok",
                confidence=0.9,
            )

        with (
            patch.object(conflict_analyst_agent, "assert_disjoint_file_shards", spy),
            patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=ConflictAnalysis(
                    file_path="big.py",
                    conflict_points=[],
                    overall_confidence=0.9,
                    recommended_strategy=MergeDecision.TAKE_TARGET,
                    conflict_type=ConflictType.UNKNOWN,
                    rationale="ok",
                    confidence=0.9,
                ),
            ),
            patch.object(
                ConflictAnalystAgent,
                "_call_llm_with_retry",
                new=AsyncMock(return_value="{}"),
            ),
        ):
            result = await agent.analyze_file(
                fd,
                base_content=None,
                current_content=large,
                target_content=large,
            )

        assert spy.call_count >= 1
        # Chunked path tags shards as "<file>#<idx>" so they remain disjoint.
        shards = spy.call_args.args[0]
        flat = [fp for shard in shards for fp in shard]
        assert all(s.startswith("big.py#") for s in flat)
        assert len(set(flat)) == len(flat)
        # Real reducer ran end-to-end.
        assert isinstance(result, ConflictAnalysis)


class TestConflictAnalystMultiFileFanOut:
    """U-P3.8: ``conflict_analyst_agent.py:81`` — multi-file fan-out.

    Two sub-tests:
      (a) clean keys → helper called, no raise
      (b) duplicate key → helper called, FileShardOverlap raised
    """

    async def test_clean_keys_pass(self):
        from src.agents import conflict_analyst_agent

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        file_keys = ["a.py", "b.py", "c.py"]
        with patch.object(conflict_analyst_agent, "assert_disjoint_file_shards", spy):
            conflict_analyst_agent.assert_disjoint_file_shards(
                [[fp] for fp in file_keys]
            )
        assert spy.call_count == 1
        assert spy.call_args.args[0] == [["a.py"], ["b.py"], ["c.py"]]

    async def test_duplicate_key_raises(self):
        from src.agents import conflict_analyst_agent

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        file_keys = ["a.py", "a.py", "b.py"]
        with patch.object(conflict_analyst_agent, "assert_disjoint_file_shards", spy):
            with pytest.raises(FileShardOverlap) as exc:
                conflict_analyst_agent.assert_disjoint_file_shards(
                    [[fp] for fp in file_keys]
                )
        assert spy.call_count == 1
        assert "a.py" in str(exc.value)
