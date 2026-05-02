"""Tests for PhaseSummarizer."""

from unittest.mock import MagicMock, patch

from datetime import datetime

from src.memory.models import MemoryEntryType
from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import (
    DecisionSource,
    FileDecisionRecord,
    MergeDecision,
)
from src.models.diff import FileChangeCategory, FileStatus
from src.models.judge import (
    IssueSeverity,
    IssueResolvability,
    JudgeIssue,
    JudgeVerdict,
    VerdictType,
)
from src.models.state import MergeState


def _make_state() -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    return MergeState(config=config)


class TestSummarizePlanning:
    def test_basic_summary(self):
        state = _make_state()
        state.file_categories = {
            "api/models/user.py": FileChangeCategory.C,
            "api/models/team.py": FileChangeCategory.C,
            "api/models/org.py": FileChangeCategory.C,
            "vendor/lib.py": FileChangeCategory.B,
            "unchanged.py": FileChangeCategory.A,
        }
        summarizer = PhaseSummarizer()
        summary, entries = summarizer.summarize_planning(state)

        assert summary.phase == "planning"
        assert summary.files_processed == 5
        assert summary.statistics["both_changed"] == 3
        assert summary.statistics["upstream_only"] == 1

    def test_detects_c_class_concentration(self):
        state = _make_state()
        state.file_categories = {
            "api/models/a.py": FileChangeCategory.C,
            "api/models/b.py": FileChangeCategory.C,
            "api/models/c.py": FileChangeCategory.C,
            "web/app.tsx": FileChangeCategory.B,
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_planning(state)

        pattern_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.PATTERN
        ]
        assert len(pattern_entries) >= 1
        assert any("api" in e.content for e in pattern_entries)

    def test_no_patterns_for_few_files(self):
        state = _make_state()
        state.file_categories = {
            "a.py": FileChangeCategory.C,
            "b.py": FileChangeCategory.B,
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_planning(state)
        assert len(entries) == 0


class TestSummarizeAutoMerge:
    def _make_record(self, path: str, decision: MergeDecision) -> FileDecisionRecord:
        return FileDecisionRecord(
            file_path=path,
            file_status=FileStatus.MODIFIED,
            decision=decision,
            decision_source=DecisionSource.AUTO_EXECUTOR,
            rationale="auto",
        )

    def test_basic_summary(self):
        state = _make_state()
        state.file_decision_records = {
            "a.py": self._make_record("a.py", MergeDecision.TAKE_TARGET),
            "b.py": self._make_record("b.py", MergeDecision.TAKE_TARGET),
            "c.py": self._make_record("c.py", MergeDecision.SEMANTIC_MERGE),
        }
        summarizer = PhaseSummarizer()
        summary, _ = summarizer.summarize_auto_merge(state)

        assert summary.phase == "auto_merge"
        assert summary.files_processed == 3
        assert summary.statistics["take_target"] == 2
        assert summary.statistics["semantic_merge"] == 1

    def test_detects_directory_dominance(self):
        state = _make_state()
        records = {}
        for i in range(5):
            path = f"vendor/lib/file{i}.py"
            records[path] = self._make_record(path, MergeDecision.TAKE_TARGET)
        records["api/service.py"] = self._make_record(
            "api/service.py", MergeDecision.SEMANTIC_MERGE
        )
        state.file_decision_records = records

        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_auto_merge(state)

        pattern_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.PATTERN
        ]
        assert len(pattern_entries) >= 1
        assert any("vendor" in e.content for e in pattern_entries)

    def test_no_dominance_pattern_for_mixed(self):
        state = _make_state()
        state.file_decision_records = {
            "api/a.py": self._make_record("api/a.py", MergeDecision.TAKE_TARGET),
            "api/b.py": self._make_record("api/b.py", MergeDecision.SEMANTIC_MERGE),
            "api/c.py": self._make_record("api/c.py", MergeDecision.ESCALATE_HUMAN),
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_auto_merge(state)
        assert len(entries) == 0


class TestSummarizeConflictAnalysis:
    def _make_analysis(self, path: str, ctype: ConflictType) -> ConflictAnalysis:
        return ConflictAnalysis(
            file_path=path,
            conflict_points=[],
            overall_confidence=0.7,
            recommended_strategy=MergeDecision.SEMANTIC_MERGE,
            conflict_type=ctype,
            rationale="test",
            confidence=0.7,
        )

    def test_basic_summary(self):
        state = _make_state()
        state.conflict_analyses = {
            "a.py": self._make_analysis("a.py", ConflictType.CONCURRENT_MODIFICATION),
            "b.py": self._make_analysis("b.py", ConflictType.DEPENDENCY_UPDATE),
        }
        summarizer = PhaseSummarizer()
        summary, _ = summarizer.summarize_conflict_analysis(state)

        assert summary.phase == "conflict_analysis"
        assert summary.files_processed == 2

    def test_detects_recurring_conflict_type(self):
        state = _make_state()
        state.conflict_analyses = {}
        for i in range(4):
            path = f"api/models/m{i}.py"
            state.conflict_analyses[path] = self._make_analysis(
                path, ConflictType.CONCURRENT_MODIFICATION
            )
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_conflict_analysis(state)

        assert len(entries) >= 1
        assert any("concurrent_modification" in e.content for e in entries)

    def test_writes_per_file_decision_entries(self):
        state = _make_state()
        state.conflict_analyses = {
            "models/azure_openai/llm.py": self._make_analysis(
                "models/azure_openai/llm.py", ConflictType.CONCURRENT_MODIFICATION
            ),
            "models/tongyi/tts.py": self._make_analysis(
                "models/tongyi/tts.py", ConflictType.LOGIC_CONTRADICTION
            ),
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_conflict_analysis(state)

        decision_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.DECISION
        ]
        assert len(decision_entries) == 2
        paths_in_entries = {fp for e in decision_entries for fp in e.file_paths}
        assert "models/azure_openai/llm.py" in paths_in_entries
        assert "models/tongyi/tts.py" in paths_in_entries
        assert any("models/azure_openai" in fp for fp in paths_in_entries)

    def test_upstream_ref_tag_in_decision_entries(self):
        state = _make_state()
        state.conflict_analyses = {
            "models/vertex_ai/llm.py": self._make_analysis(
                "models/vertex_ai/llm.py", ConflictType.CONCURRENT_MODIFICATION
            ),
        }
        summarizer = PhaseSummarizer(upstream_ref="26d88b58abcd1234")
        _, entries = summarizer.summarize_conflict_analysis(state)

        decision_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.DECISION
        ]
        assert len(decision_entries) == 1
        tags = decision_entries[0].tags
        assert any(t == "upstream_ref:26d88b58" for t in tags)

    def test_plugin_dir_in_file_paths(self):
        state = _make_state()
        state.conflict_analyses = {
            "models/volcengine_maas/models/llm/llm.py": self._make_analysis(
                "models/volcengine_maas/models/llm/llm.py",
                ConflictType.CONCURRENT_MODIFICATION,
            ),
        }
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_conflict_analysis(state)

        decision_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.DECISION
        ]
        assert len(decision_entries) == 1
        file_paths = decision_entries[0].file_paths
        assert "models/volcengine_maas/models/llm/llm.py" in file_paths
        assert "models/volcengine_maas" in file_paths


class TestSummarizeJudgeReview:
    def test_basic_summary(self):
        state = _make_state()
        state.judge_repair_rounds = 2
        state.judge_verdicts_log = [
            {"verdict": "fail", "issues": [{"issue_type": "missing_logic"}]},
            {"verdict": "pass", "issues": []},
        ]
        summarizer = PhaseSummarizer()
        summary, _ = summarizer.summarize_judge_review(state)

        assert summary.phase == "judge_review"
        assert summary.statistics["total_rounds"] == 2
        assert summary.statistics["repair_rounds"] == 2

    def test_detects_recurring_issue(self):
        state = _make_state()
        state.judge_repair_rounds = 3
        state.judge_verdicts_log = [
            {
                "verdict": "fail",
                "issues": [
                    {"issue_type": "missing_logic"},
                    {"issue_type": "missing_logic"},
                ],
            },
            {
                "verdict": "fail",
                "issues": [{"issue_type": "missing_logic"}],
            },
            {"verdict": "pass", "issues": []},
        ]
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_judge_review(state)

        assert len(entries) >= 1
        assert any("missing_logic" in e.content for e in entries)

    def test_empty_verdicts(self):
        state = _make_state()
        state.judge_verdicts_log = []
        summarizer = PhaseSummarizer()
        summary, entries = summarizer.summarize_judge_review(state)
        assert summary.statistics["total_rounds"] == 0
        assert len(entries) == 0

    def _make_verdict(self, failed_files: list[str], issue_type: str) -> JudgeVerdict:
        issues = [
            JudgeIssue(
                file_path=fp,
                issue_level=IssueSeverity.HIGH,
                issue_type=issue_type,
                description="test",
                resolvability=IssueResolvability.FIXABLE,
            )
            for fp in failed_files
        ]
        return JudgeVerdict(
            verdict=VerdictType.FAIL,
            reviewed_files_count=len(failed_files),
            passed_files=[],
            failed_files=failed_files,
            conditional_files=[],
            issues=issues,
            critical_issues_count=0,
            high_issues_count=len(failed_files),
            overall_confidence=0.5,
            summary="test",
            blocking_issues=[],
            timestamp=datetime.now(),
            judge_model="test-model",
        )

    def test_writes_failed_file_decision_entries(self):
        state = _make_state()
        state.judge_verdicts_log = []
        state.judge_verdict = self._make_verdict(
            ["models/azure_openai/llm.py", "models/tongyi/llm.py"],
            "b_class_drift",
        )
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_judge_review(state)

        decision_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.DECISION
        ]
        assert len(decision_entries) == 2
        contents = [e.content for e in decision_entries]
        assert any("azure_openai" in c for c in contents)
        assert any("judge FAIL" in c for c in contents)
        assert all("b_class_drift" in c for c in contents)

    def test_judge_fail_entries_include_plugin_dir(self):
        state = _make_state()
        state.judge_verdicts_log = []
        state.judge_verdict = self._make_verdict(
            ["models/vertex_ai/models/llm/llm.py"], "upstream_content_mismatch"
        )
        summarizer = PhaseSummarizer()
        _, entries = summarizer.summarize_judge_review(state)

        decision_entries = [
            e for e in entries if e.entry_type == MemoryEntryType.DECISION
        ]
        assert len(decision_entries) == 1
        assert "models/vertex_ai" in decision_entries[0].file_paths
