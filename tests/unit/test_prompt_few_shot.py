"""P1-1 few-shot examples for the Claude-system prompts.

Adds worked `<example>` pairs to the planner classification, conflict-analyst,
and judge file-review prompts (Claude-only — executor / planner_judge stay
zero-shot per the §五 B-class guardrail in
doc/bugfix/0528-agent-prompt-engineering-review.md).

Each test verifies the example block is present, its embedded JSON parses, and
the examples cover the intended boundaries (risk levels / strategies / a clean
pass vs a grounded defect). The English-equivalence invariant from
test_prompt_batch_c is re-checked so few-shot does not re-introduce a lang gate.
"""

from __future__ import annotations

import json
import re

from src.llm.prompts.analyst_prompts import build_conflict_analysis_prompt
from src.llm.prompts.judge_prompts import build_file_review_prompt
from src.llm.prompts.planner_prompts import build_classification_prompt
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _fd(path: str = "pkg/iso.ts") -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        language="typescript",
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=2,
        lines_deleted=1,
    )


def _decision(path: str = "pkg/iso.ts") -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.TAKE_CURRENT,
        decision_source=DecisionSource.AUTO_PLANNER,
        rationale="r",
    )


def _embedded_json_objects(prompt: str) -> list[dict]:
    """Extract and json.loads every {...} block nested inside <example> tags."""
    examples = re.findall(r"<example>(.*?)</example>", prompt, flags=re.DOTALL)
    assert examples, "prompt has no <example> blocks"
    objs: list[dict] = []
    for ex in examples:
        for match in re.findall(r"\{.*\}", ex, flags=re.DOTALL):
            objs.append(json.loads(match))
    return objs


class TestPlannerClassificationExamples:
    def test_examples_present_and_cover_risk_levels(self) -> None:
        prompt = build_classification_prompt([_fd()], "ctx")
        assert "<examples>" in prompt
        assert prompt.count("<example>") == 3
        for level in ("auto_safe", "auto_risky", "human_required"):
            assert f"Expected risk_level: {level}" in prompt

    def test_examples_precede_the_plan_instruction(self) -> None:
        prompt = build_classification_prompt([_fd()], "ctx")
        assert prompt.index("<examples>") < prompt.index("Create a phased merge plan")


class TestAnalystAnalysisExamples:
    def test_examples_present_and_parse(self) -> None:
        prompt = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx")
        assert prompt.count("<example>") == 2
        objs = _embedded_json_objects(prompt)
        for obj in objs:
            assert "recommended_strategy" in obj
            assert "semantic_compatibility" in obj
            assert "rationale" in obj

    def test_examples_cover_merge_and_escalate(self) -> None:
        prompt = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx")
        strategies = {o["recommended_strategy"] for o in _embedded_json_objects(prompt)}
        assert {"semantic_merge", "escalate_human"} <= strategies
        compat = {o["semantic_compatibility"] for o in _embedded_json_objects(prompt)}
        assert {"compatible", "incompatible"} <= compat

    def test_examples_appear_before_output_format(self) -> None:
        prompt = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx")
        assert prompt.index("<examples>") < prompt.index("<output_format>")

    def test_english_default_unchanged_by_examples(self) -> None:
        # few-shot is not lang-gated: default == en invariant still holds.
        en = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx", lang="en")
        default = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx")
        assert default == en
        assert "<examples>" in en


class TestJudgeReviewExamples:
    def test_examples_present_and_parse(self) -> None:
        prompt = build_file_review_prompt("pkg/iso.ts", "merged", _decision(), _fd())
        assert prompt.count("<example>") == 2
        objs = _embedded_json_objects(prompt)
        for obj in objs:
            assert "issues" in obj
            assert "overall_assessment" in obj

    def test_clean_and_defect_examples(self) -> None:
        prompt = build_file_review_prompt("pkg/iso.ts", "merged", _decision(), _fd())
        objs = _embedded_json_objects(prompt)
        issue_counts = [len(o["issues"]) for o in objs]
        assert 0 in issue_counts, "needs a clean (empty-issues) example"
        assert any(c > 0 for c in issue_counts), "needs a defect example"
        # the defect example must carry grounding (evidence_excerpt) per P1-3.
        defect = next(o for o in objs if o["issues"])
        assert all(i.get("evidence_excerpt") for i in defect["issues"])

    def test_examples_appear_before_output_format(self) -> None:
        prompt = build_file_review_prompt("pkg/iso.ts", "merged", _decision(), _fd())
        assert prompt.index("<examples>") < prompt.index("<output_format>")

    def test_english_default_unchanged_by_examples(self) -> None:
        en = build_file_review_prompt(
            "pkg/iso.ts", "merged", _decision(), _fd(), lang="en"
        )
        default = build_file_review_prompt("pkg/iso.ts", "merged", _decision(), _fd())
        assert default == en
        assert "<examples>" in en
