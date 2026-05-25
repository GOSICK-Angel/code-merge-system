"""Tests for the optional post-judge build/compile gate.

A non-zero ``build_check.command`` exit downgrades a Judge PASS to FAIL with a
veto, catching cross-file compilation breaks the per-file Judge review cannot
see. The gate is disabled and command-empty by default so the agent stays
target-agnostic.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.core.phases.judge_review import JudgeReviewPhase
from src.models.config import BuildCheckConfig, MergeConfig
from src.models.judge import VerdictType
from src.models.state import MergeState


def _pass_verdict():
    from src.models.judge import JudgeVerdict

    return JudgeVerdict(
        verdict=VerdictType.PASS,
        reviewed_files_count=1,
        passed_files=["a.go"],
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


def _state(tmp_path, command: str, *, enabled: bool = True, timeout: int = 600):
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        repo_path=str(tmp_path),
        build_check=BuildCheckConfig(
            enabled=enabled, command=command, timeout_seconds=timeout
        ),
    )
    state = MergeState(config=config)
    state.judge_verdict = _pass_verdict()
    return state


@pytest.mark.asyncio
async def test_build_failure_vetoes_pass(tmp_path) -> None:
    state = _state(tmp_path, "exit 1")
    await JudgeReviewPhase()._run_build_check(state, MagicMock())
    assert state.judge_verdict is not None
    assert state.judge_verdict.verdict == VerdictType.FAIL
    assert state.judge_verdict.veto_triggered
    assert "Build check failed" in (state.judge_verdict.veto_reason or "")
    assert state.judge_verdict.critical_issues_count == 1
    issue_types = {i.issue_type for i in state.judge_verdict.issues}
    assert "build_check_failed" in issue_types


@pytest.mark.asyncio
async def test_build_success_keeps_pass(tmp_path) -> None:
    state = _state(tmp_path, "exit 0")
    await JudgeReviewPhase()._run_build_check(state, MagicMock())
    assert state.judge_verdict is not None
    assert state.judge_verdict.verdict == VerdictType.PASS
    assert not state.judge_verdict.veto_triggered


@pytest.mark.asyncio
async def test_build_check_disabled_noop(tmp_path) -> None:
    state = _state(tmp_path, "exit 1", enabled=False)
    await JudgeReviewPhase()._run_build_check(state, MagicMock())
    assert state.judge_verdict is not None
    assert state.judge_verdict.verdict == VerdictType.PASS


@pytest.mark.asyncio
async def test_empty_command_noop(tmp_path) -> None:
    state = _state(tmp_path, "   ")
    await JudgeReviewPhase()._run_build_check(state, MagicMock())
    assert state.judge_verdict is not None
    assert state.judge_verdict.verdict == VerdictType.PASS


@pytest.mark.asyncio
async def test_build_timeout_vetoes_pass(tmp_path) -> None:
    state = _state(tmp_path, "sleep 5", timeout=1)
    await JudgeReviewPhase()._run_build_check(state, MagicMock())
    assert state.judge_verdict is not None
    assert state.judge_verdict.verdict == VerdictType.FAIL
    assert state.judge_verdict.veto_triggered


def test_build_check_config_defaults() -> None:
    config = MergeConfig(upstream_ref="u", fork_ref="f")
    assert config.build_check.enabled is False
    assert config.build_check.command == ""
    assert config.build_check.timeout_seconds >= 1
