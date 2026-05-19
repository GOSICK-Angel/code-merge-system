"""Unit tests for batch 3 optimizations from upstream-51 test report.

Covers:
- O-R1: ``CommitReplayer.classify_commits_with_partial`` 3-way split
- O-D1: Planner emits a ``layer_id=None`` D-missing fast-track batch
- O-E1: Empty-content runtime errors map to ``ErrorCategory.PROVIDER_EMPTY``
- O-F1: Sliding-window fallback activates on sustained error rate
- O-G1: ``_build_diff_preview`` fills ``preview_content`` on A/B options
- O-C2: ``STAGED_THRESHOLD_*`` defaults lowered
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agents.base_agent import (
    _SLIDING_WINDOW_FAILURE_RATIO,
    _SLIDING_WINDOW_MIN_SAMPLES,
    BaseAgent,
)
from src.core.phases.conflict_analysis import (
    _build_diff_preview,
    _build_human_decision_request,
)
from src.llm.error_classifier import ErrorCategory, classify_error
from src.llm.prompt_builders import STAGED_THRESHOLD_CHARS, STAGED_THRESHOLD_LINES
from src.models.conflict import (
    ChangeIntent,
    ConflictAnalysis,
    ConflictPoint,
    ConflictType,
)
from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.tools.commit_replayer import CommitReplayer


class TestOR1ClassifyCommitsWithPartial:
    def test_fully_replayable_commit(self):
        commits = [{"sha": "a", "files": ["f1"]}]
        categories = {"f1": FileChangeCategory.B}
        fully, partial, none = CommitReplayer().classify_commits_with_partial(
            commits, categories
        )
        assert fully and not partial and not none

    def test_partial_commit_records_clean_and_dirty(self):
        commits = [{"sha": "m", "files": ["f1", "f2", "f3"]}]
        categories = {
            "f1": FileChangeCategory.B,
            "f2": FileChangeCategory.C,
            "f3": FileChangeCategory.D_MISSING,
        }
        fully, partial, none = CommitReplayer().classify_commits_with_partial(
            commits, categories
        )
        assert not fully
        assert len(partial) == 1
        assert set(partial[0]["_replay_files"]) == {"f1", "f3"}
        assert partial[0]["_fallback_files"] == ["f2"]
        assert not none

    def test_all_dirty_commit_goes_to_none(self):
        commits = [{"sha": "d", "files": ["f1"]}]
        categories = {"f1": FileChangeCategory.C}
        fully, partial, none = CommitReplayer().classify_commits_with_partial(
            commits, categories
        )
        assert not fully
        assert not partial
        assert len(none) == 1


class TestOD1DMissingFastTrack:
    def test_fast_track_batch_emitted_with_no_layer(self):
        # Intentionally use the lightweight planner helper directly.
        from datetime import datetime

        from src.agents.planner_agent import PlannerAgent
        from src.models.config import AgentLLMConfig, MergeConfig

        fd_d = FileDiff(
            file_path="new/a.py",
            file_status=FileStatus.ADDED,
            change_category=FileChangeCategory.D_MISSING,
            risk_level=RiskLevel.AUTO_SAFE,
            upstream_sha="u",
            fork_sha=None,
            lines_added=10,
            lines_deleted=0,
            hunks=[],
            risk_score=0.1,
        )
        from unittest.mock import patch

        cfg = AgentLLMConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            api_key_env="ANTHROPIC_API_KEY",
        )
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}),
            patch("anthropic.AsyncAnthropic"),
        ):
            planner = PlannerAgent(cfg)
        state = MagicMock()
        state.config = MergeConfig(upstream_ref="u", fork_ref="f")
        state.file_categories = {"new/a.py": FileChangeCategory.D_MISSING}
        state.shadow_conflicts = []
        state.merge_base_commit = "abc123"
        state.pollution_audit = None
        state.config_drifts = None
        state.user_project_context = ""
        state.rename_pairs = []
        plan = planner._build_layered_plan([fd_d], state)
        fast_track = [b for b in plan.phases if b.layer_id is None]
        assert fast_track, "expected a fast-track batch"
        assert "new/a.py" in fast_track[0].file_paths
        assert fast_track[0].change_category == FileChangeCategory.D_MISSING


class TestOE1EmptyContentClassification:
    def test_openai_empty_content_is_provider_empty(self):
        err = RuntimeError(
            "OpenAI returned empty content (finish_reason='stop', model='gpt-4o')"
        )
        classified = classify_error(err, provider="openai")
        assert classified.category == ErrorCategory.PROVIDER_EMPTY
        assert classified.should_fallback is True
        assert classified.retryable is True

    def test_anthropic_no_text_blocks_is_provider_empty(self):
        err = RuntimeError(
            "Anthropic returned no text blocks (stop_reason='end_turn', model='x')"
        )
        classified = classify_error(err, provider="anthropic")
        assert classified.category == ErrorCategory.PROVIDER_EMPTY

    def test_generic_runtime_error_still_unknown(self):
        err = RuntimeError("some other runtime error")
        classified = classify_error(err, provider="openai")
        assert classified.category == ErrorCategory.UNKNOWN


class _FakeAgent:
    """Just enough of ``BaseAgent`` to exercise the sliding-window helpers."""

    _should_fallback_by_window = BaseAgent._should_fallback_by_window
    _sliding_window_failure_rate = BaseAgent._sliding_window_failure_rate


class TestOF1SlidingWindow:
    def test_threshold_constants_are_sensible(self):
        assert 0.0 < _SLIDING_WINDOW_FAILURE_RATIO <= 1.0
        assert _SLIDING_WINDOW_MIN_SAMPLES >= 1

    def test_below_min_samples_never_triggers(self):
        agent = _FakeAgent()
        from collections import deque

        agent._sliding_window = deque([False] * 5, maxlen=20)
        assert agent._should_fallback_by_window() is False

    def test_high_failure_rate_triggers_when_enough_samples(self):
        agent = _FakeAgent()
        from collections import deque

        # 15 samples, 12 failures → 80% > 60% threshold
        agent._sliding_window = deque([False] * 12 + [True] * 3, maxlen=20)
        assert agent._should_fallback_by_window() is True

    def test_low_failure_rate_does_not_trigger(self):
        agent = _FakeAgent()
        from collections import deque

        agent._sliding_window = deque([True] * 15 + [False] * 2, maxlen=20)
        assert agent._should_fallback_by_window() is False


class TestOG1DiffPreview:
    def test_generates_unified_diff_for_text_files(self):
        git_tool = MagicMock()
        git_tool.get_file_content.side_effect = lambda ref, _fp: (
            "line1\nline2\nline3\n"
            if "upstream" in ref
            else "line1\ndifferent\nline3\n"
        )
        take_target, take_current = _build_diff_preview(
            "a.py", "upstream/main", "fork/main", git_tool
        )
        assert "+line2" in take_target
        assert "-different" in take_target
        assert take_current and take_current != take_target

    def test_no_git_tool_returns_empty(self):
        target, current = _build_diff_preview("a.py", "u", "f", None)
        assert target == "" and current == ""

    def test_build_request_fills_preview_content(self):
        fd = FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            change_category=FileChangeCategory.C,
            risk_level=RiskLevel.HUMAN_REQUIRED,
            upstream_sha="u",
            fork_sha="f",
            lines_added=3,
            lines_deleted=1,
            hunks=[],
            risk_score=0.7,
        )
        analysis = ConflictAnalysis(
            file_path="a.py",
            conflict_points=[
                ConflictPoint(
                    file_path="a.py",
                    hunk_id="h1",
                    conflict_type=ConflictType.CONCURRENT_MODIFICATION,
                    upstream_intent=ChangeIntent(
                        description="u", intent_type="x", confidence=0.8
                    ),
                    fork_intent=ChangeIntent(
                        description="f", intent_type="y", confidence=0.8
                    ),
                    can_coexist=False,
                    suggested_decision=MergeDecision.ESCALATE_HUMAN,
                    confidence=0.6,
                    rationale="",
                )
            ],
            overall_confidence=0.6,
            recommended_strategy=MergeDecision.ESCALATE_HUMAN,
            conflict_type=ConflictType.CONCURRENT_MODIFICATION,
            can_coexist=False,
            is_security_sensitive=False,
            rationale="",
            confidence=0.6,
        )
        git_tool = MagicMock()
        git_tool.get_file_content.side_effect = lambda ref, _fp: (
            "alpha\nbeta\n" if "upstream" in ref else "alpha\ngamma\n"
        )
        req = _build_human_decision_request(
            fd,
            analysis,
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            git_tool=git_tool,
        )
        opts = {opt.option_key: opt for opt in req.options}
        assert opts["B"].preview_content is not None
        assert opts["A"].preview_content is not None
        assert opts["C"].preview_content is None  # semantic merge not previewed


class TestOC2StagedThresholds:
    def test_line_threshold_lowered(self):
        assert STAGED_THRESHOLD_LINES <= 250

    def test_char_threshold_lowered(self):
        assert STAGED_THRESHOLD_CHARS <= 10_000
