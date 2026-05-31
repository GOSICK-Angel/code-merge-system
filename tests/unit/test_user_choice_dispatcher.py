"""Bug 1 (zod validation, 2026-05-28): shared O-L5 dispatcher behaviour.

Originally inlined in auto_merge.py; extracted so human_review can actualize
part1-surfaced items too. These tests cover the per-choice behaviour and the
no-op cases, and verify the surfaced-item dispatch path that the bug fix
restored: a part1-surfaced UserDecisionItem with user_choice=take_target
rewrites the FileDecisionRecord from ESCALATE_HUMAN to TAKE_TARGET and writes
the upstream content to the worktree.
"""

from __future__ import annotations

import subprocess

import pytest

from src.models.config import MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.plan_review import UserDecisionItem
from src.models.state import MergeState
from src.tools.git_tool import GitTool
from src.tools.user_choice_dispatcher import dispatch_user_choice


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "src.ts").write_text("FORK CONTENT\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "src.ts"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fork"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "fork"], check=True)
    (repo / "src.ts").write_text("UPSTREAM CONTENT\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "src.ts"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "upstream"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "branch", "upstream"], check=True)
    # Working tree on fork.
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "fork"], check=True)
    return repo


def _state_with_surfaced(file_path: str, user_choice: str | None) -> MergeState:
    state = MergeState(config=MergeConfig(upstream_ref="upstream", fork_ref="fork"))
    state.file_decision_records[file_path] = FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.ESCALATE_HUMAN,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.0,
        rationale="internal escalate",
    )
    state.pending_user_decisions.append(
        UserDecisionItem(
            item_id=f"internal_escalation_{file_path}",
            file_path=file_path,
            description="surfaced",
            risk_context="internal_escalation",
            current_classification="human_required",
            user_choice=user_choice,
        )
    )
    return state


class TestDispatchUserChoice:
    @pytest.mark.asyncio
    async def test_take_target_writes_upstream_and_rewrites_record(self, tmp_path):
        repo = _make_repo(tmp_path)
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "take_target")

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == {"src.ts"}
        rec = state.file_decision_records["src.ts"]
        assert rec.decision == MergeDecision.TAKE_TARGET
        assert rec.agent == "user_choice_executor"
        assert (repo / "src.ts").read_text() == "UPSTREAM CONTENT\n"

    @pytest.mark.asyncio
    async def test_take_current_writes_fork_content(self, tmp_path):
        repo = _make_repo(tmp_path)
        # Pollute working tree so we can see take_current restoring fork.
        (repo / "src.ts").write_text("STALE\n", encoding="utf-8")
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "take_current")

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == {"src.ts"}
        rec = state.file_decision_records["src.ts"]
        assert rec.decision == MergeDecision.TAKE_CURRENT
        assert (repo / "src.ts").read_text() == "FORK CONTENT\n"

    @pytest.mark.asyncio
    async def test_skip_records_skip_no_write(self, tmp_path):
        repo = _make_repo(tmp_path)
        before = (repo / "src.ts").read_text()
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "skip")

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == {"src.ts"}
        rec = state.file_decision_records["src.ts"]
        assert rec.decision == MergeDecision.SKIP
        assert rec.decision_source == DecisionSource.HUMAN
        assert (repo / "src.ts").read_text() == before

    @pytest.mark.asyncio
    async def test_manual_paste_without_content_keeps_escalate_human(self, tmp_path):
        repo = _make_repo(tmp_path)
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "manual_paste")
        # No manual_resolution provided.

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == {"src.ts"}
        rec = state.file_decision_records["src.ts"]
        assert rec.decision == MergeDecision.ESCALATE_HUMAN
        # The "kept escalate" record is HUMAN-sourced because the user did
        # answer — the DROPPED guard recognises this and does NOT flag it.
        assert rec.decision_source == DecisionSource.HUMAN

    @pytest.mark.asyncio
    async def test_manual_paste_with_content_writes_verbatim(self, tmp_path):
        repo = _make_repo(tmp_path)
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "manual_paste")
        state.pending_user_decisions[0].manual_resolution = "HAND-RESOLVED\n"

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == {"src.ts"}
        rec = state.file_decision_records["src.ts"]
        assert rec.decision == MergeDecision.MANUAL_PATCH
        assert (repo / "src.ts").read_text() == "HAND-RESOLVED\n"

    @pytest.mark.asyncio
    async def test_no_user_choice_noop(self, tmp_path):
        repo = _make_repo(tmp_path)
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", None)

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == set()
        rec = state.file_decision_records["src.ts"]
        # Unchanged — still ESCALATE_HUMAN/AUTO_EXECUTOR.
        assert rec.decision == MergeDecision.ESCALATE_HUMAN
        assert rec.decision_source == DecisionSource.AUTO_EXECUTOR

    @pytest.mark.asyncio
    async def test_approve_human_choice_noop(self, tmp_path):
        repo = _make_repo(tmp_path)
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "approve_human")

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == set()
        rec = state.file_decision_records["src.ts"]
        # approve_human is NOT in the actionable set — left for manual.
        assert rec.decision == MergeDecision.ESCALATE_HUMAN

    @pytest.mark.asyncio
    async def test_duplicate_paths_only_dispatched_once(self, tmp_path):
        repo = _make_repo(tmp_path)
        gt = GitTool(str(repo))
        state = _state_with_surfaced("src.ts", "take_target")
        # Duplicate row for the same file_path with a different choice.
        state.pending_user_decisions.append(
            UserDecisionItem(
                item_id="dup",
                file_path="src.ts",
                description="dup",
                current_classification="human_required",
                user_choice="take_current",
            )
        )

        applied = await dispatch_user_choice(
            state, gt, state.pending_user_decisions, phase="human_review"
        )

        assert applied == {"src.ts"}
        # First item wins; file holds upstream content, not fork.
        assert (repo / "src.ts").read_text() == "UPSTREAM CONTENT\n"
