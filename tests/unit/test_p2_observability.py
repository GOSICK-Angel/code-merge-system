import re
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.models.plan import (
    MergePlan,
    MergePlanLive,
    MergePhase,
    PhaseFileBatch,
    PhaseExecutionRecord,
    PhaseJudgeRecord,
    PhaseGateRecord,
    OpenIssue,
    RiskSummary,
    CategorySummary,
    MergeLayer,
)
from src.models.diff import RiskLevel, FileChangeCategory
from src.models.judge import JudgeIssue, IssueSeverity, VETO_CONDITIONS


class TestLivingPlanModels:
    def test_phase_execution_record(self):
        rec = PhaseExecutionRecord(
            phase_id="auto_merge",
            started_at=datetime.now(),
            files_processed=42,
            files_skipped=3,
        )
        assert rec.files_processed == 42
        assert rec.completed_at is None
        assert rec.commit_hash is None

    def test_phase_judge_record(self):
        rec = PhaseJudgeRecord(
            phase_id="judge_review",
            round_number=1,
            verdict="FAIL",
            issues=[{"file": "a.py", "type": "syntax_error"}],
            veto_triggered=True,
        )
        assert rec.veto_triggered is True
        assert len(rec.issues) == 1

    def test_phase_gate_record(self):
        rec = PhaseGateRecord(
            phase_id="layer_3",
            gate_results=[{"gate_name": "lint", "passed": True, "exit_code": 0}],
            all_passed=True,
        )
        assert rec.all_passed is True

    def test_open_issue(self):
        issue = OpenIssue(
            phase_id="auto_merge",
            description="B-class file mismatch",
            severity="critical",
        )
        assert issue.resolved is False
        assert issue.assigned_to_phase is None
        assert issue.issue_id

    def test_merge_plan_live_extends_merge_plan(self):
        rs = RiskSummary(
            total_files=10,
            auto_safe_count=8,
            auto_risky_count=1,
            human_required_count=1,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.8,
        )
        live = MergePlanLive(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            merge_base_commit="abc123",
            phases=[],
            risk_summary=rs,
            project_context_summary="test",
        )
        assert isinstance(live, MergePlan)
        assert live.execution_records == []
        assert live.judge_records == []
        assert live.gate_records == []
        assert live.open_issues == []
        assert live.todo_merge_count == 0
        assert live.todo_merge_limit == 30

    def test_live_plan_accumulates_records(self):
        rs = RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        )
        live = MergePlanLive(
            created_at=datetime.now(),
            upstream_ref="u",
            fork_ref="f",
            merge_base_commit="base",
            phases=[],
            risk_summary=rs,
            project_context_summary="",
        )
        live.execution_records.append(
            PhaseExecutionRecord(
                phase_id="p1",
                started_at=datetime.now(),
                files_processed=5,
            )
        )
        live.judge_records.append(
            PhaseJudgeRecord(phase_id="j1", round_number=0, verdict="PASS")
        )
        live.gate_records.append(
            PhaseGateRecord(phase_id="g1", gate_results=[], all_passed=True)
        )
        live.open_issues.append(
            OpenIssue(phase_id="p1", description="test", severity="low")
        )
        assert len(live.execution_records) == 1
        assert len(live.judge_records) == 1
        assert len(live.gate_records) == 1
        assert len(live.open_issues) == 1


class TestJudgeIssueVetoCondition:
    def test_veto_condition_field_present(self):
        issue = JudgeIssue(
            file_path="a.py",
            issue_level=IssueSeverity.CRITICAL,
            issue_type="b_class_mismatch",
            description="B-class file differs",
            veto_condition="B-class file differs from upstream",
        )
        assert issue.veto_condition == "B-class file differs from upstream"

    def test_veto_condition_defaults_none(self):
        issue = JudgeIssue(
            file_path="a.py",
            issue_level=IssueSeverity.LOW,
            issue_type="style",
            description="minor style issue",
        )
        assert issue.veto_condition is None

    def test_veto_conditions_list(self):
        assert len(VETO_CONDITIONS) >= 6
        assert any("B-class" in c for c in VETO_CONDITIONS)
        assert any("D-missing" in c for c in VETO_CONDITIONS)
        assert any("TODO [merge]" in c for c in VETO_CONDITIONS)
        assert any("TODO [check]" in c for c in VETO_CONDITIONS)


class TestThreeWayDiff:
    def test_compare(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.side_effect = lambda ref, fp: {
            ("base", "f.py"): "base_content",
            ("upstream", "f.py"): "upstream_content",
        }.get((ref, fp))

        (tmp_path / "f.py").write_text("merged_content")

        tw = ThreeWayDiff(git_tool)
        result = tw.compare("f.py", "base", "upstream")

        assert result.base_content == "base_content"
        assert result.upstream_content == "upstream_content"
        assert result.merged_content == "merged_content"

    def test_verify_b_class_pass(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.return_value = "exact_content"

        (tmp_path / "f.py").write_text("exact_content")

        tw = ThreeWayDiff(git_tool)
        assert tw.verify_b_class("f.py", "upstream") is True

    def test_verify_b_class_fail(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.return_value = "upstream_version"

        (tmp_path / "f.py").write_text("different_version")

        tw = ThreeWayDiff(git_tool)
        assert tw.verify_b_class("f.py", "upstream") is False

    def test_verify_d_missing_present(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        (tmp_path / "new_file.py").write_text("content")

        tw = ThreeWayDiff(git_tool)
        assert tw.verify_d_missing_present("new_file.py") is True
        assert tw.verify_d_missing_present("missing.py") is False

    def test_extract_upstream_additions(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        base = "def existing():\n    pass\n"
        upstream = "def existing():\n    pass\n\ndef new_func():\n    return 1\n\nclass NewClass:\n    pass\n"

        git_tool.get_file_content.side_effect = lambda ref, fp: {
            "base": base,
            "upstream": upstream,
        }.get(ref)

        tw = ThreeWayDiff(git_tool)
        additions = tw.extract_upstream_additions("f.py", "base", "upstream")
        assert "new_func" in additions
        assert "NewClass" in additions
        assert "existing" not in additions

    def test_verify_additions_present(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        (tmp_path / "f.py").write_text(
            "def existing():\n    pass\n\ndef new_func():\n    return 1\n"
        )

        tw = ThreeWayDiff(git_tool)
        missing = tw.verify_additions_present("f.py", ["new_func", "MissingClass"])
        assert "MissingClass" in missing
        assert "new_func" not in missing

    def test_count_todo_merge(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        (tmp_path / "f.py").write_text(
            "# TODO [merge] fix this\nx = 1\n# TODO [merge] and this\n"
        )

        tw = ThreeWayDiff(git_tool)
        assert tw.count_todo_merge("f.py") == 2

    def test_find_todo_check(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        (tmp_path / "f.py").write_text(
            "line1\n# TODO [check] bad\nline3\n# TODO [check] also bad\n"
        )

        tw = ThreeWayDiff(git_tool)
        lines = tw.find_todo_check("f.py")
        assert lines == [2, 4]

    def test_find_todo_check_none(self, tmp_path):
        from src.tools.three_way_diff import ThreeWayDiff

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        (tmp_path / "f.py").write_text("clean code\n")

        tw = ThreeWayDiff(git_tool)
        assert tw.find_todo_check("f.py") == []


class TestDeterministicPipeline:
    def _make_state(self, tmp_path, categories):
        from src.models.config import MergeConfig

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="fork/main")
        state = MagicMock()
        state.file_categories = categories
        state.merge_base_commit = "base123"
        state.config = config
        state.file_decision_records = {}
        return state

    def test_b_class_veto_on_mismatch(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.return_value = "upstream_version"

        (tmp_path / "b_file.py").write_text("different_merged")

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        state = self._make_state(tmp_path, {"b_file.py": FileChangeCategory.B})

        issues = judge._run_deterministic_pipeline(state, {})
        assert len(issues) == 1
        assert issues[0].veto_condition == "B-class file differs from upstream"
        assert issues[0].issue_type == "b_class_mismatch"

    def test_d_missing_veto_when_absent(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        state = self._make_state(
            tmp_path, {"new_file.py": FileChangeCategory.D_MISSING}
        )

        issues = judge._run_deterministic_pipeline(state, {})
        assert any(i.issue_type == "d_missing_not_processed" for i in issues)

    def test_d_missing_veto_when_processed_but_absent(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import (
            FileDecisionRecord,
            MergeDecision,
            DecisionSource,
        )
        from src.models.diff import FileStatus

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        state = self._make_state(
            tmp_path, {"new_file.py": FileChangeCategory.D_MISSING}
        )
        state.file_decision_records["new_file.py"] = FileDecisionRecord(
            file_path="new_file.py",
            file_status=FileStatus.ADDED,
            decision=MergeDecision.TAKE_TARGET,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="D-missing: copying new file from upstream",
            phase="auto_merge",
            agent="executor",
        )

        issues = judge._run_deterministic_pipeline(state, {})
        assert any(i.issue_type == "d_missing_absent" for i in issues)

    def test_todo_check_veto(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.return_value = "content"

        (tmp_path / "c_file.py").write_text("# TODO [check] prohibited\nx = 1\n")

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        state = self._make_state(tmp_path, {"c_file.py": FileChangeCategory.C})

        issues = judge._run_deterministic_pipeline(state, {})
        todo_issues = [i for i in issues if i.issue_type == "prohibited_todo_check"]
        assert len(todo_issues) == 1
        assert todo_issues[0].veto_condition == "Unannotated TODO [check] exists"

    def test_no_issues_for_clean_files(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path
        git_tool.get_file_content.side_effect = lambda ref, fp: {
            ("base123", "b.py"): None,
            ("upstream/main", "b.py"): "content",
        }.get((ref, fp))

        (tmp_path / "b.py").write_text("content")

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        state = self._make_state(tmp_path, {"b.py": FileChangeCategory.B})

        issues = judge._run_deterministic_pipeline(state, {})
        assert len(issues) == 0

    def test_upstream_addition_missing(self, tmp_path):
        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig

        git_tool = MagicMock()
        git_tool.repo_path = tmp_path

        base = "def old():\n    pass\n"
        upstream = "def old():\n    pass\n\ndef new_api():\n    return 42\n"
        merged = "def old():\n    pass\n"

        git_tool.get_file_content.side_effect = lambda ref, fp: {
            "base123": base,
            "upstream/main": upstream,
        }.get(ref)

        (tmp_path / "c.py").write_text(merged)

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig(), git_tool=git_tool)

        state = self._make_state(tmp_path, {"c.py": FileChangeCategory.C})

        issues = judge._run_deterministic_pipeline(state, {})
        addition_issues = [
            i for i in issues if i.issue_type == "missing_upstream_addition"
        ]
        assert len(addition_issues) == 1
        assert "new_api" in addition_issues[0].description


class TestWriteLivingPlanReport:
    def test_basic_report_generation(self, tmp_path):
        from src.tools.report_writer import write_living_plan_report
        from src.models.state import MergeState
        from src.models.config import MergeConfig

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
        )
        state = MergeState(config=config)

        rs = RiskSummary(
            total_files=10,
            auto_safe_count=8,
            auto_risky_count=1,
            human_required_count=1,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.8,
        )
        state.merge_plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            merge_base_commit="abc",
            phases=[],
            risk_summary=rs,
            project_context_summary="test",
        )

        report_path = write_living_plan_report(state, str(tmp_path))
        assert report_path.exists()
        content = report_path.read_text()
        assert "Living Merge Plan" in content
        assert "upstream/main" in content

    def test_live_plan_with_records(self, tmp_path):
        from src.tools.report_writer import write_living_plan_report
        from src.models.state import MergeState
        from src.models.config import MergeConfig

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
        )
        state = MergeState(config=config)

        rs = RiskSummary(
            total_files=5,
            auto_safe_count=5,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        )
        live = MergePlanLive(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            merge_base_commit="abc",
            phases=[],
            risk_summary=rs,
            project_context_summary="test",
        )
        live.execution_records.append(
            PhaseExecutionRecord(
                phase_id="auto_merge",
                started_at=datetime.now(),
                files_processed=5,
            )
        )
        live.judge_records.append(
            PhaseJudgeRecord(
                phase_id="judge_review",
                round_number=0,
                verdict="PASS",
            )
        )
        live.gate_records.append(
            PhaseGateRecord(
                phase_id="layer_3",
                gate_results=[{"gate_name": "lint", "passed": True, "exit_code": 0}],
                all_passed=True,
            )
        )
        live.open_issues.append(
            OpenIssue(
                phase_id="auto_merge",
                description="Minor style issue",
                severity="low",
            )
        )
        state.merge_plan = live

        report_path = write_living_plan_report(state, str(tmp_path))
        content = report_path.read_text()
        assert "Execution Log" in content
        assert "auto_merge" in content
        assert "Judge Review Log" in content
        assert "PASS" in content
        assert "Gate Check Log" in content
        assert "lint" in content
        assert "Open Issues" in content
        assert "Minor style issue" in content
        assert "TODO [merge] count" in content


class TestSymbolExtraction:
    def test_python_symbols(self):
        from src.tools.three_way_diff import _extract_symbols

        code = """
def func_a():
    pass

async def func_b():
    pass

class MyClass:
    def method(self):
        pass
"""
        symbols = _extract_symbols(code)
        assert "func_a" in symbols
        assert "func_b" in symbols
        assert "MyClass" in symbols

    def test_javascript_symbols(self):
        from src.tools.three_way_diff import _extract_symbols

        code = """
function handleClick() {}
export function getData() {}
const processItem = async () => {}
export const helper = () => {}
"""
        symbols = _extract_symbols(code)
        assert "handleClick" in symbols
        assert "getData" in symbols
        assert "processItem" in symbols
        assert "helper" in symbols

    def test_empty_content(self):
        from src.tools.three_way_diff import _extract_symbols

        assert _extract_symbols("") == set()
        assert _extract_symbols("x = 1\ny = 2\n") == set()


class TestOrchestratorAppendRecords:
    def _make_state_with_live_plan(self):
        from src.models.state import MergeState
        from src.models.config import MergeConfig

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="fork/main")
        state = MergeState(config=config)
        rs = RiskSummary(
            total_files=5,
            auto_safe_count=5,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        )
        state.merge_plan = MergePlanLive(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            merge_base_commit="abc",
            phases=[],
            risk_summary=rs,
            project_context_summary="test",
        )
        return state

    def test_append_execution_record(self):
        from src.models.state import PhaseResult
        from src.core.phases._gate_helpers import append_execution_record

        state = self._make_state_with_live_plan()

        phase_result = PhaseResult(
            phase=MergePhase.AUTO_MERGE,
            status="completed",
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )
        append_execution_record(state, "auto_merge", phase_result, 10)

        assert isinstance(state.merge_plan, MergePlanLive)
        assert len(state.merge_plan.execution_records) == 1
        rec = state.merge_plan.execution_records[0]
        assert rec.phase_id == "auto_merge"
        assert rec.files_processed == 10

    def test_append_execution_record_skips_non_live(self):
        from src.models.state import MergeState, PhaseResult
        from src.models.config import MergeConfig
        from src.models.plan import MergePlan
        from src.core.phases._gate_helpers import append_execution_record

        config = MergeConfig(upstream_ref="u", fork_ref="f")
        state = MergeState(config=config)
        rs = RiskSummary(
            total_files=1,
            auto_safe_count=1,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        )
        state.merge_plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="u",
            fork_ref="f",
            merge_base_commit="x",
            phases=[],
            risk_summary=rs,
            project_context_summary="",
        )
        phase_result = PhaseResult(
            phase=MergePhase.AUTO_MERGE,
            status="completed",
            started_at=datetime.now(),
        )
        append_execution_record(state, "auto_merge", phase_result, 5)
        assert not isinstance(state.merge_plan, MergePlanLive)

    def test_append_judge_record(self):
        from src.models.judge import JudgeVerdict, VerdictType
        from src.core.phases._gate_helpers import append_judge_record

        state = self._make_state_with_live_plan()

        state.judge_verdict = JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=2,
            passed_files=[],
            failed_files=["a.py"],
            conditional_files=["b.py"],
            issues=[
                JudgeIssue(
                    file_path="a.py",
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="syntax_error",
                    description="bad syntax",
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
        state.judge_repair_rounds = 1

        append_judge_record(state, 1)

        assert isinstance(state.merge_plan, MergePlanLive)
        assert len(state.merge_plan.judge_records) == 1
        jrec = state.merge_plan.judge_records[0]
        assert jrec.round_number == 1
        assert jrec.verdict == "fail"
        assert len(jrec.issues) == 1

    def test_append_judge_record_multiple_rounds(self):
        from src.models.judge import JudgeVerdict, VerdictType
        from src.core.phases._gate_helpers import append_judge_record

        state = self._make_state_with_live_plan()

        for round_num in range(3):
            state.judge_verdict = JudgeVerdict(
                verdict=VerdictType.FAIL if round_num < 2 else VerdictType.PASS,
                reviewed_files_count=1,
                passed_files=[] if round_num < 2 else ["a.py"],
                failed_files=["a.py"] if round_num < 2 else [],
                conditional_files=[],
                issues=[],
                critical_issues_count=0,
                high_issues_count=0,
                overall_confidence=0.9,
                summary=f"round {round_num}",
                blocking_issues=[],
                timestamp=datetime.now(),
                judge_model="test",
            )
            append_judge_record(state, round_num)

        assert isinstance(state.merge_plan, MergePlanLive)
        assert len(state.merge_plan.judge_records) == 3
        assert state.merge_plan.judge_records[0].round_number == 0
        assert state.merge_plan.judge_records[1].round_number == 1
        assert state.merge_plan.judge_records[2].round_number == 2
        assert state.merge_plan.judge_records[2].verdict == "pass"

    def test_append_gate_record(self):
        from src.core.phases._gate_helpers import append_gate_record

        state = self._make_state_with_live_plan()

        gate_entry = {
            "phase": "layer_3",
            "all_passed": True,
            "results": [
                {"gate_name": "lint", "passed": True, "exit_code": 0},
                {"gate_name": "test", "passed": True, "exit_code": 0},
            ],
        }
        append_gate_record(state, "layer_3", gate_entry)

        assert isinstance(state.merge_plan, MergePlanLive)
        assert len(state.merge_plan.gate_records) == 1
        grec = state.merge_plan.gate_records[0]
        assert grec.phase_id == "layer_3"
        assert grec.all_passed is True
        assert len(grec.gate_results) == 2

    def test_append_judge_record_skips_when_no_verdict(self):
        from src.core.phases._gate_helpers import append_judge_record

        state = self._make_state_with_live_plan()
        state.judge_verdict = None

        append_judge_record(state, 0)

        assert isinstance(state.merge_plan, MergePlanLive)
        assert len(state.merge_plan.judge_records) == 0
