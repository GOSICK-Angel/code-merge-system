import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.decision import MergeDecision, FileDecisionRecord, DecisionSource
from src.models.human import HumanDecisionRequest, DecisionOption
from src.models.judge import JudgeVerdict, VerdictType
from src.models.plan import MergePlan, RiskSummary


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")


def _make_state() -> MergeState:
    return MergeState(config=_make_config())


def _make_file_diff(
    file_path: str = "src/main.py",
    risk_level: RiskLevel = RiskLevel.AUTO_SAFE,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        risk_level=risk_level,
        risk_score=0.2,
    )


def _make_decision_record(
    file_path: str = "src/main.py",
    decision: MergeDecision = MergeDecision.TAKE_TARGET,
    confidence: float | None = 0.9,
) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=decision,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=confidence,
        rationale="auto merged",
    )


def _make_merge_plan() -> MergePlan:
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        merge_base_commit="abc123",
        phases=[],
        risk_summary=RiskSummary(
            total_files=5,
            auto_safe_count=3,
            auto_risky_count=1,
            human_required_count=1,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=0.6,
        ),
        project_context_summary="test project",
    )


def _make_judge_verdict(verdict_type: VerdictType = VerdictType.PASS) -> JudgeVerdict:
    return JudgeVerdict(
        verdict=verdict_type,
        reviewed_files_count=2,
        passed_files=["src/a.py"],
        failed_files=[],
        conditional_files=[],
        issues=[],
        critical_issues_count=0,
        high_issues_count=0,
        overall_confidence=0.9,
        summary="All passed",
        blocking_issues=[],
        timestamp=datetime.now(),
        judge_model="test-model",
    )


def _make_human_request(file_path: str = "src/auth.py") -> HumanDecisionRequest:
    return HumanDecisionRequest(
        file_path=file_path,
        priority=5,
        conflict_points=[],
        context_summary="Auth file conflict",
        upstream_change_summary="Added OAuth",
        fork_change_summary="Fixed bug",
        analyst_recommendation=MergeDecision.TAKE_TARGET,
        analyst_confidence=0.75,
        analyst_rationale="Upstream more complete",
        options=[
            DecisionOption(
                option_key="A",
                decision=MergeDecision.TAKE_TARGET,
                description="Take upstream",
                risk_warning="May break fork customizations",
            ),
            DecisionOption(
                option_key="B",
                decision=MergeDecision.TAKE_CURRENT,
                description="Keep fork",
            ),
        ],
        created_at=datetime.now(),
    )


class TestWriteMarkdownReport:
    def test_creates_file_in_output_dir(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        result = write_markdown_report(state, str(tmp_path))

        assert result.exists()
        assert result.name == f"merge_report_{state.run_id}.md"

    def test_creates_output_dir_if_not_exists(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        output_dir = str(tmp_path / "new_dir" / "nested")
        state = _make_state()
        result = write_markdown_report(state, output_dir)

        assert result.exists()

    def test_includes_run_id_in_report(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert state.run_id in content

    def test_includes_status(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.status = SystemStatus.COMPLETED
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "completed" in content

    def test_includes_merge_plan_section(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.merge_plan = _make_merge_plan()
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "Merge Plan" in content
        assert "upstream/main" in content
        assert "feature/fork" in content
        assert "abc123" in content

    def test_includes_risk_summary(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.merge_plan = _make_merge_plan()
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "Total files: 5" in content
        assert "Auto-safe: 3" in content

    def test_includes_file_decision_records(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.file_decision_records["src/main.py"] = _make_decision_record()
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "File Decision Records" in content
        assert "src/main.py" in content
        assert "take_target" in content
        assert "0.90" in content

    def test_handles_none_confidence_in_records(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.file_decision_records["src/main.py"] = _make_decision_record(
            confidence=None
        )
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "N/A" in content

    def test_includes_judge_verdict(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.judge_verdict = _make_judge_verdict(VerdictType.CONDITIONAL)
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "Judge Verdict" in content
        assert "conditional" in content
        assert "All passed" in content

    def test_includes_errors_section(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        state.errors = [{"phase": "auto_merge", "message": "Something went wrong"}]
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "Errors" in content
        assert "Something went wrong" in content

    def test_no_plan_section_when_no_plan(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        result = write_markdown_report(state, str(tmp_path))

        content = result.read_text()
        assert "Merge Plan" not in content

    def test_returns_path_object(self, tmp_path):
        from src.tools.report_writer import write_markdown_report

        state = _make_state()
        result = write_markdown_report(state, str(tmp_path))

        assert isinstance(result, Path)


class TestWriteJsonReport:
    def test_creates_json_file(self, tmp_path):
        from src.tools.report_writer import write_json_report

        state = _make_state()
        result = write_json_report(state, str(tmp_path))

        assert result.exists()
        assert result.name == f"merge_report_{state.run_id}.json"

    def test_creates_output_dir_if_not_exists(self, tmp_path):
        from src.tools.report_writer import write_json_report

        output_dir = str(tmp_path / "deep" / "nested")
        state = _make_state()
        result = write_json_report(state, output_dir)

        assert result.exists()

    def test_output_is_valid_json(self, tmp_path):
        from src.tools.report_writer import write_json_report

        state = _make_state()
        result = write_json_report(state, str(tmp_path))

        data = json.loads(result.read_text())
        assert isinstance(data, dict)

    def test_json_contains_run_id(self, tmp_path):
        from src.tools.report_writer import write_json_report

        state = _make_state()
        result = write_json_report(state, str(tmp_path))

        data = json.loads(result.read_text())
        assert data["run_id"] == state.run_id

    def test_json_contains_status(self, tmp_path):
        from src.tools.report_writer import write_json_report

        state = _make_state()
        state.status = SystemStatus.COMPLETED
        result = write_json_report(state, str(tmp_path))

        data = json.loads(result.read_text())
        assert data["status"] == "completed"

    def test_returns_path_object(self, tmp_path):
        from src.tools.report_writer import write_json_report

        state = _make_state()
        result = write_json_report(state, str(tmp_path))

        assert isinstance(result, Path)


class TestWriteHumanDecisionReport:
    def test_creates_human_decision_file(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        result = write_human_decision_report(state, str(tmp_path))

        assert result.exists()
        assert result.name == f"human_decisions_{state.run_id}.md"

    def test_creates_output_dir_if_not_exists(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        output_dir = str(tmp_path / "new")
        state = _make_state()
        result = write_human_decision_report(state, output_dir)

        assert result.exists()

    def test_includes_run_id_in_report(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert state.run_id in content

    def test_includes_request_details(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        req = _make_human_request("src/auth.py")
        state.human_decision_requests["src/auth.py"] = req
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert "src/auth.py" in content
        assert "Auth file conflict" in content
        assert "Added OAuth" in content
        assert "Fixed bug" in content

    def test_includes_options(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        req = _make_human_request()
        state.human_decision_requests["src/auth.py"] = req
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert "take_target" in content
        assert "take_current" in content

    def test_includes_risk_warning(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        req = _make_human_request()
        state.human_decision_requests["src/auth.py"] = req
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert "May break fork customizations" in content

    def test_includes_analyst_recommendation(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        req = _make_human_request()
        state.human_decision_requests["src/auth.py"] = req
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert "take_target" in content
        assert "0.75" in content

    def test_empty_requests_generates_basic_report(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert "Human Decision Required" in content

    def test_returns_path_object(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        result = write_human_decision_report(state, str(tmp_path))

        assert isinstance(result, Path)

    def test_multiple_requests_all_included(self, tmp_path):
        from src.tools.report_writer import write_human_decision_report

        state = _make_state()
        req1 = _make_human_request("src/auth.py")
        req2 = _make_human_request("src/models.py")
        req2 = req2.model_copy(
            update={
                "context_summary": "Model schema conflict",
                "options": [
                    DecisionOption(
                        option_key="A",
                        decision=MergeDecision.SEMANTIC_MERGE,
                        description="Merge both versions",
                    )
                ],
            }
        )
        state.human_decision_requests["src/auth.py"] = req1
        state.human_decision_requests["src/models.py"] = req2
        result = write_human_decision_report(state, str(tmp_path))

        content = result.read_text()
        assert "src/auth.py" in content
        assert "src/models.py" in content
        assert "Model schema conflict" in content


class TestGitTool:
    def setup_method(self):
        self.mock_repo = MagicMock()
        self.mock_repo.working_tree_dir = "/repo"

    def _create_git_tool(self):
        with patch("src.tools.git_tool.Repo") as mock_repo_cls:
            mock_repo_cls.return_value = self.mock_repo
            from src.tools.git_tool import GitTool

            tool = GitTool("/repo")
            return tool

    def test_init_sets_repo_path(self):
        tool = self._create_git_tool()
        assert str(tool.repo_path) == "/repo"

    def test_init_raises_on_invalid_repo(self):
        from git import InvalidGitRepositoryError
        from src.tools.git_tool import GitTool

        with patch(
            "src.tools.git_tool.Repo",
            side_effect=InvalidGitRepositoryError("not a repo"),
        ):
            with pytest.raises(ValueError, match="Not a valid git repository"):
                GitTool("/not-a-repo")

    def test_get_merge_base_returns_commit(self):
        self.mock_repo.git.merge_base.return_value = "abc123def456\n"
        tool = self._create_git_tool()

        result = tool.get_merge_base("upstream/main", "feature/fork")

        assert result == "abc123def456"
        self.mock_repo.git.merge_base.assert_called_once_with(
            "upstream/main", "feature/fork"
        )

    def test_get_changed_files_parses_output(self):
        self.mock_repo.git.diff.return_value = (
            "M\tsrc/main.py\nA\tsrc/new.py\nD\tsrc/old.py\n"
        )
        tool = self._create_git_tool()

        results = tool.get_changed_files("base_commit", "head_commit")

        assert len(results) == 3
        assert ("M", "src/main.py") in results
        assert ("A", "src/new.py") in results
        assert ("D", "src/old.py") in results

    def test_get_changed_files_handles_renames(self):
        self.mock_repo.git.diff.return_value = "R100\told.py\tnew.py\n"
        tool = self._create_git_tool()

        results = tool.get_changed_files("base", "head")

        assert len(results) == 1
        assert results[0][0] == "R"
        assert results[0][1] == "new.py"

    def test_get_changed_files_skips_empty_lines(self):
        self.mock_repo.git.diff.return_value = "M\tsrc/main.py\n\n  \nA\tsrc/new.py\n"
        tool = self._create_git_tool()

        results = tool.get_changed_files("base", "head")

        assert len(results) == 2

    def test_get_file_content_returns_content(self):
        self.mock_repo.git.show.return_value = "file content here"
        tool = self._create_git_tool()

        result = tool.get_file_content("main", "src/main.py")

        assert result == "file content here"
        self.mock_repo.git.show.assert_called_once_with("main:src/main.py")

    def test_get_file_content_returns_none_on_error(self):
        import git

        self.mock_repo.git.show.side_effect = git.GitCommandError("show", 128)
        tool = self._create_git_tool()

        result = tool.get_file_content("main", "nonexistent.py")

        assert result is None

    def test_get_three_way_diff_calls_get_file_content_three_times(self):
        self.mock_repo.git.show.side_effect = [
            "base content",
            "current content",
            "target content",
        ]
        tool = self._create_git_tool()

        base, current, target = tool.get_three_way_diff(
            "base", "fork", "upstream", "file.py"
        )

        assert base == "base content"
        assert current == "current content"
        assert target == "target content"

    def test_get_three_way_diff_handles_missing_files(self):
        import git

        self.mock_repo.git.show.side_effect = git.GitCommandError("show", 128)
        tool = self._create_git_tool()

        base, current, target = tool.get_three_way_diff(
            "base", "fork", "upstream", "new.py"
        )

        assert base is None
        assert current is None
        assert target is None

    def test_write_file_content_writes_to_path(self, tmp_path):
        self.mock_repo.working_tree_dir = str(tmp_path)
        tool = self._create_git_tool()

        tool.write_file_content("src/test.py", "print('hello')")

        written = (tmp_path / "src" / "test.py").read_text()
        assert written == "print('hello')"

    def test_write_file_content_creates_parent_dirs(self, tmp_path):
        self.mock_repo.working_tree_dir = str(tmp_path)
        tool = self._create_git_tool()

        tool.write_file_content("deep/nested/file.py", "content")

        assert (tmp_path / "deep" / "nested" / "file.py").exists()

    def test_apply_patch_returns_true_on_success(self):
        tool = self._create_git_tool()

        result = tool.apply_patch("diff content")

        assert result is True

    def test_apply_patch_returns_false_on_error(self):
        import git

        self.mock_repo.git.apply.side_effect = git.GitCommandError("apply", 1)
        tool = self._create_git_tool()

        result = tool.apply_patch("bad patch")

        assert result is False

    def test_get_commit_messages_returns_list(self):
        self.mock_repo.git.log.return_value = (
            "abc123 fix: something\ndef456 feat: added feature\n"
        )
        tool = self._create_git_tool()

        messages = tool.get_commit_messages("src/main.py", "main", limit=5)

        assert len(messages) == 2
        assert "abc123 fix: something" in messages

    def test_get_commit_messages_returns_empty_on_error(self):
        import git

        self.mock_repo.git.log.side_effect = git.GitCommandError("log", 128)
        tool = self._create_git_tool()

        messages = tool.get_commit_messages("nonexistent.py", "main")

        assert messages == []

    def test_get_unified_diff_returns_diff_string(self):
        self.mock_repo.git.diff.return_value = "@@ -1,3 +1,4 @@\n context\n+added\n"
        tool = self._create_git_tool()

        result = tool.get_unified_diff("base", "head", "src/main.py")

        assert "@@ -1,3 +1,4 @@" in result

    def test_get_unified_diff_returns_empty_on_error(self):
        import git

        self.mock_repo.git.diff.side_effect = git.GitCommandError("diff", 128)
        tool = self._create_git_tool()

        result = tool.get_unified_diff("base", "head", "nonexistent.py")

        assert result == ""

    def test_is_binary_file_returns_true_for_binary(self):
        self.mock_repo.git.diff.return_value = "-\t-\tbinary.png"
        tool = self._create_git_tool()

        assert tool.is_binary_file("main", "binary.png") is True

    def test_is_binary_file_returns_false_for_text(self):
        self.mock_repo.git.diff.return_value = "10\t5\tsrc/main.py"
        tool = self._create_git_tool()

        assert tool.is_binary_file("main", "src/main.py") is False

    def test_is_binary_file_returns_false_on_error(self):
        import git

        self.mock_repo.git.diff.side_effect = git.GitCommandError("diff", 128)
        tool = self._create_git_tool()

        assert tool.is_binary_file("main", "file.py") is False

    def test_get_current_branch_returns_branch_name(self):
        self.mock_repo.active_branch.name = "feature/my-branch"
        tool = self._create_git_tool()

        result = tool.get_current_branch()

        assert result == "feature/my-branch"

    def test_stage_file_calls_index_add(self):
        tool = self._create_git_tool()

        tool.stage_file("src/main.py")

        self.mock_repo.index.add.assert_called_once_with(["src/main.py"])

    def test_get_status_parses_output(self):
        self.mock_repo.git.status.return_value = (
            "M  src/main.py\nA  src/new.py\n?? untracked.py\n"
        )
        tool = self._create_git_tool()

        results = tool.get_status()

        assert len(results) == 3
        assert ("M", "src/main.py") in results
        assert ("A", "src/new.py") in results

    def test_get_status_skips_short_lines(self):
        self.mock_repo.git.status.return_value = "M  src/main.py\nX\n"
        tool = self._create_git_tool()

        results = tool.get_status()

        assert len(results) == 1

    def test_create_working_branch_checks_out_branch(self):
        tool = self._create_git_tool()

        result = tool.create_working_branch("merge/auto", "main")

        assert result == "merge/auto"
        self.mock_repo.git.checkout.assert_any_call("main")
        self.mock_repo.git.checkout.assert_any_call("-b", "merge/auto")
