"""Unit tests for batch 2 optimizations from upstream-51 test report.

Covers:
- O-C1: per-agent ``max_tokens`` defaults
- O-P1: ``AgentLLMConfig.repair_max_file_chars`` is configurable
- O-J1: Judge skips high-confidence records when local syntax validates
- O-J2: Judge freezes scope to prior-round issues in dispute rounds
- O-M2: memory extraction triggers on meta-review directives
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.judge_agent import JudgeAgent
from src.models.config import AgentLLMConfig, AgentsLLMConfig, MemoryExtractionConfig
from src.models.judge import IssueSeverity, JudgeIssue


def _make_judge() -> JudgeAgent:
    cfg = AgentLLMConfig(
        provider="anthropic",
        model="claude-opus-4-6",
        api_key_env="ANTHROPIC_API_KEY",
    )
    with (
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}),
        patch("anthropic.AsyncAnthropic"),
    ):
        return JudgeAgent(cfg, git_tool=None)


class TestOC1MaxTokensDefaults:
    def test_judge_default_max_tokens_shrunk(self):
        cfg = AgentsLLMConfig()
        assert cfg.judge.max_tokens == 2048

    def test_executor_default_max_tokens(self):
        cfg = AgentsLLMConfig()
        assert cfg.executor.max_tokens == 32768
        assert cfg.executor.reasoning_effort == "medium"

    def test_conflict_analyst_default_max_tokens(self):
        cfg = AgentsLLMConfig()
        assert cfg.conflict_analyst.max_tokens == 4096

    def test_planner_keeps_generous_budget(self):
        cfg = AgentsLLMConfig()
        assert cfg.planner.max_tokens == 8192


class TestOP1RepairMaxFileChars:
    def test_default_matches_legacy_constant(self):
        cfg = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
        )
        assert cfg.repair_max_file_chars == 30_000

    def test_override_is_respected(self):
        cfg = AgentLLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
            repair_max_file_chars=80_000,
        )
        assert cfg.repair_max_file_chars == 80_000

    def test_below_floor_rejected(self):
        with pytest.raises(Exception):
            AgentLLMConfig(
                provider="openai",
                model="gpt-4o",
                api_key_env="OPENAI_API_KEY",
                repair_max_file_chars=100,
            )


class TestOJ2IssueFingerprinting:
    def test_same_file_same_type_match(self):
        issue_a = JudgeIssue(
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="something",
        )
        issue_b = JudgeIssue(
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="reworded",
        )
        assert JudgeAgent._issue_fingerprint(issue_a) == JudgeAgent._issue_fingerprint(
            issue_b
        )

    def test_different_type_differs(self):
        a = JudgeIssue(
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="x",
        )
        b = JudgeIssue(
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="missing_logic",
            description="x",
        )
        assert JudgeAgent._issue_fingerprint(a) != JudgeAgent._issue_fingerprint(b)


class TestOJ2FreezeToPriorIssues:
    def test_keeps_matching_issues_with_prior_id(self):
        judge = _make_judge()
        prior = JudgeIssue(
            issue_id="stable-id-1",
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="initial wording",
        )
        current = JudgeIssue(
            issue_id="fresh-uuid",
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="slightly different wording",
        )
        kept = judge._freeze_to_prior_issues([current], [prior])
        assert len(kept) == 1
        assert kept[0].issue_id == "stable-id-1"

    def test_drops_brand_new_issues(self):
        judge = _make_judge()
        prior = JudgeIssue(
            issue_id="k",
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="x",
        )
        new = JudgeIssue(
            file_path="b.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="missing_logic",
            description="y",
        )
        kept = judge._freeze_to_prior_issues([new], [prior])
        assert kept == []

    def test_deduplicates_repeated_fingerprints(self):
        judge = _make_judge()
        prior = JudgeIssue(
            issue_id="k",
            file_path="a.py",
            issue_level=IssueSeverity.HIGH,
            issue_type="wrong_merge",
            description="x",
        )
        current_dup = [
            JudgeIssue(
                file_path="a.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="wrong_merge",
                description="x",
            ),
            JudgeIssue(
                file_path="a.py",
                issue_level=IssueSeverity.HIGH,
                issue_type="wrong_merge",
                description="x (echo)",
            ),
        ]
        kept = judge._freeze_to_prior_issues(current_dup, [prior])
        assert len(kept) == 1
        assert kept[0].issue_id == "k"


class TestOJ1LocalSyntaxOkWithoutGit:
    def test_returns_false_without_git_tool(self):
        judge = _make_judge()
        assert judge._local_syntax_ok("whatever.py") is False


class TestOM2MemoryExtractionConfig:
    def test_min_rounds_lowered_to_one(self):
        cfg = MemoryExtractionConfig()
        assert cfg.min_judge_repair_rounds == 1

    def test_meta_review_default_enabled(self):
        cfg = MemoryExtractionConfig()
        assert cfg.extract_on_meta_review is True


class TestOM2ShouldLLMExtractMetaReview:
    """Ensure Orchestrator._should_llm_extract picks up meta-review directives
    without needing a full dispute round."""

    def test_triggers_on_judge_stall_directive(self):
        from src.core.orchestrator import Orchestrator
        from src.models.coordinator import MetaReviewResult
        from src.models.config import MergeConfig
        from src.models.state import MergeState

        state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
        state.judge_repair_rounds = 0
        state.coordinator_directives.append(
            MetaReviewResult(
                phase="judge_review",
                trigger="judge_stall",
                assessment="blocked",
                recommendation="escalate",
                raw_response="{}",
            )
        )
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = MergeConfig(upstream_ref="u", fork_ref="f")
        assert orch._should_llm_extract("judge_review", state) is True

    def test_no_trigger_when_empty(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig
        from src.models.state import MergeState

        state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
        state.judge_repair_rounds = 0
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = MergeConfig(upstream_ref="u", fork_ref="f")
        assert orch._should_llm_extract("judge_review", state) is False


class TestJudgeSkipFilterOnSkipDecision:
    """Regression: SKIP-decision files must not trigger deterministic syntax
    checks. Empty/missing worktree paths previously tripped ``_check_json("")``
    → ``[critical] syntax_error`` for every skipped .json file, exhausting the
    Executor↔Judge dispute loop and dumping placeholder HumanDecisionRequest
    entries on the user.
    """

    async def test_review_batch_skips_skip_decision_json_files(self, tmp_path):
        import git as _git

        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig, MergeConfig, OutputConfig
        from src.models.decision import (
            DecisionSource,
            FileDecisionRecord,
            MergeDecision,
        )
        from src.models.diff import FileDiff, FileStatus, RiskLevel
        from src.models.state import MergeState
        from src.tools.git_tool import GitTool

        _git.Repo.init(str(tmp_path))
        # No file written → abs_path will not exist; previously this would
        # have produced ``merged_content=""`` and trigger _check_json("").
        target = "config.json"

        cfg = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            repo_path=str(tmp_path),
            output=OutputConfig(directory=str(tmp_path / "outputs")),
        )
        state = MergeState(config=cfg)
        state.file_diffs.append(
            FileDiff(
                file_path=target,
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_SAFE,
                risk_score=0.2,
            )
        )
        state.file_decision_records[target] = FileDecisionRecord(
            file_path=target,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.SKIP,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.8,
            rationale="executor chose to skip",
        )

        judge = JudgeAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=GitTool(str(tmp_path)),
        )
        from src.core.read_only_state_view import ReadOnlyStateView

        verdict = await judge.review_batch(
            layer_id=None,
            file_paths=[target],
            state=ReadOnlyStateView(state),
        )
        # SKIP filter must drop the file entirely → no syntax_error issues,
        # batch approved by default.
        assert verdict.approved is True
        syntax_issues = [i for i in verdict.issues if i.issue_type == "syntax_error"]
        assert syntax_issues == []

    async def test_review_batch_still_checks_non_skip_decisions(self, tmp_path):
        """Guard: only SKIP decisions get filtered; SEMANTIC_MERGE / TAKE_*
        decisions still go through normal syntax check."""
        import git as _git

        from src.agents.judge_agent import JudgeAgent
        from src.models.config import AgentLLMConfig, MergeConfig, OutputConfig
        from src.models.decision import (
            DecisionSource,
            FileDecisionRecord,
            MergeDecision,
        )
        from src.models.diff import FileDiff, FileStatus, RiskLevel
        from src.core.read_only_state_view import ReadOnlyStateView
        from src.models.state import MergeState
        from src.tools.git_tool import GitTool

        _git.Repo.init(str(tmp_path))
        target = "broken.json"
        (tmp_path / target).write_text("{not valid json")

        cfg = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            repo_path=str(tmp_path),
            output=OutputConfig(directory=str(tmp_path / "outputs")),
        )
        state = MergeState(config=cfg)
        state.file_diffs.append(
            FileDiff(
                file_path=target,
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_SAFE,
                risk_score=0.2,
            )
        )
        state.file_decision_records[target] = FileDecisionRecord(
            file_path=target,
            file_status=FileStatus.MODIFIED,
            decision=MergeDecision.SEMANTIC_MERGE,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            confidence=0.8,
            rationale="merged",
        )
        judge = JudgeAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=GitTool(str(tmp_path)),
        )
        verdict = await judge.review_batch(
            layer_id=None,
            file_paths=[target],
            state=ReadOnlyStateView(state),
        )
        syntax_issues = [i for i in verdict.issues if i.issue_type == "syntax_error"]
        assert len(syntax_issues) >= 1, "broken JSON content must still flag"
