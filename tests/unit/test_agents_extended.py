import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.config import AgentLLMConfig, MergeConfig
from src.models.state import MergeState, SystemStatus
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.decision import MergeDecision, FileDecisionRecord, DecisionSource
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.human import HumanDecisionRequest, DecisionOption
from src.models.judge import JudgeIssue, IssueSeverity, VerdictType, JudgeVerdict
from src.models.plan import MergePlan, MergePhase, PhaseFileBatch, RiskSummary
from src.models.plan_judge import PlanIssue
from src.core.read_only_state_view import ReadOnlyStateView


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")


def _make_state() -> MergeState:
    return MergeState(config=_make_config())


def _make_llm_config(
    provider: str = "anthropic", key_env: str = "TEST_KEY"
) -> AgentLLMConfig:
    return AgentLLMConfig(
        provider=provider,
        model="test-model",
        api_key_env=key_env,
        max_retries=1,
    )


def _make_file_diff(
    file_path: str = "src/main.py",
    risk_level: RiskLevel = RiskLevel.AUTO_SAFE,
    file_status: FileStatus = FileStatus.MODIFIED,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=file_status,
        risk_level=risk_level,
        risk_score=0.2,
        lines_added=10,
        lines_deleted=5,
        lines_changed=10,
    )


def _make_conflict_analysis(
    file_path: str = "src/main.py",
    conflict_type: ConflictType = ConflictType.CONCURRENT_MODIFICATION,
    confidence: float = 0.7,
) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path=file_path,
        conflict_points=[],
        overall_confidence=confidence,
        recommended_strategy=MergeDecision.ESCALATE_HUMAN,
        conflict_type=conflict_type,
        confidence=confidence,
    )


def _make_merge_plan(phases: list[PhaseFileBatch] | None = None) -> MergePlan:
    if phases is None:
        phases = []
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="abc123",
        phases=phases,
        risk_summary=RiskSummary(
            total_files=0,
            auto_safe_count=0,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.0,
        ),
        project_context_summary="test",
    )


def _make_human_request(
    file_path: str = "src/auth.py",
    human_decision: MergeDecision | None = None,
) -> HumanDecisionRequest:
    return HumanDecisionRequest(
        file_path=file_path,
        priority=5,
        conflict_points=[],
        context_summary="Test context",
        upstream_change_summary="Added feature X",
        fork_change_summary="Fixed bug Y",
        analyst_recommendation=MergeDecision.TAKE_TARGET,
        analyst_confidence=0.75,
        analyst_rationale="Upstream is more complete",
        options=[
            DecisionOption(
                option_key="A",
                decision=MergeDecision.TAKE_TARGET,
                description="Take upstream version",
            ),
            DecisionOption(
                option_key="B",
                decision=MergeDecision.TAKE_CURRENT,
                description="Keep fork version",
            ),
        ],
        created_at=datetime.now(),
        human_decision=human_decision,
    )


class TestConflictAnalystAgent:
    def setup_method(self):
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            from src.agents.conflict_analyst_agent import ConflictAnalystAgent

            self.agent = ConflictAnalystAgent(
                _make_llm_config(provider="anthropic", key_env="TEST_KEY")
            )

    def test_run_returns_skipped_message_when_no_plan(self):
        state = _make_state()
        assert state.merge_plan is None

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        from src.models.message import MessageType

        assert result.message_type == MessageType.PHASE_COMPLETED
        assert "skipped" in result.subject.lower()

    def test_run_processes_high_risk_files(self):
        state = _make_state()
        fd = _make_file_diff("src/auth.py", RiskLevel.HUMAN_REQUIRED)
        state._file_diffs = [fd]

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.HUMAN_REVIEW,
            file_paths=["src/auth.py"],
            risk_level=RiskLevel.HUMAN_REQUIRED,
        )
        state.merge_plan = _make_merge_plan([batch])

        mock_analysis = _make_conflict_analysis("src/auth.py")

        import asyncio

        with patch.object(
            self.agent, "analyze_file", new=AsyncMock(return_value=mock_analysis)
        ):
            result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.payload["analyzed_count"] == 1
        assert "src/auth.py" in state.conflict_analyses

    def test_run_skips_files_not_in_diff_map(self):
        state = _make_state()
        state._file_diffs = []

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.HUMAN_REVIEW,
            file_paths=["src/missing.py"],
            risk_level=RiskLevel.HUMAN_REQUIRED,
        )
        state.merge_plan = _make_merge_plan([batch])

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.payload["analyzed_count"] == 0

    def test_run_ignores_auto_safe_batches(self):
        state = _make_state()
        fd = _make_file_diff("src/utils.py", RiskLevel.AUTO_SAFE)
        state._file_diffs = [fd]

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["src/utils.py"],
            risk_level=RiskLevel.AUTO_SAFE,
        )
        state.merge_plan = _make_merge_plan([batch])

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.payload["analyzed_count"] == 0

    def test_run_uses_git_tool_for_three_way_diff(self):
        state = _make_state()
        state._merge_base = "abc123"
        fd = _make_file_diff("src/auth.py", RiskLevel.AUTO_RISKY)
        state._file_diffs = [fd]

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["src/auth.py"],
            risk_level=RiskLevel.AUTO_RISKY,
        )
        state.merge_plan = _make_merge_plan([batch])

        mock_git = MagicMock()
        mock_git.get_three_way_diff.return_value = ("base", "current", "target")
        self.agent.git_tool = mock_git

        mock_analysis = _make_conflict_analysis("src/auth.py")

        import asyncio

        with patch.object(
            self.agent, "analyze_file", new=AsyncMock(return_value=mock_analysis)
        ):
            asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        mock_git.get_three_way_diff.assert_called_once()

    def test_analyze_file_calls_llm_and_parses_result(self):
        fd = _make_file_diff("src/main.py")
        mock_analysis = _make_conflict_analysis("src/main.py")

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(return_value='{"test": true}'),
        ):
            with patch(
                "src.agents.conflict_analyst_agent.parse_conflict_analysis",
                return_value=mock_analysis,
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    self.agent.analyze_file(fd, None, None, None)
                )

        assert result.file_path == "src/main.py"

    def test_analyze_file_returns_fallback_on_llm_error(self):
        fd = _make_file_diff("src/main.py")

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("API error")),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_file(fd, None, None, None)
            )

        assert result.file_path == "src/main.py"
        assert result.recommended_strategy == MergeDecision.ESCALATE_HUMAN
        assert result.overall_confidence == 0.3

    def test_compute_confidence_adjusts_for_conflict_type(self):
        analysis = _make_conflict_analysis(
            confidence=0.8, conflict_type=ConflictType.SEMANTIC_EQUIVALENT
        )
        result = self.agent.compute_confidence(analysis, has_base_version=False)
        assert result > 0.8 * 0.85

    def test_compute_confidence_increases_with_base_version(self):
        analysis = _make_conflict_analysis(
            confidence=0.7, conflict_type=ConflictType.CONCURRENT_MODIFICATION
        )
        without_base = self.agent.compute_confidence(analysis, has_base_version=False)
        with_base = self.agent.compute_confidence(analysis, has_base_version=True)
        assert with_base > without_base

    def test_compute_confidence_clamped_to_0_10_to_0_95(self):
        analysis = _make_conflict_analysis(
            confidence=0.99, conflict_type=ConflictType.LOGIC_CONTRADICTION
        )
        result = self.agent.compute_confidence(analysis, has_base_version=False)
        assert 0.10 <= result <= 0.95

        analysis_low = _make_conflict_analysis(
            confidence=0.01, conflict_type=ConflictType.UNKNOWN
        )
        result_low = self.agent.compute_confidence(analysis_low, has_base_version=False)
        assert result_low >= 0.10

    def test_compute_confidence_all_conflict_types(self):
        for ct in ConflictType:
            analysis = _make_conflict_analysis(confidence=0.5, conflict_type=ct)
            result = self.agent.compute_confidence(analysis, has_base_version=True)
            assert 0.10 <= result <= 0.95

    def test_can_handle_returns_true_for_analyzing_conflicts_status(self):
        state = _make_state()
        state.status = SystemStatus.ANALYZING_CONFLICTS
        assert self.agent.can_handle(state) is True

    def test_can_handle_returns_false_for_other_status(self):
        state = _make_state()
        state.status = SystemStatus.PLANNING
        assert self.agent.can_handle(state) is False

    def test_analyze_conflict_point_delegates_to_analyze_file(self):
        fd = _make_file_diff("src/main.py")
        mock_analysis = _make_conflict_analysis("src/main.py")

        import asyncio

        with patch.object(
            self.agent, "analyze_file", new=AsyncMock(return_value=mock_analysis)
        ) as mock_af:
            asyncio.get_event_loop().run_until_complete(
                self.agent.analyze_conflict_point(fd, "hunk content", "context")
            )

        mock_af.assert_called_once_with(
            fd,
            base_content=None,
            current_content="hunk content",
            target_content=None,
            project_context="context",
        )


class TestExecutorAgent:
    def setup_method(self):
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            from src.agents.executor_agent import ExecutorAgent

            self.agent = ExecutorAgent(
                _make_llm_config(provider="openai", key_env="TEST_KEY")
            )

    def test_run_returns_error_when_no_plan(self):
        state = _make_state()
        assert state.merge_plan is None

        import asyncio
        from src.models.message import MessageType

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.message_type == MessageType.ERROR

    def test_run_processes_auto_safe_files(self):
        state = _make_state()
        fd = _make_file_diff("src/utils.py", RiskLevel.AUTO_SAFE)
        state._file_diffs = [fd]

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["src/utils.py"],
            risk_level=RiskLevel.AUTO_SAFE,
        )
        state.merge_plan = _make_merge_plan([batch])

        mock_record = FileDecisionRecord(
            file_path="src/utils.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="took target",
        )

        import asyncio

        with patch.object(
            self.agent, "execute_auto_merge", new=AsyncMock(return_value=mock_record)
        ):
            result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.payload["processed"] == 1
        assert "src/utils.py" in state.file_decision_records

    def test_run_skips_human_required_files(self):
        state = _make_state()
        fd = _make_file_diff("src/auth.py", RiskLevel.HUMAN_REQUIRED)
        state._file_diffs = [fd]

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.HUMAN_REVIEW,
            file_paths=["src/auth.py"],
            risk_level=RiskLevel.HUMAN_REQUIRED,
        )
        state.merge_plan = _make_merge_plan([batch])

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.payload["processed"] == 0

    def test_run_processes_deleted_only_with_skip_strategy(self):
        state = _make_state()
        fd = _make_file_diff("old.py", RiskLevel.DELETED_ONLY, FileStatus.DELETED)
        state._file_diffs = [fd]

        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["old.py"],
            risk_level=RiskLevel.DELETED_ONLY,
        )
        state.merge_plan = _make_merge_plan([batch])

        mock_record = FileDecisionRecord(
            file_path="old.py",
            file_status=FileStatus.DELETED,
            decision=MergeDecision.SKIP,
            decision_source=DecisionSource.AUTO_PLANNER,
            rationale="skipped",
        )

        import asyncio

        with patch.object(
            self.agent, "execute_auto_merge", new=AsyncMock(return_value=mock_record)
        ) as mock_exec:
            asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        call_args = mock_exec.call_args
        assert call_args[0][1] == MergeDecision.SKIP

    def test_execute_auto_merge_returns_escalate_when_no_git_tool(self):
        self.agent.git_tool = None
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE
        fd = _make_file_diff()

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_auto_merge(fd, MergeDecision.TAKE_TARGET, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_auto_merge_take_target_calls_git_tool(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE

        mock_git = MagicMock()
        mock_git.get_file_content.return_value = "target content"
        mock_git.repo_path = MagicMock()
        self.agent.git_tool = mock_git

        fd = _make_file_diff("src/utils.py")

        import asyncio

        with patch(
            "src.agents.executor_agent.apply_with_snapshot",
            new=AsyncMock(
                return_value=FileDecisionRecord(
                    file_path="src/utils.py",
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.TAKE_TARGET,
                    decision_source=DecisionSource.AUTO_EXECUTOR,
                    rationale="ok",
                )
            ),
        ):
            record = asyncio.get_event_loop().run_until_complete(
                self.agent.execute_auto_merge(fd, MergeDecision.TAKE_TARGET, state)
            )

        mock_git.get_file_content.assert_called_once_with(
            "upstream/main", "src/utils.py"
        )
        assert record.decision == MergeDecision.TAKE_TARGET

    def test_execute_auto_merge_take_target_escalates_when_content_none(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE

        mock_git = MagicMock()
        mock_git.get_file_content.return_value = None
        self.agent.git_tool = mock_git

        fd = _make_file_diff("src/utils.py")

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_auto_merge(fd, MergeDecision.TAKE_TARGET, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_auto_merge_take_current(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE

        mock_git = MagicMock()
        mock_git.get_file_content.return_value = "current content"
        mock_git.repo_path = MagicMock()
        self.agent.git_tool = mock_git

        fd = _make_file_diff("src/utils.py")

        import asyncio

        with patch(
            "src.agents.executor_agent.apply_with_snapshot",
            new=AsyncMock(
                return_value=FileDecisionRecord(
                    file_path="src/utils.py",
                    file_status=FileStatus.MODIFIED,
                    decision=MergeDecision.TAKE_CURRENT,
                    decision_source=DecisionSource.AUTO_EXECUTOR,
                    rationale="ok",
                )
            ),
        ):
            record = asyncio.get_event_loop().run_until_complete(
                self.agent.execute_auto_merge(fd, MergeDecision.TAKE_CURRENT, state)
            )

        mock_git.get_file_content.assert_called_once_with(
            "feature/fork", "src/utils.py"
        )
        assert record.decision == MergeDecision.TAKE_CURRENT

    def test_execute_auto_merge_skip_strategy(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE
        self.agent.git_tool = MagicMock()

        fd = _make_file_diff("old.py", RiskLevel.DELETED_ONLY, FileStatus.DELETED)

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_auto_merge(fd, MergeDecision.SKIP, state)
        )

        assert record.decision == MergeDecision.SKIP
        assert record.decision_source == DecisionSource.AUTO_PLANNER

    def test_execute_auto_merge_unsupported_strategy(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE
        self.agent.git_tool = MagicMock()

        fd = _make_file_diff()

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_auto_merge(fd, MergeDecision.SEMANTIC_MERGE, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_semantic_merge_returns_escalate_when_no_git_tool(self):
        self.agent.git_tool = None
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE
        fd = _make_file_diff()
        analysis = _make_conflict_analysis()

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_semantic_merge(fd, analysis, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_semantic_merge_escalates_when_contents_none(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE

        mock_git = MagicMock()
        mock_git.get_file_content.return_value = None
        self.agent.git_tool = mock_git

        fd = _make_file_diff()
        analysis = _make_conflict_analysis()

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_semantic_merge(fd, analysis, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_semantic_merge_calls_llm_and_applies(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE

        mock_git = MagicMock()
        mock_git.get_file_content.return_value = "file content"
        mock_git.repo_path = MagicMock()
        self.agent.git_tool = mock_git

        fd = _make_file_diff()
        analysis = _make_conflict_analysis()

        merged_record = FileDecisionRecord(
            file_path="src/main.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.SEMANTIC_MERGE,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="merged",
        )

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(return_value="merged content"),
        ):
            with patch(
                "src.agents.executor_agent.parse_merge_result",
                return_value="final content",
            ):
                with patch(
                    "src.agents.executor_agent.apply_with_snapshot",
                    new=AsyncMock(return_value=merged_record),
                ):
                    record = asyncio.get_event_loop().run_until_complete(
                        self.agent.execute_semantic_merge(fd, analysis, state)
                    )

        assert record.decision == MergeDecision.SEMANTIC_MERGE

    def test_execute_semantic_merge_escalates_on_llm_error(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE

        mock_git = MagicMock()
        mock_git.get_file_content.return_value = "content"
        self.agent.git_tool = mock_git

        fd = _make_file_diff()
        analysis = _make_conflict_analysis()

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("LLM error")),
        ):
            record = asyncio.get_event_loop().run_until_complete(
                self.agent.execute_semantic_merge(fd, analysis, state)
            )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_human_decision_escalates_when_no_decision(self):
        state = _make_state()
        state.current_phase = MergePhase.HUMAN_REVIEW
        request = _make_human_request(human_decision=None)

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_human_decision(request, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_human_decision_manual_patch_without_content(self):
        state = _make_state()
        state.current_phase = MergePhase.HUMAN_REVIEW
        request = _make_human_request(human_decision=MergeDecision.MANUAL_PATCH)

        import asyncio

        record = asyncio.get_event_loop().run_until_complete(
            self.agent.execute_human_decision(request, state)
        )

        assert record.decision == MergeDecision.ESCALATE_HUMAN

    def test_execute_human_decision_manual_patch_applies_content(self):
        state = _make_state()
        state.current_phase = MergePhase.HUMAN_REVIEW
        request = _make_human_request(human_decision=MergeDecision.MANUAL_PATCH)
        request = request.model_copy(
            update={"custom_content": "patched content", "reviewer_notes": "manual fix"}
        )

        mock_git = MagicMock()
        mock_git.repo_path = MagicMock()
        self.agent.git_tool = mock_git

        applied_record = FileDecisionRecord(
            file_path="src/auth.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.MANUAL_PATCH,
            decision_source=DecisionSource.HUMAN,
            rationale="applied",
        )

        import asyncio

        with patch(
            "src.agents.executor_agent.apply_with_snapshot",
            new=AsyncMock(return_value=applied_record),
        ):
            record = asyncio.get_event_loop().run_until_complete(
                self.agent.execute_human_decision(request, state)
            )

        assert record.decision == MergeDecision.MANUAL_PATCH

    def test_execute_human_decision_delegates_to_auto_merge(self):
        state = _make_state()
        state.current_phase = MergePhase.HUMAN_REVIEW

        fd = _make_file_diff("src/auth.py")
        state._file_diffs = [fd]

        request = _make_human_request(
            "src/auth.py", human_decision=MergeDecision.TAKE_TARGET
        )

        auto_record = FileDecisionRecord(
            file_path="src/auth.py",
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="ok",
        )

        import asyncio

        with patch.object(
            self.agent, "execute_auto_merge", new=AsyncMock(return_value=auto_record)
        ):
            record = asyncio.get_event_loop().run_until_complete(
                self.agent.execute_human_decision(request, state)
            )

        assert record.decision_source == DecisionSource.HUMAN

    def test_raise_plan_dispute_appends_to_state(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE
        fd = _make_file_diff("src/auth.py", RiskLevel.AUTO_SAFE)

        dispute = self.agent.raise_plan_dispute(
            fd,
            "Security file misclassified",
            {"src/auth.py": RiskLevel.HUMAN_REQUIRED},
            "Auth file should require human review",
            state,
        )

        assert len(state.plan_disputes) == 1
        assert dispute.dispute_reason == "Security file misclassified"

    def test_raise_plan_dispute_does_not_change_risk_classification(self):
        state = _make_state()
        state.current_phase = MergePhase.AUTO_MERGE
        state.file_classifications["src/auth.py"] = RiskLevel.AUTO_SAFE
        fd = _make_file_diff("src/auth.py", RiskLevel.AUTO_SAFE)

        self.agent.raise_plan_dispute(
            fd,
            "Misclassified",
            {"src/auth.py": RiskLevel.HUMAN_REQUIRED},
            "Should be human required",
            state,
        )

        assert state.file_classifications["src/auth.py"] == RiskLevel.AUTO_SAFE

    def test_can_handle_returns_true_for_auto_merging(self):
        state = _make_state()
        state.status = SystemStatus.AUTO_MERGING
        assert self.agent.can_handle(state) is True

    def test_can_handle_returns_true_for_analyzing_conflicts(self):
        state = _make_state()
        state.status = SystemStatus.ANALYZING_CONFLICTS
        assert self.agent.can_handle(state) is True

    def test_can_handle_returns_false_for_other_status(self):
        state = _make_state()
        state.status = SystemStatus.AWAITING_HUMAN
        assert self.agent.can_handle(state) is False


class TestHumanInterfaceAgent:
    def setup_method(self):
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            from src.agents.human_interface_agent import HumanInterfaceAgent

            self.agent = HumanInterfaceAgent(
                _make_llm_config(provider="anthropic", key_env="TEST_KEY")
            )

    def test_run_returns_pending_count(self):
        state = _make_state()
        req1 = _make_human_request("src/a.py", human_decision=None)
        req2 = _make_human_request("src/b.py", human_decision=MergeDecision.TAKE_TARGET)
        state.human_decision_requests = {"src/a.py": req1, "src/b.py": req2}

        import asyncio
        from src.models.message import MessageType

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.message_type == MessageType.HUMAN_INPUT_NEEDED
        assert result.payload["pending_count"] == 1

    def test_run_with_no_pending_requests(self):
        state = _make_state()

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.payload["pending_count"] == 0

    def test_generate_report_writes_markdown_file(self, tmp_path):
        req = _make_human_request("src/auth.py")
        output_path = str(tmp_path / "report.md")

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            self.agent.generate_report([req], output_path)
        )

        assert result == output_path
        content = (tmp_path / "report.md").read_text()
        assert "src/auth.py" in content
        assert "Human Decision Report" in content

    def test_generate_report_creates_parent_dirs(self, tmp_path):
        req = _make_human_request("src/auth.py")
        output_path = str(tmp_path / "nested" / "dir" / "report.md")

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            self.agent.generate_report([req], output_path)
        )

        assert (tmp_path / "nested" / "dir" / "report.md").exists()

    def test_generate_report_includes_all_request_info(self, tmp_path):
        req = _make_human_request("src/core.py")
        output_path = str(tmp_path / "report.md")

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            self.agent.generate_report([req], output_path)
        )

        content = (tmp_path / "report.md").read_text()
        assert "Test context" in content
        assert "Added feature X" in content
        assert "Fixed bug Y" in content
        assert "Upstream is more complete" in content

    def test_generate_report_with_risk_warning(self, tmp_path):
        req = _make_human_request("src/secure.py")
        req = req.model_copy(
            update={
                "options": [
                    DecisionOption(
                        option_key="A",
                        decision=MergeDecision.TAKE_TARGET,
                        description="Take upstream",
                        risk_warning="This may break authentication",
                    )
                ]
            }
        )
        output_path = str(tmp_path / "report.md")

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            self.agent.generate_report([req], output_path)
        )

        content = (tmp_path / "report.md").read_text()
        assert "Take upstream" in content
        assert "take_target" in content

    def test_collect_decisions_file_loads_yaml(self, tmp_path):
        req = _make_human_request("src/main.py")
        yaml_content = (
            "decisions:\n"
            "  - file_path: src/main.py\n"
            "    decision: take_target\n"
            "    reviewer_name: Alice\n"
            "    reviewer_notes: Looks good\n"
        )
        yaml_file = tmp_path / "decisions.yaml"
        yaml_file.write_text(yaml_content)

        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            self.agent.collect_decisions_file(str(yaml_file), [req])
        )

        assert results[0].human_decision == MergeDecision.TAKE_TARGET
        assert results[0].reviewer_name == "Alice"
        assert results[0].reviewer_notes == "Looks good"

    def test_collect_decisions_file_raises_when_not_found(self):
        import asyncio

        with pytest.raises(FileNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                self.agent.collect_decisions_file("/nonexistent/path.yaml", [])
            )

    def test_collect_decisions_file_skips_invalid_decision(self, tmp_path):
        req = _make_human_request("src/main.py")
        yaml_content = (
            "decisions:\n  - file_path: src/main.py\n    decision: not_valid_decision\n"
        )
        yaml_file = tmp_path / "decisions.yaml"
        yaml_file.write_text(yaml_content)

        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            self.agent.collect_decisions_file(str(yaml_file), [req])
        )

        assert results[0].human_decision is None

    def test_collect_decisions_file_skips_escalate_human(self, tmp_path):
        req = _make_human_request("src/main.py")
        yaml_content = (
            "decisions:\n  - file_path: src/main.py\n    decision: escalate_human\n"
        )
        yaml_file = tmp_path / "decisions.yaml"
        yaml_file.write_text(yaml_content)

        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            self.agent.collect_decisions_file(str(yaml_file), [req])
        )

        assert results[0].human_decision is None

    def test_collect_decisions_file_skips_manual_patch_without_content(self, tmp_path):
        req = _make_human_request("src/main.py")
        yaml_content = (
            "decisions:\n  - file_path: src/main.py\n    decision: manual_patch\n"
        )
        yaml_file = tmp_path / "decisions.yaml"
        yaml_file.write_text(yaml_content)

        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            self.agent.collect_decisions_file(str(yaml_file), [req])
        )

        assert results[0].human_decision is None

    def test_validate_decision_returns_false_when_none(self):
        req = _make_human_request(human_decision=None)
        assert self.agent.validate_decision(req) is False

    def test_validate_decision_returns_false_for_manual_patch_without_content(self):
        req = _make_human_request(human_decision=MergeDecision.MANUAL_PATCH)
        assert self.agent.validate_decision(req) is False

    def test_validate_decision_returns_true_for_valid_decision(self):
        req = _make_human_request(human_decision=MergeDecision.TAKE_TARGET)
        assert self.agent.validate_decision(req) is True

    def test_validate_decision_returns_false_for_escalate_human(self):
        req = _make_human_request(human_decision=MergeDecision.ESCALATE_HUMAN)
        assert self.agent.validate_decision(req) is False

    def test_validate_decision_option_valid_key(self):
        req = _make_human_request()
        assert self.agent.validate_decision_option(req, "A") is True

    def test_validate_decision_option_invalid_key(self):
        req = _make_human_request()
        assert self.agent.validate_decision_option(req, "Z") is False

    def test_can_handle_returns_true_for_awaiting_human(self):
        state = _make_state()
        state.status = SystemStatus.AWAITING_HUMAN
        assert self.agent.can_handle(state) is True

    def test_can_handle_returns_false_for_other_status(self):
        state = _make_state()
        state.status = SystemStatus.PLANNING
        assert self.agent.can_handle(state) is False


class TestPlannerAgent:
    def setup_method(self):
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            from src.agents.planner_agent import PlannerAgent

            self.agent = PlannerAgent(
                _make_llm_config(provider="anthropic", key_env="TEST_KEY")
            )

    def test_run_sets_merge_plan_on_state(self):
        state = _make_state()
        state._file_diffs = [_make_file_diff("src/utils.py", RiskLevel.AUTO_SAFE)]

        plan_json = json.dumps(
            {
                "phases": [
                    {
                        "batch_id": "b1",
                        "phase": "auto_merge",
                        "file_paths": ["src/utils.py"],
                        "risk_level": "auto_safe",
                        "can_parallelize": True,
                    }
                ],
                "risk_summary": {
                    "total_files": 1,
                    "auto_safe_count": 1,
                    "auto_risky_count": 0,
                    "human_required_count": 0,
                    "deleted_only_count": 0,
                    "binary_count": 0,
                    "excluded_count": 0,
                    "estimated_auto_merge_rate": 1.0,
                    "top_risk_files": [],
                },
                "project_context_summary": "test project",
                "special_instructions": [],
            }
        )

        import asyncio

        with patch.object(
            self.agent, "_call_llm_with_retry", new=AsyncMock(return_value=plan_json)
        ):
            asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert state.merge_plan is not None
        assert len(state.file_classifications) == 1

    def test_run_returns_phase_completed_message(self):
        state = _make_state()
        state._file_diffs = []

        plan_json = json.dumps(
            {
                "phases": [],
                "risk_summary": {
                    "total_files": 0,
                    "auto_safe_count": 0,
                    "auto_risky_count": 0,
                    "human_required_count": 0,
                    "deleted_only_count": 0,
                    "binary_count": 0,
                    "excluded_count": 0,
                    "estimated_auto_merge_rate": 0.0,
                    "top_risk_files": [],
                },
                "project_context_summary": "",
                "special_instructions": [],
            }
        )

        from src.models.message import MessageType
        import asyncio

        with patch.object(
            self.agent, "_call_llm_with_retry", new=AsyncMock(return_value=plan_json)
        ):
            result = asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert result.message_type == MessageType.PHASE_COMPLETED

    def test_generate_plan_falls_back_on_llm_error(self):
        state = _make_state()
        state._file_diffs = [_make_file_diff("src/utils.py", RiskLevel.AUTO_SAFE)]

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("LLM error")),
        ):
            asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert state.merge_plan is not None

    def test_generate_plan_handles_json_in_code_block(self):
        state = _make_state()
        state._file_diffs = []

        inner = json.dumps(
            {
                "phases": [],
                "risk_summary": {
                    "total_files": 0,
                    "auto_safe_count": 0,
                    "auto_risky_count": 0,
                    "human_required_count": 0,
                    "deleted_only_count": 0,
                    "binary_count": 0,
                    "excluded_count": 0,
                    "estimated_auto_merge_rate": 0.0,
                    "top_risk_files": [],
                },
                "project_context_summary": "",
                "special_instructions": [],
            }
        )
        wrapped = f"```json\n{inner}\n```"

        import asyncio

        with patch.object(
            self.agent, "_call_llm_with_retry", new=AsyncMock(return_value=wrapped)
        ):
            asyncio.get_event_loop().run_until_complete(self.agent.run(state))

        assert state.merge_plan is not None

    def test_create_fallback_plan_data_all_risk_levels(self):
        file_diffs = [
            _make_file_diff("a.py", RiskLevel.AUTO_SAFE),
            _make_file_diff("b.py", RiskLevel.AUTO_RISKY),
            _make_file_diff("c.py", RiskLevel.HUMAN_REQUIRED),
            _make_file_diff("d.py", RiskLevel.DELETED_ONLY),
            _make_file_diff("e.png", RiskLevel.BINARY),
        ]

        result = self.agent._create_fallback_plan_data(file_diffs)

        risk_summary = result["risk_summary"]
        assert risk_summary["total_files"] == 5
        assert risk_summary["auto_safe_count"] == 1
        assert risk_summary["auto_risky_count"] == 1
        assert risk_summary["human_required_count"] == 1
        assert risk_summary["deleted_only_count"] == 1
        assert risk_summary["binary_count"] == 1

    def test_create_fallback_plan_data_empty(self):
        result = self.agent._create_fallback_plan_data([])

        assert result["risk_summary"]["total_files"] == 0
        assert result["risk_summary"]["estimated_auto_merge_rate"] == 0.0

    def test_create_fallback_plan_data_auto_merge_rate(self):
        file_diffs = [
            _make_file_diff("a.py", RiskLevel.AUTO_SAFE),
            _make_file_diff("b.py", RiskLevel.DELETED_ONLY),
            _make_file_diff("c.py", RiskLevel.HUMAN_REQUIRED),
        ]

        result = self.agent._create_fallback_plan_data(file_diffs)
        rate = result["risk_summary"]["estimated_auto_merge_rate"]
        assert abs(rate - 2 / 3) < 0.001

    def test_build_merge_plan_with_invalid_risk_level(self):
        state = _make_state()
        plan_data = {
            "phases": [
                {
                    "batch_id": "b1",
                    "phase": "invalid_phase",
                    "file_paths": ["a.py"],
                    "risk_level": "invalid_level",
                    "can_parallelize": True,
                }
            ],
            "risk_summary": {
                "total_files": 1,
                "auto_safe_count": 0,
                "auto_risky_count": 0,
                "human_required_count": 0,
                "deleted_only_count": 0,
                "binary_count": 0,
                "excluded_count": 0,
                "estimated_auto_merge_rate": 0.0,
            },
            "project_context_summary": "",
            "special_instructions": [],
        }

        plan = self.agent._build_merge_plan(plan_data, state, [])

        assert plan.phases[0].risk_level == RiskLevel.AUTO_SAFE
        assert plan.phases[0].phase == MergePhase.AUTO_MERGE

    def test_revise_plan_raises_when_no_plan(self):
        state = _make_state()
        assert state.merge_plan is None

        import asyncio

        with pytest.raises(ValueError, match="No existing plan"):
            asyncio.get_event_loop().run_until_complete(
                self.agent.revise_plan(state, [])
            )

    def test_revise_plan_with_llm_response(self):
        state = _make_state()
        state.merge_plan = _make_merge_plan()

        plan_json = json.dumps(
            {
                "phases": [],
                "risk_summary": {
                    "total_files": 0,
                    "auto_safe_count": 0,
                    "auto_risky_count": 0,
                    "human_required_count": 0,
                    "deleted_only_count": 0,
                    "binary_count": 0,
                    "excluded_count": 0,
                    "estimated_auto_merge_rate": 0.0,
                    "top_risk_files": [],
                },
                "project_context_summary": "revised",
                "special_instructions": [],
            }
        )

        issue = PlanIssue(
            file_path="src/auth.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.HUMAN_REQUIRED,
            reason="Security sensitive",
            issue_type="risk_underestimated",
        )

        import asyncio

        with patch.object(
            self.agent, "_call_llm_with_retry", new=AsyncMock(return_value=plan_json)
        ):
            revised = asyncio.get_event_loop().run_until_complete(
                self.agent.revise_plan(state, [issue])
            )

        assert revised is not None

    def test_revise_plan_falls_back_on_llm_error(self):
        state = _make_state()
        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["src/utils.py"],
            risk_level=RiskLevel.AUTO_SAFE,
        )
        state.merge_plan = _make_merge_plan([batch])
        state.file_classifications["src/utils.py"] = RiskLevel.AUTO_SAFE

        issue = PlanIssue(
            file_path="src/utils.py",
            current_classification=RiskLevel.AUTO_SAFE,
            suggested_classification=RiskLevel.HUMAN_REQUIRED,
            reason="Risky",
            issue_type="risk_underestimated",
        )

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("LLM error")),
        ):
            revised = asyncio.get_event_loop().run_until_complete(
                self.agent.revise_plan(state, [issue])
            )

        assert revised is not None
        human_required_paths = [
            fp
            for batch in revised.phases
            if batch.risk_level == RiskLevel.HUMAN_REQUIRED
            for fp in batch.file_paths
        ]
        assert "src/utils.py" in human_required_paths

    def test_handle_dispute_creates_plan_issues(self):
        state = _make_state()
        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["src/auth.py"],
            risk_level=RiskLevel.AUTO_SAFE,
        )
        state.merge_plan = _make_merge_plan([batch])
        state.file_classifications["src/auth.py"] = RiskLevel.AUTO_SAFE

        from src.models.dispute import PlanDisputeRequest

        dispute = PlanDisputeRequest(
            raised_by="executor",
            phase="auto_merge",
            disputed_files=["src/auth.py"],
            dispute_reason="Security file",
            suggested_reclassification={"src/auth.py": RiskLevel.HUMAN_REQUIRED},
            impact_assessment="High impact",
        )

        import asyncio

        plan_json = json.dumps(
            {
                "phases": [],
                "risk_summary": {
                    "total_files": 0,
                    "auto_safe_count": 0,
                    "auto_risky_count": 0,
                    "human_required_count": 0,
                    "deleted_only_count": 0,
                    "binary_count": 0,
                    "excluded_count": 0,
                    "estimated_auto_merge_rate": 0.0,
                    "top_risk_files": [],
                },
                "project_context_summary": "",
                "special_instructions": [],
            }
        )
        with patch.object(
            self.agent, "_call_llm_with_retry", new=AsyncMock(return_value=plan_json)
        ):
            revised = asyncio.get_event_loop().run_until_complete(
                self.agent.handle_dispute(state, dispute)
            )

        assert revised is not None

    def test_can_handle_planning_status(self):
        state = _make_state()
        state.status = SystemStatus.PLANNING
        assert self.agent.can_handle(state) is True

    def test_can_handle_plan_revising_status(self):
        state = _make_state()
        state.status = SystemStatus.PLAN_REVISING
        assert self.agent.can_handle(state) is True

    def test_can_handle_returns_false_for_other_status(self):
        state = _make_state()
        state.status = SystemStatus.COMPLETED
        assert self.agent.can_handle(state) is False


class TestJudgeAgent:
    def setup_method(self):
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            from src.agents.judge_agent import JudgeAgent

            self.agent = JudgeAgent(
                _make_llm_config(provider="anthropic", key_env="TEST_KEY")
            )

    def _make_decision_record(
        self, file_path: str = "src/auth.py"
    ) -> FileDecisionRecord:
        return FileDecisionRecord(
            file_path=file_path,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="auto merged",
        )

    def test_run_with_no_decision_records(self):
        state = _make_state()
        readonly = ReadOnlyStateView(state)

        verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=0,
            passed_files=[],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=1.0,
            summary="No files reviewed",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test-model",
        )

        import asyncio

        with patch.object(
            self.agent, "_compute_final_verdict", new=AsyncMock(return_value=verdict)
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent.run(readonly)
            )

        from src.models.message import MessageType

        assert result.message_type == MessageType.PHASE_COMPLETED

    def test_run_reviews_high_risk_files(self):
        state = _make_state()
        fd = _make_file_diff("src/auth.py", RiskLevel.HUMAN_REQUIRED)
        state._file_diffs = [fd]

        record = self._make_decision_record("src/auth.py")
        state.file_decision_records["src/auth.py"] = record

        readonly = ReadOnlyStateView(state)

        verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=1,
            passed_files=["src/auth.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.9,
            summary="All good",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test-model",
        )

        import asyncio

        with patch.object(self.agent, "review_file", new=AsyncMock(return_value=[])):
            with patch.object(
                self.agent,
                "_compute_final_verdict",
                new=AsyncMock(return_value=verdict),
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    self.agent.run(readonly)
                )

        assert result.payload["verdict"]["verdict"] == "pass"

    def test_run_reviews_security_sensitive_files(self):
        state = _make_state()
        fd = _make_file_diff("src/utils.py", RiskLevel.AUTO_SAFE)
        fd = fd.model_copy(update={"is_security_sensitive": True})
        state._file_diffs = [fd]

        record = self._make_decision_record("src/utils.py")
        state.file_decision_records["src/utils.py"] = record

        readonly = ReadOnlyStateView(state)

        verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=1,
            passed_files=["src/utils.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.9,
            summary="All good",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test-model",
        )

        import asyncio

        with patch.object(
            self.agent, "review_file", new=AsyncMock(return_value=[])
        ) as mock_review:
            with patch.object(
                self.agent,
                "_compute_final_verdict",
                new=AsyncMock(return_value=verdict),
            ):
                asyncio.get_event_loop().run_until_complete(self.agent.run(readonly))

        mock_review.assert_called_once()

    def test_review_file_detects_conflict_markers(self):
        fd = _make_file_diff("src/main.py")
        record = self._make_decision_record("src/main.py")
        merged_content = "code\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"

        import asyncio

        with patch.object(
            self.agent, "_call_llm_with_retry", new=AsyncMock(return_value="[]")
        ):
            with patch(
                "src.agents.judge_agent.parse_file_review_issues", return_value=[]
            ):
                issues = asyncio.get_event_loop().run_until_complete(
                    self.agent.review_file("src/main.py", merged_content, record, fd)
                )

        assert any(i.issue_type == "unresolved_conflict" for i in issues)
        assert any(i.issue_level == IssueSeverity.CRITICAL for i in issues)

    def test_review_file_returns_empty_on_llm_error(self):
        fd = _make_file_diff("src/main.py")
        record = self._make_decision_record("src/main.py")

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("error")),
        ):
            issues = asyncio.get_event_loop().run_until_complete(
                self.agent.review_file("src/main.py", "clean content", record, fd)
            )

        assert issues == []

    def test_compute_verdict_pass_when_no_issues(self):
        result = self.agent.compute_verdict([])
        assert result == VerdictType.PASS

    def test_compute_verdict_fail_on_critical_issue(self):
        issues = [
            JudgeIssue(
                file_path="src/auth.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="security_vulnerability",
                description="Critical issue found",
                must_fix_before_merge=True,
            )
        ]
        result = self.agent.compute_verdict(issues)
        assert result == VerdictType.FAIL

    def test_compute_verdict_fail_on_high_issue(self):
        issues = [
            JudgeIssue(
                file_path="src/auth.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="logic_error",
                description="High severity issue",
            )
        ]
        result = self.agent.compute_verdict(issues)
        assert result == VerdictType.FAIL

    def test_compute_verdict_conditional_on_medium_issues(self):
        issues = [
            JudgeIssue(
                file_path="src/utils.py",
                issue_level=IssueSeverity.MEDIUM,
                issue_type="style_issue",
                description="Medium issue",
            )
        ]
        result = self.agent.compute_verdict(issues)
        assert result == VerdictType.CONDITIONAL

    def test_compute_final_verdict_uses_llm(self):
        verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=2,
            passed_files=["a.py", "b.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.95,
            summary="All files passed",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test-model",
        )

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(return_value='{"verdict": "pass"}'),
        ):
            with patch(
                "src.agents.judge_agent.parse_judge_verdict", return_value=verdict
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    self.agent._compute_final_verdict(["a.py", "b.py"], [])
                )

        assert result.verdict == VerdictType.PASS

    def test_compute_final_verdict_falls_back_on_llm_error(self):
        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("error")),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent._compute_final_verdict(["a.py"], [])
            )

        assert result is not None
        assert result.verdict == VerdictType.PASS

    def test_compute_final_verdict_falls_back_with_critical_issues(self):
        issues = [
            JudgeIssue(
                file_path="src/auth.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="security",
                description="Critical",
                must_fix_before_merge=True,
            )
        ]

        import asyncio

        with patch.object(
            self.agent,
            "_call_llm_with_retry",
            new=AsyncMock(side_effect=RuntimeError("error")),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                self.agent._compute_final_verdict(["src/auth.py"], issues)
            )

        assert result.verdict == VerdictType.FAIL

    def test_can_handle_judge_reviewing_status(self):
        state = _make_state()
        state.status = SystemStatus.JUDGE_REVIEWING
        assert self.agent.can_handle(state) is True

    def test_can_handle_returns_false_for_other_status(self):
        state = _make_state()
        state.status = SystemStatus.PLANNING
        assert self.agent.can_handle(state) is False

    def test_run_reads_file_from_git_tool(self):
        state = _make_state()
        fd = _make_file_diff("src/auth.py", RiskLevel.HUMAN_REQUIRED)
        state._file_diffs = [fd]

        record = self._make_decision_record("src/auth.py")
        state.file_decision_records["src/auth.py"] = record

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "merged content"

        mock_git = MagicMock()
        mock_git.repo_path.__truediv__ = MagicMock(return_value=mock_path)
        self.agent.git_tool = mock_git

        readonly = ReadOnlyStateView(state)

        verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=1,
            passed_files=["src/auth.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.9,
            summary="All good",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test-model",
        )

        import asyncio

        with patch.object(self.agent, "review_file", new=AsyncMock(return_value=[])):
            with patch.object(
                self.agent,
                "_compute_final_verdict",
                new=AsyncMock(return_value=verdict),
            ):
                asyncio.get_event_loop().run_until_complete(self.agent.run(readonly))

        mock_git.repo_path.__truediv__.assert_called_with("src/auth.py")
