"""Unit tests for the forks-profile drift appendix in `merge_plan_report`.

The appendix is a thin renderer: when ``state.forks_profile_drift`` is
populated by initialize phase, ``write_merge_plan_report`` surfaces it
as a fenced block beneath the planner-judge log so reviewers see
profile staleness alongside the plan they're approving.
"""

from __future__ import annotations

from pathlib import Path

from src.models.config import (
    FileClassifierConfig,
    MergeConfig,
    OutputConfig,
)
from src.models.state import MergeState
from src.tools.merge_plan_report import write_merge_plan_report


def _make_state(tmp_path: Path) -> MergeState:
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        file_classifier=FileClassifierConfig(),
    )
    state = MergeState(config=config)
    state.run_id = "abc12345-test"
    state.merge_base_commit = "deadbeef0001"
    return state


class TestDriftAppendix:
    def test_drift_renders_when_state_populated(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.forks_profile_drift = (
            "📋 已声明但启发式不再检出 (可能可删):\n"
            "  - removed_domain[smtp]\n"
            "    rationale: no FORK_DELETED files match path_globs anymore\n"
        )

        report_path = write_merge_plan_report(state)
        text = report_path.read_text(encoding="utf-8")

        assert "Forks-profile" in text
        assert "removed_domain[smtp]" in text
        # Surrounded by a fenced block so the diff text passes through verbatim.
        assert "```" in text

    def test_no_drift_means_no_appendix(self, tmp_path: Path):
        state = _make_state(tmp_path)
        state.forks_profile_drift = None

        report_path = write_merge_plan_report(state)
        text = report_path.read_text(encoding="utf-8")

        assert "Forks-profile" not in text

    def test_empty_string_drift_treated_as_absent(self, tmp_path: Path):
        # An empty string (rather than None) must not produce an empty
        # fenced block — that would imply drift exists when none does.
        state = _make_state(tmp_path)
        state.forks_profile_drift = ""

        report_path = write_merge_plan_report(state)
        text = report_path.read_text(encoding="utf-8")

        assert "Forks-profile" not in text
