"""Tests for build_file_review_prompt's fork_content section.

Regression: Judge prompt previously contained only merged_content; the LLM
could not distinguish "merged file diverges from upstream because the fork
field was preserved" from "merged file is wrong". When the
ConflictAnalyst's rationale itself said "without seeing actual content",
the Judge would echo that as a defect and refuse to converge, producing
false-positive FAIL verdicts on HUMAN-decided semantic merges.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.judge_agent import JudgeAgent
from src.llm.prompts.judge_prompts import build_file_review_prompt
from src.models.config import AgentLLMConfig
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _make_diff(file_path: str = "packages/zod/src/v4/core/versions.ts") -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        lines_added=2,
        lines_deleted=1,
        language="typescript",
    )


def _make_record(
    file_path: str = "packages/zod/src/v4/core/versions.ts",
) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.HUMAN,
        rationale="Both sides added version entries; semantic merge.",
    )


class TestForkContentInPrompt:
    def test_includes_fork_section_when_fork_differs_from_merged(self):
        merged = 'export const version = { patch: 3, fork: "cvte-4.4.3" } as const;'
        fork = 'export const version = { patch: 2, fork: "cvte-4.4.2" } as const;'

        prompt = build_file_review_prompt(
            file_path="versions.ts",
            merged_content=merged,
            decision_record=_make_record("versions.ts"),
            original_diff=_make_diff("versions.ts"),
            fork_content=fork,
        )

        assert "Fork Original" in prompt
        assert 'fork: "cvte-4.4.2"' in prompt
        assert 'fork: "cvte-4.4.3"' in prompt  # merged still present

    def test_no_fork_section_when_fork_content_none(self):
        prompt = build_file_review_prompt(
            file_path="any.ts",
            merged_content="merged body",
            decision_record=_make_record("any.ts"),
            original_diff=_make_diff("any.ts"),
            fork_content=None,
        )

        assert "Fork Original" not in prompt

    def test_no_fork_section_when_fork_equals_merged(self):
        body = "same body on both sides"
        prompt = build_file_review_prompt(
            file_path="any.ts",
            merged_content=body,
            decision_record=_make_record("any.ts"),
            original_diff=_make_diff("any.ts"),
            fork_content=body,
        )

        assert "Fork Original" not in prompt


class TestReviewFilePassesForkContent:
    @pytest.mark.asyncio
    async def test_review_file_forwards_fork_content_to_prompt(self):
        with patch("src.llm.client.LLMClientFactory.create"):
            agent = JudgeAgent(AgentLLMConfig(), git_tool=None)

        captured_messages: dict[str, object] = {}

        async def _capture(messages, system=None):
            captured_messages["messages"] = messages
            return '{"issues": []}'

        with patch.object(
            agent, "_call_llm_with_retry", new=AsyncMock(side_effect=_capture)
        ):
            await agent.review_file(
                file_path="versions.ts",
                merged_content='patch: 3, fork: "cvte-4.4.3"',
                decision_record=_make_record("versions.ts"),
                original_diff=_make_diff("versions.ts"),
                fork_content='patch: 2, fork: "cvte-4.4.2"',
            )

        msgs = captured_messages["messages"]
        assert isinstance(msgs, list) and msgs
        user_content = msgs[-1]["content"]
        assert "Fork Original" in user_content
        assert 'fork: "cvte-4.4.2"' in user_content
