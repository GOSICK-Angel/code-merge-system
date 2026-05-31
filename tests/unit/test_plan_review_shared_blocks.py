"""Batch B / P1-2: the whole-plan and per-segment plan-review prompts must
render the SAME review-task rules (incl. rule 6's auth-keyword list) and the
SAME return-JSON schema, sourced from one place so they cannot drift.

Pins the single-source invariant structurally — both builders interpolate the
shared `_REVIEW_TASKS_RULES` constant and `_return_schema_block` helper.
"""

from __future__ import annotations

from datetime import datetime

from src.llm.prompts.planner_judge_prompts import (
    _REVIEW_TASKS_RULES,
    _return_schema_block,
    build_plan_review_prompt,
    build_segment_plan_review_prompt,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary


def _plan() -> MergePlan:
    return MergePlan(
        created_at=datetime(2026, 1, 1),
        upstream_ref="test/upstream",
        fork_ref="test/fork",
        merge_base_commit="abc123",
        phases=[
            PhaseFileBatch(
                batch_id="b1",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["src/auth/login.ts"],
                risk_level=RiskLevel.AUTO_RISKY,
                can_parallelize=True,
            )
        ],
        risk_summary=RiskSummary(
            total_files=1,
            auto_safe_count=0,
            auto_risky_count=1,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.0,
        ),
        project_context_summary="ctx",
    )


def _files() -> list[FileDiff]:
    return [
        FileDiff(
            file_path="src/auth/login.ts",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
        )
    ]


def test_both_prompts_embed_the_shared_review_rules() -> None:
    full = build_plan_review_prompt(_plan(), _files())
    seg = build_segment_plan_review_prompt(_plan(), _files(), 0, 2, 2)
    assert _REVIEW_TASKS_RULES in full
    assert _REVIEW_TASKS_RULES in seg


def test_rule6_keyword_list_is_single_source() -> None:
    # The exact auth-keyword list lives once, in _REVIEW_TASKS_RULES.
    assert "{auth, token, user, permission, session" in _REVIEW_TASKS_RULES


def test_both_prompts_embed_the_shared_schema_body() -> None:
    full = build_plan_review_prompt(_plan(), _files())
    seg = build_segment_plan_review_prompt(_plan(), _files(), 0, 2, 2)
    assert _return_schema_block("Overall assessment") in full
    assert _return_schema_block("Assessment of this segment") in seg
