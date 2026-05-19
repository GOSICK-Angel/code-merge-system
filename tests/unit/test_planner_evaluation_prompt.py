"""Regression for the softened Planner REJECT criteria.

Pre-softening: any time conflict_count=0 and is_security_sensitive=false,
the Planner was instructed to MUST-reject path-based suggestions —
which also auto-rejected evidence-backed C-class concerns and made
the Planner ↔ Judge negotiation degenerate into single-shot.

The relaxed contract:
- Path-only reasons → MAY reject (not MUST).
- Evidence-backed reasons (line numbers, function names, fork/upstream
  deltas, [C] flag with non-trivial deltas) → MUST NOT auto-reject.
"""

from __future__ import annotations

from datetime import datetime

from src.llm.prompts.planner_prompts import build_evaluation_prompt
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.plan_judge import PlanIssue


def _plan_with_issue(file_path: str = "models/user/user.go") -> MergePlan:
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="up",
        fork_ref="fork",
        merge_base_commit="abc",
        phases=[
            PhaseFileBatch(
                batch_id="b0",
                phase=MergePhase.AUTO_MERGE,
                file_paths=[file_path],
                risk_level=RiskLevel.AUTO_RISKY,
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
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )


def _issue(file_path: str = "models/user/user.go") -> PlanIssue:
    return PlanIssue(
        file_path=file_path,
        current_classification=RiskLevel.AUTO_RISKY,
        suggested_classification=RiskLevel.HUMAN_REQUIRED,
        reason="path contains 'user' — security-adjacent",
        issue_type="risk_underestimated",
    )


def _fd(file_path: str = "models/user/user.go") -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=12,
        lines_deleted=3,
        upstream_lines_added=8,
        upstream_lines_deleted=2,
        change_category=FileChangeCategory.C,
    )


class TestSoftenedRejectCriteria:
    def test_reject_criteria_phrased_as_may_not_must(self):
        prompt = build_evaluation_prompt(
            _plan_with_issue(), [_issue()], file_diffs=[_fd()]
        )
        assert "MAY reject" in prompt
        assert "MUST reject" not in prompt

    def test_evidence_backed_carve_out_present(self):
        prompt = build_evaluation_prompt(
            _plan_with_issue(), [_issue()], file_diffs=[_fd()]
        )
        assert "EVIDENCE-BACKED REASONS must NOT be auto-rejected" in prompt

    def test_carve_out_lists_concrete_evidence_kinds(self):
        prompt = build_evaluation_prompt(
            _plan_with_issue(), [_issue()], file_diffs=[_fd()]
        )
        for token in (
            "line numbers",
            "function names",
            "regions=",
            "category=both_changed",
        ):
            assert token in prompt, f"evidence carve-out must mention {token!r}"

    def test_prefer_discuss_when_uncertain(self):
        prompt = build_evaluation_prompt(
            _plan_with_issue(), [_issue()], file_diffs=[_fd()]
        )
        assert "prefer DISCUSS" in prompt
