"""Batch C (output quality) prompt-engineering hardening.

Covers three A-class (model-agnostic) changes:

- **P2-2** every analyst / judge JSON prompt uses one strong "respond with
  ONLY a single JSON object" instruction instead of the weak ``Return JSON:``
  header. The bare header let some models emit a markdown preamble.
- **P2-4** analyst / judge inject a Chinese language note for human-facing
  fields when ``lang == "zh"``; English runs are byte-for-byte unchanged.
- **P1-4** the planner revision prompt no longer instructs the model to
  extrapolate to "similar files" when the issue list is truncated — it scopes
  the reclassification to exactly the listed files.
"""

from __future__ import annotations

from datetime import datetime

from src.llm.prompts.analyst_prompts import (
    _JSON_ONLY_INSTRUCTION as ANALYST_JSON_ONLY,
    build_conflict_analysis_prompt,
)
from src.llm.prompts.judge_prompts import (
    _JSON_ONLY_INSTRUCTION as JUDGE_JSON_ONLY,
    build_file_review_prompt,
    build_re_evaluate_prompt,
    build_verdict_prompt,
)
from src.llm.prompts.planner_prompts import (
    MAX_REVISION_ISSUES,
    build_revision_prompt,
)
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePlan, RiskSummary
from src.models.plan_judge import PlanIssue


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


class TestStrongJsonWording:
    """P2-2: weak ``Return JSON:`` is replaced by the strong instruction."""

    def test_analyst_conflict_prompt_uses_strong_wording(self) -> None:
        prompt = build_conflict_analysis_prompt(_fd(), "base", "cur", "tgt", "ctx")
        assert ANALYST_JSON_ONLY in prompt
        assert "Return JSON:" not in prompt
        assert "json.loads()" in ANALYST_JSON_ONLY

    def test_judge_file_review_prompt_uses_strong_wording(self) -> None:
        prompt = build_file_review_prompt(
            "pkg/iso.ts", "merged", _decision(), _fd(), "ctx"
        )
        assert JUDGE_JSON_ONLY in prompt
        assert "Return JSON:" not in prompt

    def test_judge_verdict_and_reeval_use_strong_wording(self) -> None:
        verdict = build_verdict_prompt(["a", "b"], "summary", 1, 0)
        reeval = build_re_evaluate_prompt("rebuttal", "orig issues")
        for prompt in (verdict, reeval):
            assert JUDGE_JSON_ONLY in prompt
            assert "Return JSON:" not in prompt


class TestLanguageInjection:
    """P2-4: zh runs add a Chinese note; en runs are unchanged."""

    def test_analyst_zh_note_only_when_zh(self) -> None:
        en = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx", lang="en")
        zh = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx", lang="zh")
        assert "语言要求" not in en
        assert "语言要求" in zh
        # default is English-equivalent
        default = build_conflict_analysis_prompt(_fd(), "b", "c", "t", "ctx")
        assert default == en

    def test_judge_zh_note_only_when_zh(self) -> None:
        en = build_file_review_prompt(
            "pkg/iso.ts", "merged", _decision(), _fd(), "ctx", lang="en"
        )
        zh = build_file_review_prompt(
            "pkg/iso.ts", "merged", _decision(), _fd(), "ctx", lang="zh"
        )
        assert "语言要求" not in en
        assert "语言要求" in zh
        default = build_file_review_prompt(
            "pkg/iso.ts", "merged", _decision(), _fd(), "ctx"
        )
        assert default == en


class TestRevisionExplicitScope:
    """P1-4: truncated revision list scopes to listed files, no extrapolation."""

    def _plan(self) -> MergePlan:
        return MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="origin/main",
            merge_base_commit="abc123",
            phases=[],
            risk_summary=RiskSummary(
                total_files=0,
                auto_safe_count=0,
                auto_risky_count=0,
                human_required_count=0,
                deleted_only_count=0,
                binary_count=0,
                excluded_count=0,
                estimated_auto_merge_rate=0.0,
                top_risk_files=[],
            ),
            project_context_summary="test project",
        )

    def _issues(self, n: int) -> list[PlanIssue]:
        return [
            PlanIssue(
                file_path=f"f{i}.ts",
                current_classification=RiskLevel.AUTO_SAFE,
                suggested_classification=RiskLevel.AUTO_RISKY,
                reason="r",
                issue_type="risk_underestimation",
            )
            for i in range(n)
        ]

    def test_no_extrapolation_instruction(self) -> None:
        prompt = build_revision_prompt(
            self._plan(), self._issues(MAX_REVISION_ISSUES + 5)
        )
        assert "Apply the same reclassification pattern to similar files" not in prompt
        assert "Reclassify ONLY the files listed" in prompt
        assert "Do not infer or extrapolate" in prompt

    def test_no_truncation_note_when_within_cap(self) -> None:
        prompt = build_revision_prompt(self._plan(), self._issues(3))
        assert "Reclassify ONLY the files listed" not in prompt
