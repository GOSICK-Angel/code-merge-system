"""Unit tests for the cherry-pick replay idempotency guard (P0 hang fix).

Three regressions are covered:

1. ``AutoMergePhase`` must skip ``CommitReplayer.replay_clean_commits`` when
   ``state.replayed_commits`` is non-empty, even if ``rerun_round == 0``.
   This is the AWAITING_HUMAN-induced re-entry path (plan_review /
   conflict_marker / binary_escalate resume) that previously caused the
   18-minute hang on dify-plugins/upstream25 (Run 6dd6a513).

2. ``GitTool.cherry_pick_abort`` returns ``True`` on success and ``False``
   on ``GitCommandError``, instead of silently swallowing failure. Callers
   need observability to break out of strategy ladders when the worktree
   is stuck.

3. ``GitTool.cherry_pick_strategy_ladder`` must short-circuit and return
   ``(False, label)`` when ``cherry_pick_abort`` itself fails — otherwise
   the next ladder strategy hits "previous cherry-pick still in progress"
   and the loop cascades for the remainder of the replayable_commits list.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import git
import pytest

from src.core.phases import auto_merge as auto_merge_mod
from src.models.config import MergeConfig
from src.models.plan import MergePlan, RiskSummary
from src.models.state import MergeState
from src.tools.git_tool import GitTool


def _make_minimal_state_with_replay() -> MergeState:
    cfg = MergeConfig(upstream_ref="upstream", fork_ref="fork")
    cfg.history.enabled = True
    cfg.history.cherry_pick_clean = True
    state = MergeState(config=cfg)
    state.merge_plan = MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream",
        fork_ref="fork",
        merge_base_commit="base",
        phases=[],
        risk_summary=RiskSummary(
            total_files=0,
            auto_safe_count=0,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )
    state.file_diffs = []
    return state


@pytest.mark.asyncio
async def test_auto_merge_skips_replay_when_already_replayed(monkeypatch):
    """AWAITING_HUMAN resume regression: rerun_round stays 0 across
    plan_review / conflict_marker resumes, but the worktree already holds
    the prior pass's cherry-picks. ``state.replayed_commits`` is the
    durable signal that replay already produced commits this run, so the
    second entry into AutoMergePhase must short-circuit the replay branch.
    """
    state = _make_minimal_state_with_replay()
    state.rerun_round = 0
    state.replayed_commits = ["abc12345", "def67890"]
    state.replayable_commits = [
        {"sha": "abc12345", "files": ["a.py"]},
        {"sha": "def67890", "files": ["b.py"]},
    ]
    state.partial_replayable_commits = []

    ctx = MagicMock()
    ctx.config = state.config
    ctx.git_tool.repo_path = MagicMock()
    ctx.agents = {"executor": MagicMock(), "judge": MagicMock()}

    called = {"clean": 0, "partial": 0}

    class FakeReplayer:
        async def replay_clean_commits(self, *args, **kwargs):
            called["clean"] += 1
            return MagicMock(
                replayed_files=[],
                replayed_shas=[],
                partial_replays=[],
                failed_shas=[],
            )

        async def replay_partial_commits(self, *args, **kwargs):
            called["partial"] += 1

    monkeypatch.setattr(auto_merge_mod, "CommitReplayer", FakeReplayer)

    try:
        await auto_merge_mod.AutoMergePhase().execute(state, ctx)
    except Exception:
        # Downstream stages need fixtures we do not provide; we only
        # assert the replay-branch short-circuit, mirroring the existing
        # test_p2_rerun_incremental skip-on-rerun test.
        pass

    assert called["clean"] == 0, (
        "replay_clean_commits must be skipped when "
        "state.replayed_commits is already populated"
    )
    assert called["partial"] == 0, (
        "replay_partial_commits must be skipped when prior replay produced "
        "commits — guard mirrors clean-replay's"
    )


@pytest.mark.asyncio
async def test_auto_merge_runs_replay_on_first_entry(monkeypatch):
    """Sanity counter-check: when nothing has been replayed yet,
    AutoMergePhase MUST invoke the replayer. Regressions that flipped the
    guard polarity would silently strand the entire upstream history."""
    state = _make_minimal_state_with_replay()
    state.rerun_round = 0
    state.replayed_commits = []
    state.replayable_commits = [{"sha": "abc12345", "files": ["a.py"]}]
    state.partial_replayable_commits = []

    ctx = MagicMock()
    ctx.config = state.config
    ctx.git_tool.repo_path = MagicMock()
    ctx.agents = {"executor": MagicMock(), "judge": MagicMock()}

    called = {"clean": 0}

    class FakeReplayer:
        async def replay_clean_commits(self, *args, **kwargs):
            called["clean"] += 1
            return MagicMock(
                replayed_files=[],
                replayed_shas=[],
                partial_replays=[],
                failed_shas=[],
            )

        async def replay_partial_commits(self, *args, **kwargs):
            return None

    monkeypatch.setattr(auto_merge_mod, "CommitReplayer", FakeReplayer)

    try:
        await auto_merge_mod.AutoMergePhase().execute(state, ctx)
    except Exception:
        pass

    assert called["clean"] == 1, (
        "First-entry replay must run; the new guard accidentally blocking "
        "it would silently strand all upstream commits"
    )


def _make_git_tool_with_mock_repo() -> GitTool:
    """Bypass GitTool.__init__'s Repo() probe by constructing a hollow
    instance and stubbing the .repo attribute. Keeping the real GitTool
    class on the test target ensures we exercise the actual abort + ladder
    code paths, not a re-implementation."""
    tool = GitTool.__new__(GitTool)
    tool.repo = MagicMock()
    tool.repo_path = MagicMock()
    return tool


class TestCherryPickAbortReturnValue:
    def test_abort_success_returns_true(self):
        tool = _make_git_tool_with_mock_repo()
        tool.repo.git.cherry_pick = MagicMock(return_value=None)
        assert tool.cherry_pick_abort() is True

    def test_abort_failure_returns_false_and_logs(self, caplog):
        tool = _make_git_tool_with_mock_repo()
        tool.repo.git.cherry_pick = MagicMock(
            side_effect=git.GitCommandError(
                "cherry-pick", 128, b"fatal: no in-progress"
            )
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="src.tools.git_tool"):
            result = tool.cherry_pick_abort()
        assert result is False
        assert any(
            "cherry_pick_abort failed" in rec.message for rec in caplog.records
        ), "abort failure must emit a WARNING log for observability"


class TestStrategyLadderShortCircuitOnAbortFailure:
    """When cherry-pick fails AND abort fails, the ladder must NOT keep
    trying additional strategies — every subsequent ``git cherry-pick``
    will hit "previous cherry-pick still in progress" and waste minutes
    of subprocess time. This is the precise hang vector observed in the
    Run 6dd6a513 P0 (silent 18-min hang)."""

    def test_ladder_bails_out_when_abort_fails(self):
        tool = _make_git_tool_with_mock_repo()
        cherry_pick_calls: list[tuple] = []

        def fake_cherry_pick(*args):
            if args and args[0] == "--abort":
                raise git.GitCommandError("cherry-pick --abort", 128, b"fatal")
            cherry_pick_calls.append(args)
            raise git.GitCommandError("cherry-pick", 1, b"conflict")

        tool.repo.git.cherry_pick = fake_cherry_pick

        ok, label = tool.cherry_pick_strategy_ladder("abc12345")

        assert ok is False
        assert label == "default", (
            "ladder must return the strategy that triggered the bail-out, "
            "not the last entry of the ladder"
        )
        assert len(cherry_pick_calls) == 1, (
            f"ladder must stop after the first abort failure; got "
            f"{len(cherry_pick_calls)} cherry_pick attempts: {cherry_pick_calls}"
        )

    def test_ladder_continues_when_abort_succeeds(self):
        tool = _make_git_tool_with_mock_repo()
        cherry_pick_attempts: list[tuple] = []

        def fake_cherry_pick(*args):
            if args and args[0] == "--abort":
                return None
            cherry_pick_attempts.append(args)
            raise git.GitCommandError("cherry-pick", 1, b"conflict")

        tool.repo.git.cherry_pick = fake_cherry_pick

        ok, label = tool.cherry_pick_strategy_ladder("abc12345")

        assert ok is False
        assert len(cherry_pick_attempts) == 3, (
            "with successful aborts the ladder must walk all 3 default "
            "strategies (default, -X theirs, --strategy=recursive -X patience)"
        )
