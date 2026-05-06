"""Tests for P1: Customization protection, Judge-Executor repair loop, Gate system."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.config import (
    CustomizationEntry,
    CustomizationVerification,
    GateCommandConfig,
    GateConfig,
    MergeConfig,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.judge import (
    CustomizationViolation,
    ExecutorRebuttal,
    IssueSeverity,
    JudgeIssue,
    JudgeVerdict,
    RepairInstruction,
    VerdictType,
    VETO_CONDITIONS,
)
from src.models.state import MergeState, SystemStatus
from src.core.state_machine import StateMachine, VALID_TRANSITIONS


class TestCustomizationModels:
    def test_customization_entry(self):
        entry = CustomizationEntry(
            name="HTTP-only Cookie Auth",
            description="Replace localStorage with cookies",
            files=["api/auth/login.py", "web/service/base.ts"],
            verification=[
                CustomizationVerification(
                    type="grep",
                    pattern="set_cookie|csrf_token",
                    files=["api/auth/**"],
                ),
                CustomizationVerification(
                    type="file_exists",
                    files=["api/auth/login.py"],
                ),
            ],
        )
        assert entry.name == "HTTP-only Cookie Auth"
        assert len(entry.verification) == 2
        assert entry.verification[0].type == "grep"

    def test_customization_in_config(self):
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            customizations=[
                CustomizationEntry(
                    name="SSO",
                    files=["api/auth/sso.py"],
                    verification=[
                        CustomizationVerification(
                            type="grep",
                            pattern="keycloak",
                            files=["api/auth/**"],
                        )
                    ],
                )
            ],
        )
        assert len(config.customizations) == 1
        assert config.customizations[0].name == "SSO"


class TestRepairInstructionModel:
    def test_repair_instruction(self):
        ri = RepairInstruction(
            file_path="src/foo.py",
            instruction="Add missing upstream function bar()",
            severity=IssueSeverity.HIGH,
            is_repairable=True,
            source_issue_id="issue-123",
        )
        assert ri.is_repairable
        assert ri.severity == IssueSeverity.HIGH

    def test_non_repairable_instruction(self):
        ri = RepairInstruction(
            file_path="src/auth.py",
            instruction="Logic contradiction requires human judgment",
            is_repairable=False,
        )
        assert not ri.is_repairable


class TestJudgeVerdictExtensions:
    def test_verdict_with_veto(self):
        v = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=5,
            passed_files=[],
            failed_files=["a.py"],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.3,
            summary="veto triggered",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
            veto_triggered=True,
            veto_reason="B-class file differs from upstream",
        )
        assert v.veto_triggered
        assert v.veto_reason is not None

    def test_verdict_with_repair_instructions(self):
        v = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=["a.py"],
            conditional_files=[],
            issues=[],
            critical_issues_count=1,
            high_issues_count=0,
            overall_confidence=0.4,
            summary="needs repair",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
            repair_instructions=[
                RepairInstruction(
                    file_path="a.py",
                    instruction="fix syntax",
                    is_repairable=True,
                )
            ],
        )
        assert len(v.repair_instructions) == 1

    def test_verdict_backward_compat(self):
        v = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=0,
            passed_files=[],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=1.0,
            summary="ok",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )
        assert not v.veto_triggered
        assert v.repair_instructions == []
        assert v.customization_violations == []


class TestVetoConditions:
    def test_veto_conditions_defined(self):
        assert len(VETO_CONDITIONS) >= 6
        assert "B-class file differs from upstream" in VETO_CONDITIONS


class TestStateMachineRepairLoop:
    def test_judge_to_auto_merging_allowed(self):
        sm = StateMachine()
        assert sm.can_transition(
            SystemStatus.JUDGE_REVIEWING, SystemStatus.AUTO_MERGING
        )

    def test_judge_to_generating_report_allowed(self):
        sm = StateMachine()
        assert sm.can_transition(
            SystemStatus.JUDGE_REVIEWING, SystemStatus.GENERATING_REPORT
        )

    def test_judge_to_awaiting_human_allowed(self):
        sm = StateMachine()
        assert sm.can_transition(
            SystemStatus.JUDGE_REVIEWING, SystemStatus.AWAITING_HUMAN
        )


class TestMergeStateP1Fields:
    def test_state_has_repair_fields(self):
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        assert state.judge_repair_rounds == 0
        assert state.judge_verdicts_log == []
        assert state.gate_baselines == {}
        assert state.gate_history == []
        assert state.consecutive_gate_failures == 0


class TestJudgeVerifyCustomizations:
    def test_verify_grep_passes(self, tmp_path):
        (tmp_path / "auth").mkdir()
        (tmp_path / "auth" / "login.py").write_text(
            "def login():\n    set_cookie('token')\n"
        )

        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.grep_in_files.return_value = {
            "auth/login.py": ["set_cookie"],
        }

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        customizations = [
            CustomizationEntry(
                name="Cookie Auth",
                verification=[
                    CustomizationVerification(
                        type="grep",
                        pattern="set_cookie",
                        files=["auth/*"],
                    )
                ],
            )
        ]

        violations = judge.verify_customizations(customizations)
        assert violations == []

    def test_verify_grep_fails(self, tmp_path):
        (tmp_path / "auth").mkdir()
        (tmp_path / "auth" / "login.py").write_text("def login():\n    pass\n")

        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.grep_in_files.return_value = {}

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        customizations = [
            CustomizationEntry(
                name="Cookie Auth",
                verification=[
                    CustomizationVerification(
                        type="grep",
                        pattern="set_cookie",
                        files=["auth/*"],
                    )
                ],
            )
        ]

        violations = judge.verify_customizations(customizations)
        assert len(violations) == 1
        assert violations[0].customization_name == "Cookie Auth"
        assert violations[0].match_count == 0

    def test_verify_file_exists_passes(self, tmp_path):
        (tmp_path / "auth.py").write_text("x = 1")

        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        customizations = [
            CustomizationEntry(
                name="Auth Module",
                verification=[
                    CustomizationVerification(
                        type="file_exists",
                        files=["auth.py"],
                    )
                ],
            )
        ]

        violations = judge.verify_customizations(customizations)
        assert violations == []

    def test_verify_file_exists_fails(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        customizations = [
            CustomizationEntry(
                name="Missing Module",
                verification=[
                    CustomizationVerification(
                        type="file_exists",
                        files=["nonexistent.py"],
                    )
                ],
            )
        ]

        violations = judge.verify_customizations(customizations)
        assert len(violations) == 1
        assert violations[0].customization_name == "Missing Module"

    def test_no_customizations_no_violations(self):
        from src.agents.judge_agent import JudgeAgent

        git_tool = MagicMock()
        git_tool.repo_path = Path("/tmp")

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        assert judge.verify_customizations([]) == []


class TestJudgeBuildRepairInstructions:
    def test_builds_from_must_fix_issues(self):
        from src.agents.judge_agent import JudgeAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig())

        issues = [
            JudgeIssue(
                file_path="a.py",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="syntax_error",
                description="Syntax error at line 10",
                must_fix_before_merge=True,
                suggested_fix="Fix the syntax error on line 10",
            ),
            JudgeIssue(
                file_path="b.py",
                issue_level=IssueSeverity.LOW,
                issue_type="style",
                description="Minor style issue",
                must_fix_before_merge=False,
            ),
        ]

        instructions = judge.build_repair_instructions(issues)
        assert len(instructions) == 1
        assert instructions[0].file_path == "a.py"
        assert instructions[0].is_repairable

    def test_non_repairable_issue_type(self):
        from src.agents.judge_agent import JudgeAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            from src.models.config import AgentLLMConfig

            judge = JudgeAgent(AgentLLMConfig())

        issues = [
            JudgeIssue(
                file_path="a.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="logic_error",
                description="Logic contradiction",
                must_fix_before_merge=True,
            ),
        ]

        instructions = judge.build_repair_instructions(issues)
        assert len(instructions) == 1
        assert not instructions[0].is_repairable


class TestGateConfig:
    def test_gate_config_defaults(self):
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        assert config.gate.enabled
        assert config.gate.max_consecutive_failures == 3
        assert config.gate.commands == []

    def test_gate_config_with_commands(self):
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            gate=GateConfig(
                commands=[
                    GateCommandConfig(name="lint", command="ruff check ."),
                    GateCommandConfig(
                        name="test",
                        command="pytest -x",
                        timeout_seconds=600,
                    ),
                ]
            ),
        )
        assert len(config.gate.commands) == 2
        assert config.gate.commands[1].timeout_seconds == 600


class TestGateRunner:
    @pytest.mark.asyncio
    async def test_run_gate_success(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(name="echo", command="echo hello")
        result = await runner.run_gate(gate)

        assert result.passed
        assert result.exit_code == 0
        assert "hello" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_run_gate_failure(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(name="fail", command="exit 1")
        result = await runner.run_gate(gate)

        assert not result.passed
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_run_gate_timeout(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(name="slow", command="sleep 10", timeout_seconds=1)
        result = await runner.run_gate(gate)

        assert not result.passed
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_run_all_gates(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gates = [
            GateCommandConfig(name="ok1", command="echo ok1"),
            GateCommandConfig(name="ok2", command="echo ok2"),
        ]
        report = await runner.run_all_gates(gates)

        assert report.all_passed
        assert len(report.results) == 2

    @pytest.mark.asyncio
    async def test_run_all_gates_partial_failure(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gates = [
            GateCommandConfig(name="ok", command="echo ok"),
            GateCommandConfig(name="fail", command="exit 1"),
        ]
        report = await runner.run_all_gates(gates)

        assert not report.all_passed
        assert report.results[0].passed
        assert not report.results[1].passed

    @pytest.mark.asyncio
    async def test_record_baseline(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gates = [GateCommandConfig(name="ver", command="echo v1.0")]
        baselines = await runner.record_baseline(gates)

        assert "ver" in baselines
        assert "v1.0" in baselines["ver"]

    @pytest.mark.asyncio
    async def test_not_worse_than_baseline(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(
            name="test",
            command="echo '5 failed, 100 passed' && exit 1",
            pass_criteria="not_worse_than_baseline",
        )
        baselines = {"test": "6 failed, 99 passed"}
        report = await runner.run_all_gates([gate], baselines)

        assert report.all_passed
        assert report.results[0].passed

    @pytest.mark.asyncio
    async def test_worse_than_baseline_still_fails(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(
            name="test",
            command="echo '10 failed, 90 passed' && exit 1",
            pass_criteria="not_worse_than_baseline",
        )
        baselines = {"test": "5 failed, 95 passed"}
        report = await runner.run_all_gates([gate], baselines)

        assert not report.all_passed


class TestExtractFailedCount:
    def test_pytest_format(self):
        from src.tools.gate_runner import _extract_failed_count

        assert _extract_failed_count("5616 passed, 6 failed, 201 skipped") == 6

    def test_no_failures(self):
        from src.tools.gate_runner import _extract_failed_count

        assert _extract_failed_count("100 passed") is None

    def test_error_format(self):
        from src.tools.gate_runner import _extract_failed_count

        assert _extract_failed_count("errors: 3") == 3


class TestLayerGateCommands:
    def test_merge_layer_with_gate_commands(self):
        from src.models.plan import MergeLayer

        layer = MergeLayer(
            layer_id=3,
            name="models",
            gate_commands=[
                GateCommandConfig(name="lint", command="ruff check ."),
                GateCommandConfig(name="test", command="pytest -x"),
            ],
        )
        assert len(layer.gate_commands) == 2
        assert layer.gate_commands[0].name == "lint"

    def test_default_layers_have_gates(self):
        from src.models.plan import DEFAULT_LAYERS, MergeLayer

        layers = [MergeLayer(**data) for data in DEFAULT_LAYERS]
        layers_with_gates = [ly for ly in layers if ly.gate_commands]
        assert len(layers_with_gates) >= 3


class TestRepairLoopOrchestration:
    @staticmethod
    def _make_ctx(config, **overrides):
        from src.core.phases.base import PhaseContext
        from src.core.state_machine import StateMachine
        from src.core.message_bus import MessageBus
        from src.core.phase_runner import PhaseRunner
        from src.memory.store import MemoryStore
        from src.memory.summarizer import PhaseSummarizer

        defaults = dict(
            config=config,
            git_tool=MagicMock(),
            gate_runner=MagicMock(),
            state_machine=StateMachine(),
            message_bus=MessageBus(),
            checkpoint=MagicMock(),
            phase_runner=PhaseRunner(),
            memory_store=MemoryStore(),
            summarizer=PhaseSummarizer(),
            trace_logger=None,
            emit=None,
            agents={},
        )
        defaults.update(overrides)
        return PhaseContext(**defaults)

    @pytest.mark.asyncio
    async def test_phase5_repair_loop_pass_after_repair(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        fail_verdict = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=1,
            passed_files=[],
            failed_files=["a.py"],
            conditional_files=[],
            issues=[
                JudgeIssue(
                    file_path="a.py",
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="syntax_error",
                    description="syntax error",
                    must_fix_before_merge=True,
                    suggested_fix="fix it",
                )
            ],
            critical_issues_count=1,
            high_issues_count=0,
            overall_confidence=0.3,
            summary="fail",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )

        pass_verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=1,
            passed_files=["a.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.95,
            summary="pass after repair",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )

        fail_msg = MagicMock()
        fail_msg.payload = {"verdict": fail_verdict.model_dump(mode="json")}
        pass_msg = MagicMock()
        pass_msg.payload = {"verdict": pass_verdict.model_dump(mode="json")}

        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(side_effect=[fail_msg, pass_msg])
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(
            return_value=[
                RepairInstruction(
                    file_path="a.py",
                    instruction="fix syntax",
                    is_repairable=True,
                )
            ]
        )
        mock_executor = MagicMock()
        mock_executor.repair = AsyncMock(return_value=[])
        mock_executor.build_rebuttal = AsyncMock(
            return_value=ExecutorRebuttal(
                accepts_all=True,
                repair_instructions=[
                    RepairInstruction(
                        file_path="a.py",
                        instruction="fix syntax",
                        is_repairable=True,
                    )
                ],
            )
        )

        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": mock_executor}
        )

        state = MergeState(config=config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.GENERATING_REPORT
        assert state.judge_repair_rounds == 1
        assert len(state.judge_verdicts_log) == 2
        mock_executor.repair.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase5_veto_escalates_to_human(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            output=MergeConfig.model_fields["output"].default_factory(),
            customizations=[
                CustomizationEntry(
                    name="Missing Feature",
                    verification=[
                        CustomizationVerification(
                            type="file_exists",
                            files=["nonexistent.py"],
                        )
                    ],
                )
            ],
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        pass_verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=1,
            passed_files=["a.py"],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=0.95,
            summary="pass",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )
        msg = MagicMock()
        msg.payload = {"verdict": pass_verdict.model_dump(mode="json")}

        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)

        violation = CustomizationViolation(
            customization_name="Missing Feature",
            verification_type="file_exists",
            expected_pattern="nonexistent.py",
            checked_files=["nonexistent.py"],
            match_count=0,
        )
        mock_judge.verify_customizations = MagicMock(return_value=[violation])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])

        mock_executor = MagicMock()
        mock_executor.build_rebuttal = AsyncMock(
            return_value=ExecutorRebuttal(accepts_all=True, repair_instructions=[])
        )
        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": mock_executor}
        )

        state = MergeState(config=config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN
        assert state.judge_verdict is not None
        assert state.judge_verdict.veto_triggered
        assert "Missing Feature" in (state.judge_verdict.veto_reason or "")
