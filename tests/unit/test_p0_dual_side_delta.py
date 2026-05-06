"""P0-1 tests: dual-side delta visibility + close C/AUTO_SAFE silent take_target.

These tests pin the contract introduced by the dify-plugin-daemon root-cause
fix:
- ``FileDiff`` exposes upstream-side line counts in addition to the existing
  fork-side counts (which are populated from ``git diff base..fork``).
- ``ExecutorAgent._select_strategy_by_category`` must NEVER return
  ``TAKE_TARGET`` for a C-class file, regardless of the planner's risk_level.
  C-class means both sides changed; silently overwriting fork loses fork work.
- The planner classification prompt surfaces the upstream-side delta so the
  LLM can downgrade a C-class file to AUTO_RISKY when upstream had a large
  refactor even if the fork delta is small.
- ``AutoMergePhase`` defers SEMANTIC_MERGE files (whether AUTO_SAFE or
  AUTO_RISKY) to ``ConflictAnalysisPhase`` instead of leaking them as
  unaccounted plan files.
"""

from __future__ import annotations

from src.agents.executor_agent import ExecutorAgent
from src.models.config import AgentLLMConfig
from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel


def _make_executor() -> ExecutorAgent:
    cfg = AgentLLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
    )
    return ExecutorAgent(cfg, git_tool=None)


class TestFileDiffUpstreamDelta:
    def test_upstream_line_counts_default_to_zero(self) -> None:
        fd = FileDiff(
            file_path="x.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.0,
        )
        assert fd.upstream_lines_added == 0
        assert fd.upstream_lines_deleted == 0

    def test_upstream_line_counts_are_distinct_from_fork(self) -> None:
        fd = FileDiff(
            file_path="x.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.0,
            lines_added=10,
            lines_deleted=2,
            upstream_lines_added=300,
            upstream_lines_deleted=150,
        )
        assert fd.lines_added == 10
        assert fd.upstream_lines_added == 300
        assert fd.upstream_lines_deleted == 150


class TestExecutorStrategyForCClass:
    def test_c_class_auto_safe_must_not_take_target(self) -> None:
        executor = _make_executor()
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.C, RiskLevel.AUTO_SAFE
        )
        assert strategy != MergeDecision.TAKE_TARGET, (
            "C-class + AUTO_SAFE must not silently overwrite fork content. "
            "It should be deferred to ConflictAnalyst (SEMANTIC_MERGE)."
        )
        assert strategy == MergeDecision.SEMANTIC_MERGE

    def test_c_class_auto_risky_remains_semantic_merge(self) -> None:
        executor = _make_executor()
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.C, RiskLevel.AUTO_RISKY
        )
        assert strategy == MergeDecision.SEMANTIC_MERGE

    def test_c_class_human_required_remains_escalate(self) -> None:
        executor = _make_executor()
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.C, RiskLevel.HUMAN_REQUIRED
        )
        assert strategy == MergeDecision.ESCALATE_HUMAN

    def test_b_class_auto_safe_still_takes_target(self) -> None:
        executor = _make_executor()
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.B, RiskLevel.AUTO_SAFE
        )
        assert strategy == MergeDecision.TAKE_TARGET


class TestPlannerPromptShowsUpstreamDelta:
    def test_file_list_line_includes_upstream_lines(self) -> None:
        from src.llm.prompts.planner_prompts import build_classification_prompt

        fd = FileDiff(
            file_path="internal/service/install_plugin.go",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.0,
            lines_added=20,
            lines_deleted=5,
            upstream_lines_added=600,
            upstream_lines_deleted=560,
            conflict_count=0,
            change_category=FileChangeCategory.C,
        )
        prompt = build_classification_prompt([fd], project_context="test")
        assert "fork_lines_added=20" in prompt
        assert "fork_lines_deleted=5" in prompt
        assert "upstream_lines_added=600" in prompt
        assert "upstream_lines_deleted=560" in prompt

    def test_classification_rules_mention_upstream_delta(self) -> None:
        from src.llm.prompts.planner_prompts import build_classification_prompt

        fd = FileDiff(
            file_path="x.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.0,
        )
        prompt = build_classification_prompt([fd], project_context="")
        assert "upstream_lines_added + upstream_lines_deleted" in prompt
        assert "auto_risky" in prompt
