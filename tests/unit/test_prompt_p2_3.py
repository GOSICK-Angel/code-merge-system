"""P2-3 find/filter separation (high-recall review) — opt-in, Judge only.

The change is version-specific (Opus 4.8 follows "be conservative" literally,
lowering defect recall) and lives behind ``AgentLLMConfig.high_recall_review``
(default False). These tests pin the two invariants the §五 C-class guardrail
demands:

- **Default off → byte-for-byte unchanged.** Both judge prompts must be
  identical to their pre-P2-3 form unless ``high_recall=True``.
- **On → the find/filter block is injected** and is orthogonal to ``lang``.

PlannerJudge is intentionally untouched — its conservatism is a deliberate
calibration, not a recall bug.
"""

from __future__ import annotations

from src.llm.prompts.judge_prompts import (
    _FIND_FILTER_NOTE,
    build_batch_file_review_prompt,
    build_file_review_prompt,
)
from src.models.config import AgentLLMConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileDiff, FileStatus, RiskLevel

_RECALL_MARKER = "RECALL MODE (P2-3)"


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


def _review(**kw: object) -> str:
    return build_file_review_prompt(
        "pkg/iso.ts",
        "merged",
        _decision(),
        _fd(),
        "ctx",
        **kw,  # type: ignore[arg-type]
    )


def _batch(**kw: object) -> str:
    reviews = [
        {
            "file_path": "pkg/iso.ts",
            "merged_content": "merged",
            "decision_record": _decision(),
            "original_diff": _fd(),
        }
    ]
    return build_batch_file_review_prompt(reviews, "ctx", **kw)  # type: ignore[arg-type]


class TestConfigDefault:
    def test_high_recall_review_defaults_off(self) -> None:
        assert AgentLLMConfig().high_recall_review is False


class TestFileReviewRecallMode:
    def test_off_is_byte_identical_to_default(self) -> None:
        assert _review(high_recall=False) == _review()

    def test_on_injects_find_filter_block(self) -> None:
        on = _review(high_recall=True)
        assert _RECALL_MARKER in on
        assert _FIND_FILTER_NOTE in on
        assert _RECALL_MARKER not in _review()

    def test_orthogonal_to_lang(self) -> None:
        zh_on = _review(high_recall=True, lang="zh")
        assert _RECALL_MARKER in zh_on
        assert "语言要求" in zh_on
        # zh + off stays byte-identical to plain zh
        assert _review(high_recall=False, lang="zh") == _review(lang="zh")

    def test_grounding_precedes_recall_note(self) -> None:
        on = _review(high_recall=True)
        assert on.index("GROUNDING RULE") < on.index(_RECALL_MARKER)


class TestBatchReviewRecallMode:
    def test_off_is_byte_identical_to_default(self) -> None:
        assert _batch(high_recall=False) == _batch()

    def test_on_injects_find_filter_block(self) -> None:
        on = _batch(high_recall=True)
        assert _RECALL_MARKER in on
        assert _RECALL_MARKER not in _batch()
