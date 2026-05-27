"""Unit tests for P1 hardening
(multi-agent-optimization doc §4 P1-1..P1-3).

Covers:
- P1-1 InterfaceChangeExtractor + ReverseImpactScanner
- P1-2 Baseline parsers (8) + GateRunner no_new_regression
- P1-3 SmokeRunner (shell/http) + SmokeTestAgent + Phase 5.5 veto flow
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.config import (
    AgentLLMConfig,
    GateCommandConfig,
    MergeConfig,
    SmokeTestCase,
    SmokeTestConfig,
    SmokeTestSuite,
)
from src.models.judge import (
    JudgeVerdict,
    VerdictType,
    VETO_CONDITIONS,
)
from src.models.smoke import (
    SmokeSuiteReport,
    SmokeTestReport,
    SmokeTestResult,
)
from src.models.state import MergeState, SystemStatus
from src.tools.baseline_parsers import (
    available_parsers,
    diff_new_failures,
    empty_snapshot,
    get_parser,
)
from src.tools.interface_change_extractor import (
    InterfaceChange,
    InterfaceChangeExtractor,
)
from src.tools.reverse_impact_scanner import ReverseImpactScanner
from src.tools.smoke_runner import SmokeRunner


# ---------------------------------------------------------------------------
# P1-1 InterfaceChangeExtractor
# ---------------------------------------------------------------------------


class TestInterfaceChangeExtractor:
    def test_constructor_signature_change(self):
        base = "class Foo:\n    def __init__(self, x):\n        self.x = x\n"
        upstream = (
            "class Foo:\n    def __init__(self, x, y):\n        self.x, self.y = x, y\n"
        )
        changes = InterfaceChangeExtractor().extract("foo.py", base, upstream)
        kinds = {c.change_kind for c in changes}
        assert "constructor_signature" in kinds
        init_change = next(c for c in changes if c.symbol == "__init__")
        assert "y" in init_change.after
        assert "y" not in init_change.before

    def test_method_signature_change(self):
        base = "def login(user):\n    pass\n"
        upstream = "def login(user, token):\n    pass\n"
        changes = InterfaceChangeExtractor().extract("a.py", base, upstream)
        assert any(
            c.change_kind == "method_signature" and c.symbol == "login" for c in changes
        )

    def test_base_class_change(self):
        base = "class Foo(Bar):\n    pass\n"
        upstream = "class Foo(Baz):\n    pass\n"
        changes = InterfaceChangeExtractor().extract("a.py", base, upstream)
        assert any(c.change_kind == "base_class" and c.symbol == "Foo" for c in changes)

    def test_enum_value_change(self):
        base = "STATUS_A = 'a'\nSTATUS_B = 'b'\n"
        upstream = "STATUS_A = 'a'\nSTATUS_B = 'renamed'\n"
        changes = InterfaceChangeExtractor().extract("a.py", base, upstream)
        assert any(
            c.change_kind == "enum_value" and c.symbol == "STATUS_B" for c in changes
        )

    def test_export_removed(self):
        base = "__all__ = ['foo', 'bar']\n"
        upstream = "__all__ = ['foo']\n"
        changes = InterfaceChangeExtractor().extract("a.py", base, upstream)
        assert any(
            c.change_kind == "export_removed" and c.symbol == "bar" for c in changes
        )

    def test_no_change_returns_empty(self):
        content = "def foo(x):\n    return x\n"
        assert InterfaceChangeExtractor().extract("a.py", content, content) == []

    def test_extract_from_paths_batch(self):
        pairs = [
            ("a.py", "def f(x):\n    pass\n", "def f(x, y):\n    pass\n"),
            ("b.py", "def g():\n    pass\n", "def g():\n    pass\n"),
        ]
        changes = InterfaceChangeExtractor().extract_from_paths(pairs)
        assert len(changes) == 1
        assert changes[0].file_path == "a.py"


# ---------------------------------------------------------------------------
# P1-1 ReverseImpactScanner
# ---------------------------------------------------------------------------


class TestReverseImpactScanner:
    def test_detects_fork_only_reference(self, tmp_path):
        (tmp_path / "fork_only.py").write_text(
            "from api import login\nresult = login(user)\n"
        )
        (tmp_path / "unchanged.py").write_text("# no reference\n")

        scanner = ReverseImpactScanner(tmp_path)
        change = InterfaceChange(
            file_path="api/login.py",
            symbol="login",
            change_kind="method_signature",
            before="user",
            after="user, token",
        )
        impacts = scanner.scan(
            [change],
            fork_only_files=["fork_only.py", "unchanged.py"],
        )
        assert impacts == {"login": ["fork_only.py"]}

    def test_word_boundary_avoids_false_positive(self, tmp_path):
        (tmp_path / "fork.py").write_text("loginrequired = True\n")

        scanner = ReverseImpactScanner(tmp_path)
        change = InterfaceChange(
            file_path="a.py",
            symbol="login",
            change_kind="method_signature",
            before="",
            after="",
        )
        assert scanner.scan([change], fork_only_files=["fork.py"]) == {}

    def test_extra_globs_expand_scope(self, tmp_path):
        (tmp_path / "web").mkdir()
        (tmp_path / "web" / "page.ts").write_text("import { foo } from 'x';\n")

        scanner = ReverseImpactScanner(tmp_path)
        change = InterfaceChange(
            file_path="x.ts",
            symbol="foo",
            change_kind="export_removed",
            before="foo",
            after="",
        )
        impacts = scanner.scan([change], fork_only_files=[], extra_globs=["web/*.ts"])
        assert "foo" in impacts
        assert impacts["foo"] == ["web/page.ts"]

    def test_max_files_per_symbol_limit(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("foo()\n")
        scanner = ReverseImpactScanner(tmp_path, max_files_per_symbol=2)
        change = InterfaceChange(
            file_path="x.py",
            symbol="foo",
            change_kind="method_signature",
            before="",
            after="",
        )
        impacts = scanner.scan(
            [change],
            fork_only_files=[f"f{i}.py" for i in range(5)],
        )
        assert len(impacts["foo"]) == 2

    def test_missing_symbol_returns_empty(self, tmp_path):
        scanner = ReverseImpactScanner(tmp_path)
        assert scanner.scan([], []) == {}


# ---------------------------------------------------------------------------
# P1-2 Baseline parsers — one test per parser
# ---------------------------------------------------------------------------


class TestBaselineParsersRegistry:
    def test_all_eight_parsers_registered(self):
        names = set(available_parsers())
        expected = {
            "pytest_summary",
            "mypy_json",
            "basedpyright_json",
            "ruff_json",
            "eslint_json",
            "tsc_errors",
            "go_test_json",
            "cargo_test_json",
            "junit_xml",
        }
        assert expected <= names

    def test_empty_snapshot_shape(self):
        s = empty_snapshot()
        assert s == {"passed": 0, "failed": 0, "failed_ids": []}

    def test_diff_new_failures(self):
        base = {"passed": 0, "failed": 2, "failed_ids": ["a", "b"]}
        cur = {"passed": 0, "failed": 2, "failed_ids": ["b", "c"]}
        assert diff_new_failures(base, cur) == ["c"]


class TestPytestSummaryParser:
    def test_extracts_failed_ids(self):
        parser = get_parser("pytest_summary")
        assert parser is not None
        out = parser(
            "FAILED tests/foo.py::test_a\n"
            "FAILED tests/bar.py::test_b\n"
            "5616 passed, 2 failed, 201 skipped\n"
        )
        assert out["failed"] == 2
        assert out["passed"] == 5616
        assert out["failed_ids"] == [
            "tests/bar.py::test_b",
            "tests/foo.py::test_a",
        ]

    def test_counts_errors_as_failures(self):
        parser = get_parser("pytest_summary")
        out = parser("100 passed, 3 errors\n")
        assert out["failed"] == 3


class TestMypyParser:
    def test_extracts_error_locations(self):
        parser = get_parser("mypy_json")
        out = parser(
            "src/foo.py:12: error: Incompatible return value\n"
            "src/bar.py:3: error: Missing type annotation\n"
            "Found 2 errors in 2 files\n"
        )
        assert out["failed"] == 2
        assert "src/foo.py:12" in out["failed_ids"]


class TestBasedpyrightParser:
    def test_parses_json_errors(self):
        parser = get_parser("basedpyright_json")
        payload = {
            "generalDiagnostics": [
                {
                    "severity": "error",
                    "file": "src/foo.py",
                    "range": {"start": {"line": 10}},
                },
                {
                    "severity": "warning",
                    "file": "src/bar.py",
                    "range": {"start": {"line": 1}},
                },
            ],
            "summary": {"errorCount": 1},
        }
        out = parser(json.dumps(payload))
        assert out["failed"] == 1
        assert out["failed_ids"] == ["src/foo.py:10"]

    def test_invalid_json_returns_empty(self):
        parser = get_parser("basedpyright_json")
        assert parser("not-json")["failed"] == 0


class TestRuffJsonParser:
    def test_parses_list(self):
        parser = get_parser("ruff_json")
        payload = [
            {
                "code": "E501",
                "filename": "src/foo.py",
                "location": {"row": 10},
            }
        ]
        out = parser(json.dumps(payload))
        assert out["failed_ids"] == ["src/foo.py:10:E501"]


class TestEslintParser:
    def test_parses_messages(self):
        parser = get_parser("eslint_json")
        payload = [
            {
                "filePath": "src/foo.ts",
                "messages": [
                    {"ruleId": "no-unused-vars", "line": 10, "severity": 2},
                    {"ruleId": "quotes", "line": 3, "severity": 1},
                ],
            }
        ]
        out = parser(json.dumps(payload))
        assert out["failed_ids"] == ["src/foo.ts:10:no-unused-vars"]


class TestTscParser:
    def test_parses_tsc_errors(self):
        parser = get_parser("tsc_errors")
        out = parser(
            "src/foo.ts(10,5): error TS2322: Type mismatch.\n"
            "src/bar.ts(3,1): error TS2304: Cannot find name 'x'.\n"
        )
        assert out["failed"] == 2
        assert "src/foo.ts:10:TS2322" in out["failed_ids"]


class TestGoTestJsonParser:
    def test_parses_ndjson(self):
        parser = get_parser("go_test_json")
        out = parser(
            '{"Action":"pass","Package":"foo","Test":"TestA"}\n'
            '{"Action":"fail","Package":"foo","Test":"TestB"}\n'
            '{"Action":"fail","Package":"bar","Test":"TestC"}\n'
        )
        assert out["passed"] == 1
        assert out["failed"] == 2
        assert "foo.TestB" in out["failed_ids"]


class TestCargoTestJsonParser:
    def test_parses_ndjson(self):
        parser = get_parser("cargo_test_json")
        out = parser(
            '{"type":"test","event":"ok","name":"tests::a"}\n'
            '{"type":"test","event":"failed","name":"tests::b"}\n'
        )
        assert out["failed_ids"] == ["tests::b"]


class TestJUnitXmlParser:
    def test_parses_xml(self):
        parser = get_parser("junit_xml")
        xml = (
            '<testsuite tests="3" failures="1">'
            '<testcase classname="com.Foo" name="testA"/>'
            '<testcase classname="com.Foo" name="testB">'
            "<failure>broken</failure>"
            "</testcase>"
            '<testcase classname="com.Foo" name="testC">'
            "<skipped/></testcase>"
            "</testsuite>"
        )
        out = parser(xml)
        assert out["passed"] == 1
        assert out["failed"] == 1
        assert "com.Foo.testB" in out["failed_ids"]

    def test_invalid_xml_returns_empty(self):
        parser = get_parser("junit_xml")
        assert parser("not-xml")["failed"] == 0


# ---------------------------------------------------------------------------
# P1-2 GateRunner no_new_regression
# ---------------------------------------------------------------------------


class TestGateRunnerNoNewRegression:
    @pytest.mark.asyncio
    async def test_subset_of_baseline_passes(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(
            name="py",
            command=(
                "printf 'FAILED tests/a.py::test_a\\n1 failed, 5 passed\\n' && exit 1"
            ),
            pass_criteria="no_new_regression",
            baseline_parser="pytest_summary",
        )
        baselines = {
            "py": json.dumps(
                {
                    "passed": 4,
                    "failed": 2,
                    "failed_ids": [
                        "tests/a.py::test_a",
                        "tests/b.py::test_b",
                    ],
                }
            )
        }
        report = await runner.run_all_gates([gate], baselines)
        assert report.all_passed
        assert report.new_failures == {}

    @pytest.mark.asyncio
    async def test_new_failure_trips_gate_even_if_total_smaller(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(
            name="py",
            command=(
                "printf 'FAILED tests/new.py::test_new\\n"
                "1 failed, 10 passed\\n' && exit 1"
            ),
            pass_criteria="no_new_regression",
            baseline_parser="pytest_summary",
        )
        baselines = {
            "py": json.dumps(
                {
                    "passed": 5,
                    "failed": 3,
                    "failed_ids": [
                        "tests/a.py::test_a",
                        "tests/b.py::test_b",
                        "tests/c.py::test_c",
                    ],
                }
            )
        }
        report = await runner.run_all_gates([gate], baselines)
        assert not report.all_passed
        assert "py" in report.new_failures
        assert report.new_failures["py"] == ["tests/new.py::test_new"]

    @pytest.mark.asyncio
    async def test_missing_parser_falls_back_gracefully(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(
            name="py",
            command="exit 1",
            pass_criteria="no_new_regression",
            baseline_parser="unknown_parser_xyz",
        )
        report = await runner.run_all_gates([gate], {"py": ""})
        assert not report.all_passed

    @pytest.mark.asyncio
    async def test_record_baseline_structured(self, tmp_path):
        from src.tools.gate_runner import GateRunner

        runner = GateRunner(tmp_path)
        gate = GateCommandConfig(
            name="py",
            command="printf 'FAILED tests/a.py::t1\\n1 failed, 4 passed\\n'",
            baseline_parser="pytest_summary",
        )
        result = await runner.record_baseline_structured([gate])
        assert result["py"]["failed_ids"] == ["tests/a.py::t1"]
        assert result["py"]["failed"] == 1


# ---------------------------------------------------------------------------
# P1-3 SmokeRunner
# ---------------------------------------------------------------------------


class TestSmokeRunner:
    @pytest.mark.asyncio
    async def test_shell_pass(self, tmp_path):
        runner = SmokeRunner(tmp_path)
        cfg = SmokeTestConfig(
            enabled=True,
            suites=[
                SmokeTestSuite(
                    name="s1",
                    kind="shell",
                    cases=[SmokeTestCase(id="echo", cmd="echo ok")],
                )
            ],
        )
        report = await runner.run(cfg)
        assert report.all_passed
        assert report.total_failed == 0
        assert report.suites[0].results[0].exit_code == 0

    @pytest.mark.asyncio
    async def test_shell_fail(self, tmp_path):
        runner = SmokeRunner(tmp_path)
        cfg = SmokeTestConfig(
            enabled=True,
            suites=[
                SmokeTestSuite(
                    name="s1",
                    kind="shell",
                    cases=[SmokeTestCase(id="fail", cmd="exit 3")],
                )
            ],
        )
        report = await runner.run(cfg)
        assert not report.all_passed
        assert report.suites[0].results[0].exit_code == 3
        assert report.suites[0].results[0].status == "fail"

    @pytest.mark.asyncio
    async def test_shell_timeout_reports_error(self, tmp_path):
        runner = SmokeRunner(tmp_path)
        cfg = SmokeTestConfig(
            enabled=True,
            suites=[
                SmokeTestSuite(
                    name="s1",
                    kind="shell",
                    cases=[SmokeTestCase(id="slow", cmd="sleep 5", timeout_seconds=1)],
                )
            ],
        )
        report = await runner.run(cfg)
        assert report.suites[0].results[0].status == "error"

    @pytest.mark.asyncio
    async def test_http_missing_url_is_error(self, tmp_path):
        runner = SmokeRunner(tmp_path)
        cfg = SmokeTestConfig(
            enabled=True,
            suites=[
                SmokeTestSuite(name="h", kind="http", cases=[SmokeTestCase(id="noop")])
            ],
        )
        report = await runner.run(cfg)
        assert report.suites[0].results[0].status == "error"

    @pytest.mark.asyncio
    async def test_empty_suites_short_circuit(self, tmp_path):
        runner = SmokeRunner(tmp_path)
        cfg = SmokeTestConfig(enabled=True, suites=[])
        report = await runner.run(cfg)
        assert report.all_passed


# ---------------------------------------------------------------------------
# P1-3 SmokeTestReport model
# ---------------------------------------------------------------------------


class TestSmokeTestReportModel:
    def test_report_properties(self):
        report = SmokeTestReport(
            all_passed=False,
            suites=[
                SmokeSuiteReport(
                    suite_name="s",
                    kind="shell",
                    results=[
                        SmokeTestResult(
                            suite_name="s",
                            case_id="ok",
                            kind="shell",
                            status="pass",
                        ),
                        SmokeTestResult(
                            suite_name="s",
                            case_id="bad",
                            kind="shell",
                            status="fail",
                            exit_code=1,
                        ),
                    ],
                )
            ],
        )
        assert report.total_cases == 2
        assert report.total_failed == 1
        failed = report.failed_results()
        assert len(failed) == 1 and failed[0].case_id == "bad"


# ---------------------------------------------------------------------------
# P1-3 Phase 5.5 smoke flow
# ---------------------------------------------------------------------------


class TestPhase55SmokeFlow:
    @staticmethod
    def _make_ctx(config, **overrides):
        from src.core.phases.base import PhaseContext
        from src.core.state_machine import StateMachine
        from src.memory.store import MemoryStore
        from src.memory.summarizer import PhaseSummarizer

        defaults = dict(
            config=config,
            git_tool=MagicMock(),
            gate_runner=MagicMock(),
            state_machine=StateMachine(),
            checkpoint=MagicMock(),
            memory_store=MemoryStore(),
            summarizer=PhaseSummarizer(),
            trace_logger=None,
            emit=None,
            agents={},
        )
        defaults.update(overrides)
        return PhaseContext(**defaults)

    @pytest.mark.asyncio
    async def test_smoke_failure_vetoes_pass_verdict(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            smoke_tests=SmokeTestConfig(
                enabled=True,
                suites=[
                    SmokeTestSuite(
                        name="s",
                        kind="shell",
                        cases=[SmokeTestCase(id="x", cmd="exit 1")],
                    )
                ],
                block_on_failure=True,
            ),
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
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])

        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": MagicMock()}
        )

        state = MergeState(config=config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.AWAITING_HUMAN
        assert state.judge_verdict is not None
        assert state.judge_verdict.veto_triggered
        assert "Smoke test failed" in (state.judge_verdict.veto_reason or "")

    @pytest.mark.asyncio
    async def test_smoke_disabled_keeps_pass(self, tmp_path):
        from src.core.phases.judge_review import JudgeReviewPhase

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            smoke_tests=SmokeTestConfig(enabled=False),
        )
        config.output.directory = str(tmp_path)
        config.output.debug_directory = str(tmp_path / "debug")

        pass_verdict = JudgeVerdict(
            verdict=VerdictType.PASS,
            reviewed_files_count=0,
            passed_files=[],
            failed_files=[],
            conditional_files=[],
            issues=[],
            critical_issues_count=0,
            high_issues_count=0,
            overall_confidence=1.0,
            summary="pass",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test",
        )
        msg = MagicMock()
        msg.payload = {"verdict": pass_verdict.model_dump(mode="json")}

        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(return_value=msg)
        mock_judge.verify_customizations = MagicMock(return_value=[])
        mock_judge.build_repair_instructions = MagicMock(return_value=[])

        ctx = self._make_ctx(
            config, agents={"judge": mock_judge, "executor": MagicMock()}
        )

        state = MergeState(config=config)
        state.status = SystemStatus.JUDGE_REVIEWING
        phase = JudgeReviewPhase()
        await phase.execute(state, ctx)

        assert state.status == SystemStatus.GENERATING_REPORT


# ---------------------------------------------------------------------------
# Judge reverse_impact_unhandled VETO
# ---------------------------------------------------------------------------


class TestJudgeReverseImpactVeto:
    def test_reverse_impact_produces_veto_issue(self):
        from src.agents.judge_agent import JudgeAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            judge = JudgeAgent(AgentLLMConfig())

        state = MergeState(
            config=MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        )
        state.interface_changes = [
            InterfaceChange(
                file_path="api/login.py",
                symbol="login_required",
                change_kind="method_signature",
                before="request",
                after="request, current_user.id",
            )
        ]
        state.reverse_impacts = {"login_required": ["fork/auth.py"]}

        from src.core.read_only_state_view import ReadOnlyStateView

        issues = judge._check_reverse_impacts(ReadOnlyStateView(state))
        assert len(issues) == 1
        assert issues[0].issue_type == "reverse_impact_unhandled"
        assert issues[0].veto_condition in VETO_CONDITIONS


# ---------------------------------------------------------------------------
# Config / model sanity
# ---------------------------------------------------------------------------


class TestConfigExtensions:
    def test_reverse_impact_config_defaults(self):
        config = MergeConfig(upstream_ref="u", fork_ref="f")
        assert config.reverse_impact.enabled
        assert config.reverse_impact.max_files_per_symbol >= 1

    def test_smoke_tests_config_defaults_to_disabled(self):
        config = MergeConfig(upstream_ref="u", fork_ref="f")
        assert not config.smoke_tests.enabled
        assert config.smoke_tests.suites == []

    def test_gate_command_config_new_fields(self):
        cmd = GateCommandConfig(
            name="py",
            command="pytest",
            pass_criteria="no_new_regression",
            baseline_parser="pytest_summary",
        )
        assert cmd.pass_criteria == "no_new_regression"
        assert cmd.baseline_parser == "pytest_summary"

    def test_new_veto_conditions_listed(self):
        assert (
            "Reverse-impact unhandled for upstream interface change" in VETO_CONDITIONS
        )
        assert "Smoke test failed" in VETO_CONDITIONS


class TestMergeStateP1Fields:
    def test_state_has_p1_fields(self):
        config = MergeConfig(upstream_ref="u", fork_ref="f")
        state = MergeState(config=config)
        assert state.interface_changes == []
        assert state.reverse_impacts == {}
        assert state.smoke_test_report is None
        assert state.consecutive_smoke_failures == 0
