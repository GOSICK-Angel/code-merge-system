"""Judge deterministic-pipeline forks-profile downgrade.

Verifies that paths declared in ``state.forks_profile.removed_domains`` /
``rewritten_modules`` cause the deterministic pipeline to emit INFO
issues (with a ``_profile_pinned`` suffix) instead of CRITICAL/HIGH.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agents.judge_agent import JudgeAgent
from src.core.read_only_state_view import ReadOnlyStateView
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.diff import FileChangeCategory
from src.models.forks_profile import ForksProfile
from src.models.judge import IssueSeverity
from src.models.state import MergeState


def _make_judge(git_tool=None) -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return JudgeAgent(AgentLLMConfig(), git_tool=git_tool)


def _make_state(
    file_categories: dict[str, FileChangeCategory],
    profile: ForksProfile | None,
) -> MergeState:
    config = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        repo_path=".",
    )
    state = MergeState(config=config)
    state.merge_base_commit = "base-sha"
    state.file_categories = file_categories
    state.forks_profile = profile
    return state


def _stub_three_way(b_applied: bool, d_present: bool, additions: list[str]):
    """Build a ThreeWayDiff stub that lets us drive the verification
    branches in _run_deterministic_pipeline.
    """
    twd = MagicMock()
    twd.verify_b_class_diff_applied.return_value = b_applied
    twd.verify_d_missing_present.return_value = d_present
    twd.extract_upstream_additions.return_value = additions
    twd.verify_additions_present.return_value = additions  # all missing
    twd.find_todo_check.return_value = []
    twd.count_todo_merge.return_value = 0
    return twd


class TestJudgeForksProfileDowngrade:
    def test_b_class_downgraded_when_path_in_removed_domain(self):
        profile = ForksProfile.model_validate(
            {
                "removed_domains": [
                    {
                        "name": "alpha",
                        "paths": ["svc/alpha/**"],
                        "reason": "out of scope",
                    }
                ]
            }
        )
        state = _make_state({"svc/alpha/handler.py": FileChangeCategory.B}, profile)
        view = ReadOnlyStateView(state)
        judge = _make_judge(git_tool=MagicMock())

        with patch(
            "src.agents.judge_agent.ThreeWayDiff",
            return_value=_stub_three_way(False, True, []),
        ):
            issues = judge._run_deterministic_pipeline(view, {})

        b_issues = [i for i in issues if "b_class_mismatch" in i.issue_type]
        assert len(b_issues) == 1
        assert b_issues[0].issue_level == IssueSeverity.INFO
        assert b_issues[0].issue_type == "b_class_mismatch_profile_pinned"
        assert "alpha" in b_issues[0].description
        assert "out of scope" in b_issues[0].description

    def test_d_missing_downgraded_for_removed_domain(self):
        profile = ForksProfile.model_validate(
            {
                "removed_domains": [
                    {"name": "beta", "paths": ["svc/beta/**"]},
                ]
            }
        )
        state = _make_state(
            {"svc/beta/login.py": FileChangeCategory.D_MISSING}, profile
        )
        view = ReadOnlyStateView(state)
        judge = _make_judge(git_tool=MagicMock())

        with patch(
            "src.agents.judge_agent.ThreeWayDiff",
            return_value=_stub_three_way(True, False, []),
        ):
            issues = judge._run_deterministic_pipeline(view, {})

        d_issues = [i for i in issues if "d_missing_absent" in i.issue_type]
        assert len(d_issues) == 1
        assert d_issues[0].issue_level == IssueSeverity.INFO
        assert d_issues[0].issue_type == "d_missing_absent_profile_pinned"

    def test_c_class_addition_downgraded_for_rewritten_module(self):
        profile = ForksProfile.model_validate(
            {
                "rewritten_modules": [
                    {
                        "path": "svc/auth/**",
                        "policy": "escalate_human",
                        "note": "custom SSO",
                    },
                ]
            }
        )
        state = _make_state({"svc/auth/login.py": FileChangeCategory.C}, profile)
        view = ReadOnlyStateView(state)
        judge = _make_judge(git_tool=MagicMock())

        with patch(
            "src.agents.judge_agent.ThreeWayDiff",
            return_value=_stub_three_way(True, True, ["new_function_x"]),
        ):
            issues = judge._run_deterministic_pipeline(view, {})

        c_issues = [i for i in issues if "missing_upstream_addition" in i.issue_type]
        assert len(c_issues) == 1
        assert c_issues[0].issue_level == IssueSeverity.INFO
        assert c_issues[0].issue_type == "missing_upstream_addition_profile_pinned"
        assert "escalate_human" in c_issues[0].description
        assert "svc/auth/**" in c_issues[0].description

    def test_no_profile_keeps_critical_severity(self):
        state = _make_state({"svc/whatever/x.py": FileChangeCategory.B}, profile=None)
        view = ReadOnlyStateView(state)
        judge = _make_judge(git_tool=MagicMock())

        with patch(
            "src.agents.judge_agent.ThreeWayDiff",
            return_value=_stub_three_way(False, True, []),
        ):
            issues = judge._run_deterministic_pipeline(view, {})

        b_issues = [i for i in issues if "b_class_mismatch" in i.issue_type]
        assert len(b_issues) == 1
        assert b_issues[0].issue_level == IssueSeverity.CRITICAL
        assert b_issues[0].issue_type == "b_class_mismatch"

    def test_unrelated_path_keeps_critical_when_profile_present(self):
        profile = ForksProfile.model_validate(
            {
                "removed_domains": [
                    {"name": "alpha", "paths": ["svc/alpha/**"]},
                ]
            }
        )
        state = _make_state({"svc/other/x.py": FileChangeCategory.B}, profile)
        view = ReadOnlyStateView(state)
        judge = _make_judge(git_tool=MagicMock())

        with patch(
            "src.agents.judge_agent.ThreeWayDiff",
            return_value=_stub_three_way(False, True, []),
        ):
            issues = judge._run_deterministic_pipeline(view, {})

        b_issues = [i for i in issues if "b_class_mismatch" in i.issue_type]
        assert len(b_issues) == 1
        assert b_issues[0].issue_level == IssueSeverity.CRITICAL
        assert b_issues[0].issue_type == "b_class_mismatch"

    def test_fork_pinned_pattern_takes_priority_over_profile(self):
        """always_take_current_patterns wins over forks-profile in label.

        Both signals trigger fork-aware downgrade; the priority order is
        encoded in the suffix/reason builder so audits surface the most
        specific source.
        """
        from src.models.config import FileClassifierConfig

        profile = ForksProfile.model_validate(
            {
                "removed_domains": [
                    {"name": "alpha", "paths": ["svc/alpha/**"]},
                ]
            }
        )
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            repo_path=".",
            file_classifier=FileClassifierConfig(
                always_take_current_patterns=["svc/alpha/**"],
            ),
        )
        state = MergeState(config=config)
        state.merge_base_commit = "base-sha"
        state.file_categories = {"svc/alpha/handler.py": FileChangeCategory.B}
        state.forks_profile = profile
        view = ReadOnlyStateView(state)
        judge = _make_judge(git_tool=MagicMock())

        with patch(
            "src.agents.judge_agent.ThreeWayDiff",
            return_value=_stub_three_way(False, True, []),
        ):
            issues = judge._run_deterministic_pipeline(view, {})

        b_issues = [i for i in issues if "b_class_mismatch" in i.issue_type]
        assert len(b_issues) == 1
        assert b_issues[0].issue_level == IssueSeverity.INFO
        assert b_issues[0].issue_type == "b_class_mismatch_fork_pinned"
        assert "always_take_current_patterns" in b_issues[0].description
