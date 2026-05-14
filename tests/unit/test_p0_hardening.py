"""Unit tests for P0 hardening (multi-agent-optimization doc §4 P0-1..P0-4).

Covers:
- P0-1 CustomizationVerification: grep_count_min / grep_count_baseline / line_retention
- P0-2 ShadowConflictDetector + Planner HUMAN_REQUIRED upgrade + Judge VETO
- P0-3 TopLevelInvocationExtractor + Judge VETO
- P0-4 CrossLayerChecker + Judge VETO
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.judge_agent import JudgeAgent
from src.models.config import (
    AgentLLMConfig,
    CrossLayerAssertion,
    CustomizationEntry,
    CustomizationVerification,
    MergeConfig,
    ShadowRuleConfig,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState
from src.tools.cross_layer_checker import CrossLayerChecker
from src.tools.shadow_conflict_detector import (
    DEFAULT_SHADOW_RULES,
    ShadowConflictDetector,
)
from src.tools.three_way_diff import (
    ThreeWayDiff,
    _extract_top_level_invocations,
)


def _make_judge(git_tool=None) -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return JudgeAgent(AgentLLMConfig(), git_tool=git_tool)


class _FakeGit:
    def __init__(self, repo_path: Path, ref_contents: dict[str, dict[str, str]]):
        self.repo_path = repo_path
        self._ref_contents = ref_contents

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        return self._ref_contents.get(ref, {}).get(file_path)

    def list_files(self, ref: str) -> list[str]:
        return list(self._ref_contents.get(ref, {}).keys())

    def grep_in_files(
        self, pattern: str, file_patterns: list[str]
    ) -> dict[str, list[str]]:
        import fnmatch
        import re

        compiled = re.compile(pattern)
        results: dict[str, list[str]] = {}
        all_files = [
            str(p.relative_to(self.repo_path))
            for p in self.repo_path.rglob("*")
            if p.is_file()
        ]
        for fp in all_files:
            if not any(fnmatch.fnmatch(fp, gp) for gp in file_patterns):
                continue
            try:
                content = (self.repo_path / fp).read_text(encoding="utf-8")
            except Exception:
                continue
            matches = compiled.findall(content)
            if matches:
                results[fp] = matches
        return results


# ─── P0-1: grep_count_min ─────────────────────────────────────────────────────


class TestGrepCountMin:
    def test_fails_when_below_min_count(self, tmp_path):
        (tmp_path / "api.py").write_text("Authorization header only once\n")
        git = _FakeGit(tmp_path, {})
        judge = _make_judge(git_tool=git)

        cust = [
            CustomizationEntry(
                name="Auth mentions",
                files=["*.py"],
                verification=[
                    CustomizationVerification(
                        type="grep_count_min",
                        pattern="Authorization",
                        files=["*.py"],
                        min_count=3,
                    )
                ],
            )
        ]
        violations = judge.verify_customizations(cust)
        assert len(violations) == 1
        assert violations[0].verification_type == "grep_count_min"

    def test_passes_when_min_count_met(self, tmp_path):
        (tmp_path / "api.py").write_text(
            "Authorization a\nAuthorization b\nAuthorization c\n"
        )
        git = _FakeGit(tmp_path, {})
        judge = _make_judge(git_tool=git)
        cust = [
            CustomizationEntry(
                name="Auth mentions",
                files=["*.py"],
                verification=[
                    CustomizationVerification(
                        type="grep_count_min",
                        pattern="Authorization",
                        files=["*.py"],
                        min_count=3,
                    )
                ],
            )
        ]
        assert judge.verify_customizations(cust) == []


# ─── P0-1: grep_count_baseline ────────────────────────────────────────────────


class TestGrepCountBaseline:
    def test_fails_when_current_below_baseline(self, tmp_path):
        (tmp_path / "workspace.py").write_text("api.add_resource(A)\n")

        git = _FakeGit(
            tmp_path,
            {
                "base-sha": {
                    "workspace.py": (
                        "api.add_resource(A)\n"
                        "api.add_resource(B)\n"
                        "api.add_resource(C)\n"
                    )
                }
            },
        )
        judge = _make_judge(git_tool=git)
        cust = [
            CustomizationEntry(
                name="api.add_resource retention",
                files=["*.py"],
                verification=[
                    CustomizationVerification(
                        type="grep_count_baseline",
                        pattern=r"api\.add_resource",
                        files=["*.py"],
                    )
                ],
            )
        ]
        violations = judge.verify_customizations(cust, merge_base="base-sha")
        assert len(violations) == 1
        assert violations[0].verification_type == "grep_count_baseline"
        assert "baseline=3" in violations[0].expected_pattern
        assert "current=1" in violations[0].expected_pattern

    def test_passes_when_current_equals_baseline(self, tmp_path):
        (tmp_path / "workspace.py").write_text(
            "api.add_resource(A)\napi.add_resource(B)\n"
        )
        git = _FakeGit(
            tmp_path,
            {
                "base-sha": {
                    "workspace.py": "api.add_resource(A)\napi.add_resource(B)\n"
                }
            },
        )
        judge = _make_judge(git_tool=git)
        cust = [
            CustomizationEntry(
                name="api.add_resource retention",
                files=["*.py"],
                verification=[
                    CustomizationVerification(
                        type="grep_count_baseline",
                        pattern=r"api\.add_resource",
                        files=["*.py"],
                    )
                ],
            )
        ]
        assert judge.verify_customizations(cust, merge_base="base-sha") == []

    def test_skips_when_baseline_zero(self, tmp_path):
        (tmp_path / "workspace.py").write_text("")
        git = _FakeGit(tmp_path, {"base-sha": {"workspace.py": "no matches here\n"}})
        judge = _make_judge(git_tool=git)
        cust = [
            CustomizationEntry(
                name="Retention",
                files=["*.py"],
                verification=[
                    CustomizationVerification(
                        type="grep_count_baseline",
                        pattern=r"api\.add_resource",
                        files=["*.py"],
                    )
                ],
            )
        ]
        assert judge.verify_customizations(cust, merge_base="base-sha") == []


# ─── P0-1: line_retention ─────────────────────────────────────────────────────


class TestLineRetention:
    def test_fails_when_retention_below_ratio(self, tmp_path):
        (tmp_path / "workflow.yml").write_text(
            "line1\nreplaced2\nreplaced3\nreplaced4\n"
        )
        baseline = "line1\nline2\nline3\nline4\n"
        git = _FakeGit(tmp_path, {"base-sha": {"workflow.yml": baseline}})
        judge = _make_judge(git_tool=git)
        cust = [
            CustomizationEntry(
                name="Workflow retention",
                files=["workflow.yml"],
                verification=[
                    CustomizationVerification(
                        type="line_retention",
                        files=["workflow.yml"],
                        retention_ratio=0.9,
                    )
                ],
            )
        ]
        violations = judge.verify_customizations(cust, merge_base="base-sha")
        assert len(violations) == 1
        assert violations[0].verification_type == "line_retention"

    def test_passes_when_retention_above_ratio(self, tmp_path):
        (tmp_path / "workflow.yml").write_text("line1\nline2\nline3\nline4\n")
        baseline = "line1\nline2\nline3\nline4\n"
        git = _FakeGit(tmp_path, {"base-sha": {"workflow.yml": baseline}})
        judge = _make_judge(git_tool=git)
        cust = [
            CustomizationEntry(
                name="Workflow retention",
                files=["workflow.yml"],
                verification=[
                    CustomizationVerification(
                        type="line_retention",
                        files=["workflow.yml"],
                        retention_ratio=0.9,
                    )
                ],
            )
        ]
        assert judge.verify_customizations(cust, merge_base="base-sha") == []


# ─── P0-2: ShadowConflictDetector ─────────────────────────────────────────────


class TestShadowConflictDetector:
    def test_detects_ts_vs_tsx(self):
        det = ShadowConflictDetector()
        result = det.detect(
            ["app/components/context.ts", "app/components/context.tsx", "other.ts"]
        )
        assert len(result) == 1
        assert {result[0].path_a, result[0].path_b} == {
            "app/components/context.ts",
            "app/components/context.tsx",
        }
        assert result[0].logical_name == "app/components/context"

    def test_detects_yaml_vs_yml(self):
        det = ShadowConflictDetector()
        result = det.detect([".github/workflows/ci.yaml", ".github/workflows/ci.yml"])
        assert len(result) == 1

    def test_detects_module_vs_package(self):
        det = ShadowConflictDetector()
        result = det.detect(["pkg/m.py", "pkg/m/__init__.py", "pkg/other.py"])
        assert len(result) == 1
        assert {result[0].path_a, result[0].path_b} == {
            "pkg/m.py",
            "pkg/m/__init__.py",
        }

    def test_no_conflict_without_pair(self):
        det = ShadowConflictDetector()
        assert det.detect(["a.ts", "b.tsx", "c.py"]) == []

    def test_extra_rules_appended(self):
        det = ShadowConflictDetector.from_config(
            [
                ShadowRuleConfig(
                    exts_a=[".toml"], exts_b=[".ini"], description="toml vs ini"
                )
            ]
        )
        result = det.detect(["cfg/app.toml", "cfg/app.ini"])
        assert len(result) == 1
        assert result[0].rule_description == "toml vs ini"

    def test_default_rules_count(self):
        assert len(DEFAULT_SHADOW_RULES) >= 7


class TestPlannerShadowUpgrade:
    def test_shadow_file_forced_to_human_required(self):
        from src.agents.planner_agent import PlannerAgent

        with patch("src.llm.client.LLMClientFactory.create"):
            planner = PlannerAgent(AgentLLMConfig())

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = "abc"
        state.file_categories = {
            "web/app/context.ts": FileChangeCategory.C,
            "web/app/context.tsx": FileChangeCategory.C,
            "web/app/other.ts": FileChangeCategory.B,
        }
        fd_ts = FileDiff(
            file_path="web/app/context.ts",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.1,
            change_category=FileChangeCategory.C,
        )
        fd_tsx = FileDiff(
            file_path="web/app/context.tsx",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.1,
            change_category=FileChangeCategory.C,
        )
        fd_other = FileDiff(
            file_path="web/app/other.ts",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.1,
            change_category=FileChangeCategory.B,
        )
        state.file_diffs = [fd_ts, fd_tsx, fd_other]

        plan = planner._build_layered_plan(state.file_diffs, state)

        human_files: set[str] = set()
        for p in plan.phases:
            if p.risk_level == RiskLevel.HUMAN_REQUIRED:
                human_files.update(p.file_paths)
        assert "web/app/context.ts" in human_files
        assert "web/app/context.tsx" in human_files
        assert len(state.shadow_conflicts) == 1


# ─── P0-3: TopLevelInvocationExtractor ────────────────────────────────────────


class TestTopLevelInvocationExtractor:
    def test_extracts_top_level_call(self):
        s = "api.add_resource(WorkspaceListApi, '/workspaces')\n"
        assert "api.add_resource" in _extract_top_level_invocations(s)

    def test_extracts_decorator(self):
        s = "@app.route('/hello')\ndef hello(): ...\n"
        assert "@app.route" in _extract_top_level_invocations(s)

    def test_skips_control_flow_heads(self):
        s = "if x(): pass\nwhile y(): pass\n"
        out = _extract_top_level_invocations(s)
        assert "if" not in out
        assert "while" not in out

    def test_missing_invocations_flagged(self, tmp_path):
        (tmp_path / "routes.py").write_text("def hello(): pass\n")

        git = _FakeGit(
            tmp_path,
            {
                "base": {"routes.py": "def hello(): pass\n"},
                "upstream/main": {
                    "routes.py": (
                        "def hello(): pass\n"
                        "api.add_resource(A, '/a')\n"
                        "api.add_resource(B, '/b')\n"
                    )
                },
            },
        )
        twd = ThreeWayDiff(git)  # type: ignore[arg-type]
        missing = twd.extract_missing_top_level_invocations(
            "routes.py", "base", "upstream/main"
        )
        assert "api.add_resource" in missing


# ─── P0-4: CrossLayerChecker ──────────────────────────────────────────────────


class TestCrossLayerChecker:
    def test_all_keys_present(self, tmp_path):
        (tmp_path / "types.ts").write_text(
            "export enum Kind {\n  A = 'A',\n  B = 'B',\n}\n"
        )
        (tmp_path / "registry.ts").write_text("const MAP = { A: 1, B: 2 };\n")

        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [
                CrossLayerAssertion(
                    name="Kind -> MAP",
                    keys_from=r"types.ts::^\s+(\w+)\s*=\s*'",
                    keys_in=["registry.ts"],
                )
            ]
        )
        assert results[0].missing_keys == []
        assert set(results[0].captured_keys) == {"A", "B"}

    def test_missing_key_flagged(self, tmp_path):
        (tmp_path / "types.ts").write_text(
            "export enum Kind {\n  A = 'A',\n  B = 'B',\n  C = 'C',\n}\n"
        )
        (tmp_path / "registry.ts").write_text("const MAP = { A: 1, B: 2 };\n")

        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [
                CrossLayerAssertion(
                    name="Kind -> MAP",
                    keys_from=r"types.ts::^\s+(\w+)\s*=\s*'",
                    keys_in=["registry.ts"],
                )
            ]
        )
        assert results[0].missing_keys == ["C"]

    def test_allow_missing_exempts_keys(self, tmp_path):
        (tmp_path / "types.ts").write_text(
            "  A = 'A'\n  B = 'B'\n  IterationStart = 'IterationStart'\n"
        )
        (tmp_path / "registry.ts").write_text("A, B\n")
        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [
                CrossLayerAssertion(
                    name="Kind -> MAP",
                    keys_from=r"types.ts::^\s+(\w+)\s*=\s*'",
                    keys_in=["registry.ts"],
                    allow_missing=["IterationStart"],
                )
            ]
        )
        assert results[0].missing_keys == []

    def test_invalid_keys_from_spec_surfaces_error(self, tmp_path):
        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [CrossLayerAssertion(name="bad", keys_from="no-separator-here", keys_in=[])]
        )
        assert results[0].error != ""

    def test_missing_source_surfaces_error(self, tmp_path):
        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [
                CrossLayerAssertion(
                    name="missing-src",
                    keys_from="nonexistent.ts::(\\w+)",
                    keys_in=["also-missing.ts"],
                )
            ]
        )
        assert "not found" in results[0].error

    def test_binary_source_does_not_crash(self, tmp_path):
        # PNG magic bytes — non-UTF-8. A naked read_text would raise
        # UnicodeDecodeError and abort the whole Judge pass; the checker
        # must surface the error on the result object instead.
        png_payload = b"\x89PNG\r\n\x1a\n\x00\x00\x00rest-of-binary"
        (tmp_path / "asset.png").write_bytes(png_payload)
        (tmp_path / "registry.ts").write_text("nothing to see here\n")

        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [
                CrossLayerAssertion(
                    name="png-as-source",
                    keys_from=r"asset.png::(\w+)",
                    keys_in=["registry.ts"],
                )
            ]
        )
        assert results[0].error != ""
        assert (
            "binary" in results[0].error.lower()
            or "unreadable" in results[0].error.lower()
        )
        assert results[0].missing_keys == []

    def test_binary_target_treated_as_missing(self, tmp_path):
        (tmp_path / "types.ts").write_text(
            "export enum Kind {\n  A = 'A',\n  B = 'B',\n}\n"
        )
        (tmp_path / "registry.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")

        checker = CrossLayerChecker(tmp_path)
        results = checker.check(
            [
                CrossLayerAssertion(
                    name="png-as-target",
                    keys_from=r"types.ts::^\s+(\w+)\s*=\s*'",
                    keys_in=["registry.png"],
                )
            ]
        )
        # Should not raise; binary target treated like a missing target —
        # all captured keys reported as missing.
        assert set(results[0].missing_keys) == {"A", "B"}


# ─── Judge deterministic pipeline: new VETOs ──────────────────────────────────


class TestJudgeDeterministicVetoes:
    def test_shadow_conflict_produces_veto_issue(self, tmp_path):
        from src.core.read_only_state_view import ReadOnlyStateView
        from src.tools.shadow_conflict_detector import ShadowConflict

        git_mock = MagicMock()
        git_mock.repo_path = tmp_path
        git_mock.get_file_content.return_value = None
        judge = _make_judge(git_tool=git_mock)

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            repo_path=str(tmp_path),
        )
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"
        state.file_categories = {"app/ctx.ts": FileChangeCategory.C}
        state.shadow_conflicts = [
            ShadowConflict(
                logical_name="app/ctx",
                path_a="app/ctx.ts",
                path_b="app/ctx.tsx",
                rule_description="ts vs tsx",
            )
        ]

        view = ReadOnlyStateView(state)
        issues = judge._run_deterministic_pipeline(view, {})
        shadow_issues = [
            i for i in issues if i.issue_type == "shadow_conflict_unresolved"
        ]
        assert len(shadow_issues) == 1
        assert shadow_issues[0].veto_condition == "Shadow-path conflict unresolved"

    def test_top_level_invocation_lost_veto(self, tmp_path):
        from src.core.read_only_state_view import ReadOnlyStateView

        (tmp_path / "routes.py").write_text("def hello(): pass\n")

        class _Git:
            def __init__(self, repo):
                self.repo_path = repo

            def get_file_content(self, ref, fp):
                if ref == "base-sha" and fp == "routes.py":
                    return "def hello(): pass\n"
                if ref == "upstream/main" and fp == "routes.py":
                    return "def hello(): pass\napi.add_resource(A)\n"
                return None

        judge = _make_judge(git_tool=_Git(tmp_path))
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            repo_path=str(tmp_path),
        )
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"
        state.file_categories = {"routes.py": FileChangeCategory.C}

        view = ReadOnlyStateView(state)
        issues = judge._check_top_level_invocations(
            view, {"routes.py": FileChangeCategory.C}
        )
        assert any(i.issue_type == "top_level_invocation_lost" for i in issues)

    def test_cross_layer_assertion_missing_veto(self, tmp_path):
        from src.core.read_only_state_view import ReadOnlyStateView

        (tmp_path / "types.ts").write_text("  A = 'A'\n  B = 'B'\n  C = 'C'\n")
        (tmp_path / "registry.ts").write_text("A, B\n")

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            repo_path=str(tmp_path),
            cross_layer_assertions=[
                CrossLayerAssertion(
                    name="Kind -> MAP",
                    keys_from=r"types.ts::^\s+(\w+)\s*=\s*'",
                    keys_in=["registry.ts"],
                )
            ],
        )
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"

        class _Git:
            def __init__(self, repo):
                self.repo_path = repo

        judge = _make_judge(git_tool=_Git(tmp_path))
        view = ReadOnlyStateView(state)
        issues = judge._check_cross_layer_assertions(view)
        assert any(i.issue_type == "cross_layer_assertion_missing" for i in issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
