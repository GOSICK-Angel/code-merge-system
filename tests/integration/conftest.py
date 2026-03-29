import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.models.config import MergeConfig, OutputConfig


# ── Shared LLM response payloads ─────────────────────────────────────────────

PLAN_ALL_AUTO_SAFE = json.dumps(
    {
        "phases": [
            {
                "batch_id": "batch-safe-1",
                "phase": "auto_merge",
                "file_paths": ["src/utils.py", "src/helpers.py"],
                "risk_level": "auto_safe",
                "can_parallelize": True,
            }
        ],
        "risk_summary": {
            "total_files": 2,
            "auto_safe_count": 2,
            "auto_risky_count": 0,
            "human_required_count": 0,
            "deleted_only_count": 0,
            "binary_count": 0,
            "excluded_count": 0,
            "estimated_auto_merge_rate": 1.0,
            "top_risk_files": [],
        },
        "project_context_summary": "Integration test project",
        "special_instructions": [],
    }
)

PLAN_ONE_AUTO_RISKY = json.dumps(
    {
        "phases": [
            {
                "batch_id": "batch-risky-1",
                "phase": "auto_merge",
                "file_paths": ["src/service.py"],
                "risk_level": "auto_risky",
                "can_parallelize": False,
            }
        ],
        "risk_summary": {
            "total_files": 1,
            "auto_safe_count": 0,
            "auto_risky_count": 1,
            "human_required_count": 0,
            "deleted_only_count": 0,
            "binary_count": 0,
            "excluded_count": 0,
            "estimated_auto_merge_rate": 0.0,
            "top_risk_files": [],
        },
        "project_context_summary": "Integration test project",
        "special_instructions": [],
    }
)

PLANNER_JUDGE_APPROVED_2 = json.dumps(
    {
        "result": "approved",
        "issues": [],
        "approved_files_count": 2,
        "flagged_files_count": 0,
        "summary": "Plan approved",
    }
)

PLANNER_JUDGE_APPROVED_1 = json.dumps(
    {
        "result": "approved",
        "issues": [],
        "approved_files_count": 1,
        "flagged_files_count": 0,
        "summary": "Plan approved",
    }
)

PLANNER_JUDGE_REVISION_NEEDED = json.dumps(
    {
        "result": "revision_needed",
        "issues": [
            {
                "file_path": "src/utils.py",
                "current_classification": "auto_safe",
                "suggested_classification": "auto_risky",
                "reason": "File contains complex branching logic",
                "issue_type": "risk_underestimated",
            }
        ],
        "approved_files_count": 1,
        "flagged_files_count": 1,
        "summary": "One file needs reclassification",
    }
)

CONFLICT_HIGH_CONFIDENCE = json.dumps(
    {
        "conflict_type": "concurrent_modification",
        "confidence": 0.92,
        "recommended_strategy": "semantic_merge",
        "rationale": "Both changes are independent and can be combined",
        "can_coexist": True,
        "is_security_sensitive": False,
        "upstream_intent": {
            "description": "upstream adds feature",
            "intent_type": "feature",
            "confidence": 0.9,
        },
        "fork_intent": {
            "description": "fork adds endpoint",
            "intent_type": "feature",
            "confidence": 0.9,
        },
    }
)

CONFLICT_LOW_CONFIDENCE = json.dumps(
    {
        "conflict_type": "logic_contradiction",
        "confidence": 0.3,
        "recommended_strategy": "escalate_human",
        "rationale": "Cannot safely determine which logic to keep",
        "can_coexist": False,
        "is_security_sensitive": False,
        "upstream_intent": {
            "description": "upstream logic",
            "intent_type": "unknown",
            "confidence": 0.3,
        },
        "fork_intent": {
            "description": "fork logic",
            "intent_type": "unknown",
            "confidence": 0.3,
        },
    }
)

SEMANTIC_MERGE_CONTENT = "def merged_service():\n    pass\n"

FILE_REVIEW_NO_ISSUES = json.dumps({"issues": []})

JUDGE_VERDICT_PASS = json.dumps(
    {
        "verdict": "pass",
        "summary": "All merges verified correct",
        "confidence": 0.95,
    }
)


# ── Fake Git tool ─────────────────────────────────────────────────────────────


class FakeGitTool:
    """In-memory git tool that writes files to tmp_path."""

    def __init__(
        self,
        repo_path: Path,
        changed_files: list[tuple[str, str]],
        file_contents: dict[str, str],
    ) -> None:
        self.repo_path = repo_path
        self._changed_files = changed_files
        self._file_contents = file_contents

    def get_merge_base(self, upstream_ref: str, fork_ref: str) -> str:
        return "deadbeef00"

    def get_changed_files(self, base: str, head: str) -> list[tuple[str, str]]:
        return self._changed_files

    def get_unified_diff(self, base: str, head: str, file_path: str) -> str:
        return (
            f"--- a/{file_path}\n+++ b/{file_path}\n"
            "@@ -1,2 +1,3 @@\n def existing():\n-    pass\n+    return 1\n"
        )

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        return self._file_contents.get(file_path, f"# content of {file_path}\n")

    def get_three_way_diff(
        self, base: str, fork_ref: str, upstream_ref: str, file_path: str
    ) -> tuple[str | None, str | None, str | None]:
        content = self._file_contents.get(file_path, f"# {file_path}\n")
        return content, content, content

    def write_file_content(self, file_path: str, content: str) -> None:
        dest = self.repo_path / file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def patch_llm_factory(mocker):
    """Prevent LLMClientFactory from checking env vars during Agent.__init__."""
    mocker.patch(
        "src.llm.client.LLMClientFactory.create",
        return_value=MagicMock(),
    )


@pytest.fixture
def make_config(tmp_path):
    def _factory(max_plan_revision_rounds: int = 2) -> MergeConfig:
        (tmp_path / "outputs").mkdir(exist_ok=True)
        return MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/test-branch",
            repo_path=str(tmp_path),
            project_context="Integration test project",
            max_plan_revision_rounds=max_plan_revision_rounds,
            output=OutputConfig(directory=str(tmp_path / "outputs")),
        )

    return _factory


@pytest.fixture
def fake_git_auto_safe(tmp_path) -> FakeGitTool:
    changed = [("M", "src/utils.py"), ("M", "src/helpers.py")]
    contents = {
        "src/utils.py": "def util(): return 1\n",
        "src/helpers.py": "def help(): return 2\n",
    }
    return FakeGitTool(tmp_path, changed, contents)


@pytest.fixture
def fake_git_auto_risky(tmp_path) -> FakeGitTool:
    changed = [("M", "src/service.py")]
    contents = {"src/service.py": "class Service:\n    def run(self): pass\n"}
    return FakeGitTool(tmp_path, changed, contents)
