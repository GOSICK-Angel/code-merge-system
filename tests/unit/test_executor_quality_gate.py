"""Executor-side regression net for the merge-output quality gate.

Pins the contract between ``execute_semantic_merge`` and
``parse_merge_result``: when the LLM returns prose preamble or hits a
``max_tokens`` truncation, the executor MUST escalate to human review
instead of writing the bad content to disk. The same scenario must
also propagate ``stop_reason``/preamble flags to ``build_rebuttal``
so the dispute round can tell the LLM what went wrong on the last
attempt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.executor_agent import ExecutorAgent
from src.llm.client import LLMResponse
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState


def _make_executor(
    monkeypatch, current_content: str = "", target_content: str = ""
) -> ExecutorAgent:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    git_tool = MagicMock()
    git_tool.get_file_content = MagicMock(
        side_effect=lambda ref, path: (
            current_content if ref.endswith("fork") or "fork" in ref else target_content
        )
    )
    return ExecutorAgent(
        llm_config=AgentLLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        git_tool=git_tool,
    )


@pytest.fixture
def executor(monkeypatch) -> ExecutorAgent:
    return _make_executor(monkeypatch)


@pytest.fixture
def state(tmp_path) -> MergeState:
    return MergeState(
        config=MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            repo_path=str(tmp_path),
        )
    )


def _make_file_diff(path: str = "foo.go") -> FileDiff:
    return FileDiff(
        file_path=path,
        change_category=FileChangeCategory.C,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        upstream_diff="",
        fork_diff="",
        language="go",
    )


def _make_conflict_analysis() -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="foo.go",
        conflict_points=[],
        overall_confidence=0.8,
        conflict_type=ConflictType.CONCURRENT_MODIFICATION,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
    )


@pytest.mark.asyncio
async def test_semantic_merge_truncation_escalates(monkeypatch, state):
    """When the LLM returns ``stop_reason='max_tokens'`` the merge
    is escalated and the truncated bytes never reach apply_with_snapshot."""
    executor = _make_executor(
        monkeypatch,
        current_content="package main\nfunc one() {}\n" * 20,
        target_content="package main\nfunc one() {}\nfunc two() {}\n" * 20,
    )
    executor._call_llm_with_retry_meta = AsyncMock(
        return_value=LLMResponse(
            text="package main\n\nfunc main() {\n\t// truncated mid-block",
            stop_reason="max_tokens",
        )
    )
    fake_apply = AsyncMock()
    with patch("src.agents.executor_agent.apply_with_snapshot", fake_apply):
        record = await executor.execute_semantic_merge(
            file_diff=_make_file_diff(),
            conflict_analysis=_make_conflict_analysis(),
            state=state,
        )

    assert record.decision == MergeDecision.ESCALATE_HUMAN
    fake_apply.assert_not_called()
    # last_merge_stop_reason was captured for downstream rebuttal use.
    assert executor._last_merge_stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_semantic_merge_prose_preamble_escalates(monkeypatch, state):
    """When the LLM returns chain-of-thought preamble the parser
    rejects it and the executor escalates. The flag also surfaces on
    ``_last_merge_had_prose_preamble`` for the dispute round."""
    executor = _make_executor(
        monkeypatch,
        current_content="package main\n" * 20,
        target_content="package main\n" * 20,
    )
    executor._call_llm_with_retry_meta = AsyncMock(
        return_value=LLMResponse(
            text=(
                "Looking at the current content, I'll merge them as follows:\n\n"
                "package main\n\nfunc main() {}\n"
            ),
            stop_reason="stop",
        )
    )
    fake_apply = AsyncMock()
    with patch("src.agents.executor_agent.apply_with_snapshot", fake_apply):
        record = await executor.execute_semantic_merge(
            file_diff=_make_file_diff(),
            conflict_analysis=_make_conflict_analysis(),
            state=state,
        )

    assert record.decision == MergeDecision.ESCALATE_HUMAN
    fake_apply.assert_not_called()
    assert executor._last_merge_had_prose_preamble is True


@pytest.mark.asyncio
async def test_semantic_merge_clean_output_passes(monkeypatch, state):
    """Sanity check: a clean LLM response must NOT be rejected by the
    quality gate. Without this the gate would refuse every merge."""
    executor = _make_executor(
        monkeypatch,
        current_content="package main\nfunc old() {}\n",
        target_content="package main\nfunc new() {}\n",
    )
    clean = 'package main\n\nfunc main() {\n\tprint("ok")\n}\n'
    executor._call_llm_with_retry_meta = AsyncMock(
        return_value=LLMResponse(text=clean, stop_reason="stop")
    )

    captured_content: dict[str, str] = {}

    async def fake_apply(file_path, merged_content, *args, **kwargs):
        captured_content["body"] = merged_content
        return MagicMock(decision=MergeDecision.SEMANTIC_MERGE)

    with patch("src.agents.executor_agent.apply_with_snapshot", side_effect=fake_apply):
        record = await executor.execute_semantic_merge(
            file_diff=_make_file_diff(),
            conflict_analysis=_make_conflict_analysis(),
            state=state,
        )

    assert record.decision == MergeDecision.SEMANTIC_MERGE
    assert captured_content["body"].startswith("package main")
    assert executor._last_merge_stop_reason == "stop"
    assert executor._last_merge_had_prose_preamble is False


@pytest.mark.asyncio
async def test_rebuttal_prompt_includes_last_stop_reason(executor, state):
    """``build_rebuttal`` must thread ``last_stop_reason`` into the
    prompt so the LLM's dispute round can see "your last output was
    truncated" instead of mechanically regenerating identical garbage."""
    from src.models.judge import IssueSeverity, JudgeIssue

    # Simulate a prior truncated merge.
    executor._last_merge_stop_reason = "max_tokens"
    executor._last_merge_had_prose_preamble = False

    captured_prompts: list[str] = []

    async def fake_call(messages, system=None):
        captured_prompts.append(messages[0]["content"])
        return (
            '{"accepts_all": false, "decisions": ['
            '{"issue_id": "i1", "action": "accept"}'
            '], "overall_rationale": "ok"}'
        )

    executor._call_llm_with_retry = AsyncMock(side_effect=fake_call)

    issues = [
        JudgeIssue(
            file_path="foo.go",
            issue_level=IssueSeverity.HIGH,
            issue_type="syntax_error",
            description="truncated mid-block",
            must_fix_before_merge=True,
        )
    ]
    await executor.build_rebuttal(issues, state)

    assert captured_prompts, "expected at least one rebuttal LLM call"
    prompt = captured_prompts[0]
    assert "TRUNCATED" in prompt
    assert "OUTPUT_TOO_LARGE" in prompt


@pytest.mark.asyncio
async def test_rebuttal_prompt_omits_block_when_no_prior_failure(executor, state):
    """Backwards-compat: when no prior failure metadata is set the
    rebuttal prompt is identical to its legacy form. Avoids hot-loading
    a useless "no findings" block on every dispute round."""
    from src.models.judge import IssueSeverity, JudgeIssue

    executor._last_merge_stop_reason = "stop"
    executor._last_merge_had_prose_preamble = False

    captured_prompts: list[str] = []

    async def fake_call(messages, system=None):
        captured_prompts.append(messages[0]["content"])
        return (
            '{"accepts_all": true, "decisions": ['
            '{"issue_id": "i1", "action": "accept"}'
            '], "overall_rationale": "ok"}'
        )

    executor._call_llm_with_retry = AsyncMock(side_effect=fake_call)

    issues = [
        JudgeIssue(
            file_path="foo.go",
            issue_level=IssueSeverity.HIGH,
            issue_type="x",
            description="d",
            must_fix_before_merge=True,
        )
    ]
    await executor.build_rebuttal(issues, state)

    assert captured_prompts
    prompt = captured_prompts[0]
    assert "Prior-Round Quality-Gate Findings" not in prompt
    assert "TRUNCATED" not in prompt
