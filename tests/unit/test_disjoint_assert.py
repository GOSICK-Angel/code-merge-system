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


def _build_judge_state(tmp_path, file_paths, *, risky_count=None):
    """Build a real MergeState that lets JudgeAgent.run reach the per-file
    fan-out branch at judge_agent.py:170-173. Each file gets an AUTO_RISKY
    FileDiff + a TAKE_CURRENT FileDecisionRecord with a low confidence so
    the O-J1 / O-J3 short-circuits don't strip them out.
    """
    import git as _git

    from src.models.config import MergeConfig, OutputConfig
    from src.models.decision import (
        DecisionSource,
        FileDecisionRecord,
        MergeDecision,
    )
    from src.models.diff import FileDiff, FileStatus, RiskLevel
    from src.models.state import MergeState

    if not (tmp_path / ".git").exists():
        _git.Repo.init(str(tmp_path))
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        judge_skip_high_confidence=False,
        judge_skip_take_decisions=False,
    )
    state = MergeState(config=cfg)
    risky_count = risky_count if risky_count is not None else len(file_paths)
    for i, fp in enumerate(file_paths):
        risk = RiskLevel.AUTO_RISKY if i < risky_count else RiskLevel.AUTO_SAFE
        state.file_diffs.append(
            FileDiff(
                file_path=fp,
                file_status=FileStatus.MODIFIED,
                risk_level=risk,
                risk_score=0.5,
            )
        )
        state.file_decision_records[fp] = FileDecisionRecord(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.SEMANTIC_MERGE,
            decision_source=DecisionSource.AUTO_PLANNER,
            confidence=0.3,
            rationale="r",
        )
    return state


class TestJudgePerFileFanOut:
    """U-P3.5: ``judge_agent.py:170-173`` — per-file high-risk fan-out.

    Drives ``JudgeAgent.run`` end-to-end with a real MergeState so the
    assert call lands inside the production fan-out branch — deleting the
    assert line in src/ must surface here as ``spy.call_count == 0``.
    """

    async def test_judge_per_file_fan_out_passes_disjoint_assert(self, tmp_path):
        from src.agents import judge_agent
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        agent = JudgeAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )
        state = _build_judge_state(tmp_path, ["a.py", "b.py", "c.py"])

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        with (
            patch.object(judge_agent, "assert_disjoint_file_shards", spy),
            patch.object(
                JudgeAgent,
                "_run_deterministic_pipeline",
                return_value=[],
            ),
            patch.object(
                JudgeAgent,
                "review_file",
                new=AsyncMock(return_value=[]),
            ),
        ):
            await agent.run(state)

        assert spy.call_count >= 1
        shards = spy.call_args.args[0]
        # Three high-risk files → three single-element shards.
        assert sorted(s[0] for s in shards) == ["a.py", "b.py", "c.py"]
        assert all(len(s) == 1 for s in shards)


class TestJudgeChunkRunnerFanOut:
    """U-P3.6: ``judge_agent.py:1480-1483`` — chunked judge runner.

    Drives ``JudgeAgent.review_batch`` with >_BATCH_SIZE (=8) risky files so
    the chunking branch actually fires; spy must capture the assert that
    sits between ``chunks = [...]`` and ``chunk_runner.run_files(...)``.
    """

    async def test_judge_chunk_runner_passes_disjoint_assert(self, tmp_path):
        from src.agents import judge_agent
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        agent = JudgeAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )
        # 10 risky files → 2 chunks of size 8 + 2 (since _BATCH_SIZE=8).
        file_paths = [f"f{i}.py" for i in range(10)]
        state = _build_judge_state(tmp_path, file_paths)

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        with (
            patch.object(judge_agent, "assert_disjoint_file_shards", spy),
            patch.object(
                JudgeAgent,
                "_review_files_batch_llm",
                new=AsyncMock(return_value=[]),
            ),
        ):
            await agent.review_batch(layer_id=None, file_paths=file_paths, state=state)

        assert spy.call_count >= 1
        # Find the call site shape that matches review_batch's chunking
        # (list-of-list with ≥2 chunks, each elem a file path string).
        for call in spy.call_args_list:
            shards = call.args[0]
            if (
                isinstance(shards, list)
                and len(shards) >= 2
                and all(
                    isinstance(s, list) and all(isinstance(fp, str) for fp in s)
                    for s in shards
                )
            ):
                flat = [fp for shard in shards for fp in shard]
                assert len(set(flat)) == len(flat)
                assert set(flat) == set(file_paths)
                break
        else:
            raise AssertionError(
                "review_batch chunk runner did not invoke "
                "assert_disjoint_file_shards with the expected shape"
            )


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


def _build_conflict_state(tmp_path, file_paths):
    """Build a real MergeState that lets ConflictAnalystAgent.run reach the
    multi-file fan-out at conflict_analyst_agent.py:109. AUTO_RISKY plan
    phase + matching FileDiffs is enough; the agent's own restricted_view
    handles the rest.
    """
    from datetime import datetime

    import git as _git

    from src.models.config import MergeConfig, OutputConfig
    from src.models.diff import FileDiff, FileStatus, RiskLevel
    from src.models.plan import (
        MergePhase,
        MergePlan,
        PhaseFileBatch,
        RiskSummary,
    )
    from src.models.state import MergeState

    if not (tmp_path / ".git").exists():
        _git.Repo.init(str(tmp_path))
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
    )
    state = MergeState(config=cfg)
    state.thresholds = cfg.thresholds.model_copy()
    state.merge_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        merge_base_commit="abc123",
        phases=[
            PhaseFileBatch(
                batch_id="b1",
                phase=MergePhase.ANALYSIS,
                risk_level=RiskLevel.AUTO_RISKY,
                file_paths=list(file_paths),
            )
        ],
        risk_summary=RiskSummary(
            total_files=len(file_paths),
            auto_safe_count=0,
            auto_risky_count=len(file_paths),
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )
    for fp in file_paths:
        state.file_diffs.append(
            FileDiff(
                file_path=fp,
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_RISKY,
                risk_score=0.5,
            )
        )
    return state


class TestConflictAnalystMultiFileFanOut:
    """U-P3.8: ``conflict_analyst_agent.py:107-109`` — multi-file fan-out.

    Drives ``ConflictAnalystAgent.run`` end-to-end with a real MergeState
    so the assert call lands inside the production fan-out branch (delete
    the assert in src/ → ``spy.call_count == 0``). Two sub-tests:
      (a) clean keys → helper called, no raise
      (b) duplicate key in the plan phase → helper called, raise FileShardOverlap
    """

    async def test_clean_keys_pass(self, tmp_path):
        from src.agents import conflict_analyst_agent
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent
        from src.models.config import AgentLLMConfig

        agent = ConflictAnalystAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )
        state = _build_conflict_state(tmp_path, ["a.py", "b.py", "c.py"])

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        with (
            patch.object(conflict_analyst_agent, "assert_disjoint_file_shards", spy),
            patch.object(
                ConflictAnalystAgent,
                "analyze_file",
                new=AsyncMock(return_value=None),
            ),
        ):
            await agent.run(state)

        assert spy.call_count >= 1
        shards = spy.call_args.args[0]
        assert sorted(s[0] for s in shards) == ["a.py", "b.py", "c.py"]
        assert all(len(s) == 1 for s in shards)

    async def test_duplicate_key_raises(self, tmp_path):
        from src.agents import conflict_analyst_agent
        from src.agents.conflict_analyst_agent import ConflictAnalystAgent
        from src.models.config import AgentLLMConfig

        agent = ConflictAnalystAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )
        # ``file_paths`` carries duplicates → the assert in run() must trip
        # before any LLM fan-out fires.
        state = _build_conflict_state(tmp_path, ["a.py", "a.py", "b.py"])

        spy = MagicMock(wraps=assert_disjoint_file_shards)
        analyze_mock = AsyncMock(return_value=None)
        with (
            patch.object(conflict_analyst_agent, "assert_disjoint_file_shards", spy),
            patch.object(ConflictAnalystAgent, "analyze_file", new=analyze_mock),
        ):
            with pytest.raises(FileShardOverlap) as exc:
                await agent.run(state)
        assert spy.call_count == 1
        assert "a.py" in str(exc.value)
        analyze_mock.assert_not_called()
