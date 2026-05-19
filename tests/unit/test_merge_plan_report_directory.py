"""Unit tests for the directory-classification matrix in `merge_plan_report`.

Regression coverage for the bug where root-level files (e.g. ``go.mod``)
and one-level files (e.g. ``cmd/foo.go``) were rendered as their own
"directory" rows, polluting the matrix.
"""

from __future__ import annotations

from pathlib import Path

from src.models.config import FileClassifierConfig, MergeConfig, OutputConfig
from src.models.diff import FileChangeCategory
from src.models.state import MergeState
from src.tools.merge_plan_report import write_merge_plan_report


def _make_state(tmp_path: Path) -> MergeState:
    config = MergeConfig(
        upstream_ref="test/upstream",
        fork_ref="test/fork",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        file_classifier=FileClassifierConfig(),
    )
    state = MergeState(config=config)
    state.run_id = "dir-matrix-test"
    state.merge_base_commit = "deadbeef"
    return state


class TestDirectoryMatrix:
    def test_root_level_file_groups_under_root(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.file_categories = {
            "go.mod": FileChangeCategory.B,
            "package.json": FileChangeCategory.B,
        }

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "| (root) |" in text
        assert "| go.mod |" not in text
        assert "| package.json |" not in text

    def test_one_level_file_groups_under_top_dir(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.file_categories = {
            "cmd/admin_user.go": FileChangeCategory.B,
            "cmd/doctor.go": FileChangeCategory.B,
        }

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "| cmd |" in text
        assert "| cmd/admin_user.go |" not in text

    def test_two_level_file_groups_under_two_segments(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.file_categories = {
            "models/auth/auth_token.go": FileChangeCategory.C,
        }

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "| models/auth |" in text

    def test_deeply_nested_file_capped_at_two_levels(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.file_categories = {
            "templates/user/settings/keys_ssh.tmpl": FileChangeCategory.B,
            "templates/user/dashboard/issues.tmpl": FileChangeCategory.B,
        }

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "| templates/user |" in text
        assert "| templates/user/settings |" not in text
        assert "| templates/user/dashboard |" not in text


class TestReportOverwrite:
    """`write_merge_plan_report` must overwrite the file on every call
    within a single run — earlier behaviour added a timestamp suffix on
    re-write, leaving a stale planning-phase version next to the final
    post-review version and forcing auditors to diff two files."""

    def test_repeated_writes_target_same_file(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.file_categories = {"src/foo.py": FileChangeCategory.B}

        first = write_merge_plan_report(state)
        second = write_merge_plan_report(state)

        assert first == second
        plans_dir = first.parent
        merge_plan_files = list(plans_dir.glob("MERGE_PLAN_*.md"))
        assert len(merge_plan_files) == 1, (
            f"expected exactly one report, found {[p.name for p in merge_plan_files]}"
        )

    def test_second_write_reflects_updated_state(self, tmp_path: Path):
        from src.models.plan_judge import PlanJudgeResult
        from src.models.plan_review import PlanReviewRound

        state = _make_state(tmp_path)
        state.file_categories = {"src/foo.py": FileChangeCategory.B}

        first_path = write_merge_plan_report(state)
        first_text = first_path.read_text(encoding="utf-8")
        assert "No review rounds recorded" in first_text

        state.plan_review_log = [
            PlanReviewRound(
                round_number=0,
                verdict_result=PlanJudgeResult.APPROVED,
                verdict_summary="ok",
                issues_count=0,
            )
        ]
        second_path = write_merge_plan_report(state)
        second_text = second_path.read_text(encoding="utf-8")
        assert "No review rounds recorded" not in second_text
        assert "Round 0" in second_text


class TestPlanReviewLogInReport:
    """Regression for the bug where MERGE_PLAN_*.md was written by
    PlanningPhase only (before plan_review ran), so the Planner-Judge
    Review Log section was always "_No review rounds recorded._".

    PlanReviewPhase._complete_phase now re-writes the report at the end
    of every terminal path; verify the log section gets populated when
    state.plan_review_log is non-empty.
    """

    def test_planner_judge_log_renders_when_rounds_recorded(self, tmp_path: Path):
        from datetime import datetime as _dt

        from src.models.plan_judge import PlanJudgeResult
        from src.models.plan_review import PlanReviewRound

        state = _make_state(tmp_path)
        state.file_categories = {"models/auth/auth_token.go": FileChangeCategory.C}
        state.plan_review_log = [
            PlanReviewRound(
                round_number=0,
                verdict_result=PlanJudgeResult.APPROVED,
                verdict_summary="All segments matched safelist",
                issues_count=0,
                issues_detail=[],
            ),
        ]

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "Planner-Judge Review Log" in text or "Planner-Judge 审查记录" in text
        assert "No review rounds recorded" not in text
        assert "暂无审查记录" not in text
        assert "Round 0" in text or "轮次 0" in text
        assert "All segments matched safelist" in text


class TestPlanReviewLogSegmentTelemetry:
    """The Review Log section in MERGE_PLAN.md must render
    segment_telemetry so audit fields agree with plan_review.md
    (rendered separately by report_writer.py). Previously this section
    showed only verdict/summary/issues_count, hiding the "M LLM, N
    safelist, K cache" provenance.
    """

    def test_renders_llm_segment_cost(self, tmp_path: Path):
        from src.models.plan_judge import PlanJudgeResult
        from src.models.plan_review import PlanReviewRound, SegmentTelemetrySummary

        state = _make_state(tmp_path)
        state.file_categories = {"src/foo.py": FileChangeCategory.B}
        state.plan_review_log = [
            PlanReviewRound(
                round_number=0,
                verdict_result=PlanJudgeResult.APPROVED,
                verdict_summary="ok",
                issues_count=0,
                segment_telemetry=SegmentTelemetrySummary(
                    llm_segments=2,
                    cache_hit_segments=1,
                    safelist_segments=0,
                    total_latency_s=3.5,
                    total_tokens_in=4000,
                    total_tokens_out=400,
                ),
            )
        ]

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "Segment cost (this round)" in text
        assert "2 LLM segment(s)" in text
        assert "1 cache" in text
        assert "~4000 tokens-in" in text
        assert "3.5s total" in text

    def test_renders_zero_llm_round_provenance(self, tmp_path: Path):
        from src.models.plan_judge import PlanJudgeResult
        from src.models.plan_review import PlanReviewRound, SegmentTelemetrySummary

        state = _make_state(tmp_path)
        state.file_categories = {"src/foo.py": FileChangeCategory.B}
        state.plan_review_log = [
            PlanReviewRound(
                round_number=0,
                verdict_result=PlanJudgeResult.APPROVED,
                verdict_summary="all safelist",
                issues_count=0,
                segment_telemetry=SegmentTelemetrySummary(
                    llm_segments=0,
                    cache_hit_segments=1,
                    safelist_segments=3,
                ),
            )
        ]

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "0 LLM segment(s)" in text
        assert "3 safelist" in text
        assert "skipped LLM entirely" in text

    def test_no_telemetry_omits_cost_line(self, tmp_path: Path):
        from src.models.plan_judge import PlanJudgeResult
        from src.models.plan_review import PlanReviewRound

        state = _make_state(tmp_path)
        state.file_categories = {"src/foo.py": FileChangeCategory.B}
        state.plan_review_log = [
            PlanReviewRound(
                round_number=0,
                verdict_result=PlanJudgeResult.APPROVED,
                verdict_summary="ok",
                issues_count=0,
                segment_telemetry=None,
            )
        ]

        text = write_merge_plan_report(state).read_text(encoding="utf-8")

        assert "Segment cost" not in text
