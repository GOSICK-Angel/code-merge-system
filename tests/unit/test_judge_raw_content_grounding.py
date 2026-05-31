"""#12: Judge grounds evidence + scans for conflict markers against the RAW
on-disk merged blob, not the budget-trimmed staged view.

Regression: ``review_file`` reassigns ``merged_content`` to the staged
(budget-trimmed) view before the LLM call. Evidence grounding and the
conflict-marker scan once ran against that trimmed view, so:
  - a real CRITICAL whose ``evidence_excerpt`` was elided out of the staged
    window was wrongly downgraded to MEDIUM as "hallucinated evidence", and
  - a conflict marker outside the staged window was silently missed.
Both are fail-OPEN. The fix preserves the full blob (``raw_merged_content``)
for those two checks; the LLM prompt still sees the staged view.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.judge_agent import JudgeAgent
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.judge import IssueSeverity


def _make_judge() -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        agent = JudgeAgent(AgentLLMConfig(), git_tool=None)
    return agent


def _record() -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path="demo.txt",
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        rationale="merged",
    )


def _diff() -> FileDiff:
    return FileDiff(
        file_path="demo.txt",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.0,
    )


# The evidence line lives DEEP in the file; the staged view trims it away.
_EVIDENCE = "REAL_DEFECT_TOKEN_xyz"
_FULL = "filler\n" * 200 + _EVIDENCE + "\n" + "filler\n" * 200
# build_staged_content is patched to return only this — the evidence is gone.
_STAGED = "filler\nfiller\n"


async def test_evidence_grounded_against_full_blob_not_staged() -> None:
    agent = _make_judge()
    from src.llm import prompt_builders as pb

    llm_payload = json.dumps(
        {
            "issues": [
                {
                    "file_path": "demo.txt",
                    "issue_level": "critical",
                    "issue_type": "logic_error",
                    "description": "real defect",
                    "evidence_excerpt": _EVIDENCE,
                    "affected_lines": [201],
                    "must_fix_before_merge": True,
                }
            ]
        }
    )

    with (
        patch.object(
            pb.AgentPromptBuilder,
            "build_staged_content",
            MagicMock(return_value=_STAGED),
        ),
        patch.object(
            agent, "_call_llm_with_retry", new=AsyncMock(return_value=llm_payload)
        ),
    ):
        issues = await agent.review_file(
            file_path="demo.txt",
            merged_content=_FULL,
            decision_record=_record(),
            original_diff=_diff(),
        )

    defect = [i for i in issues if i.issue_type == "logic_error"]
    assert defect, "the LLM-reported defect must survive"
    # Grounded against the FULL blob → stays CRITICAL (would be MEDIUM if grounded
    # against the trimmed staged view, which lacks the evidence line).
    assert defect[0].issue_level == IssueSeverity.CRITICAL
    assert "hallucinated evidence" not in defect[0].description


async def test_conflict_marker_outside_staged_window_is_detected() -> None:
    agent = _make_judge()
    from src.llm import prompt_builders as pb

    full_with_marker = "ok\n" * 200 + "<<<<<<< HEAD\n" + "ok\n" * 200

    with (
        patch.object(
            pb.AgentPromptBuilder,
            "build_staged_content",
            MagicMock(return_value=_STAGED),  # marker trimmed away
        ),
        patch.object(agent, "_call_llm_with_retry", new=AsyncMock(return_value="[]")),
    ):
        issues = await agent.review_file(
            file_path="demo.txt",
            merged_content=full_with_marker,
            decision_record=_record(),
            original_diff=_diff(),
        )

    markers = [i for i in issues if i.issue_type == "unresolved_conflict"]
    assert markers, "conflict marker outside the staged window must be detected"
    assert markers[0].issue_level == IssueSeverity.CRITICAL
