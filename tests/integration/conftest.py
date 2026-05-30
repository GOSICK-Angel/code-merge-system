import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.models.config import MergeConfig, OutputConfig
from src.tools.git_tool import GitReadStatus


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
                # deliberately OUTSIDE SHORTCIRCUIT_SAFE_ISSUE_TYPES so the R1
                # plan review runs the (scripted) LLM re-check each round instead
                # of auto-applying + short-circuiting — the behaviour these
                # revision-loop tests assert (planner/pj call counts, round
                # exhaustion → AWAITING_HUMAN).
                "issue_type": "classification_dispute",
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
    """In-memory git tool that writes files to tmp_path.

    ``category`` shapes the per-ref blobs so ``classify_all_files`` (which
    compares base/fork/upstream blob hashes) yields the intended change
    category, driving the same routing a real repo would:
      - ``"B"``: only upstream changed (base == fork) → upstream-only auto-merge.
      - ``"C"``: both sides changed (all three differ) → conflict analysis.
    The worktree blob is shaped to the post-merge expectation (upstream for B; a
    distinct merged blob for C) so neither the B-class drift sanity nor the
    C-class preservation audit false-fires on these synthetic refs.
    """

    _MERGE_BASE = "deadbeef00"

    def __init__(
        self,
        repo_path: Path,
        changed_files: list[tuple[str, str]],
        file_contents: dict[str, str],
        *,
        category: str = "C",
        fork_ref: str = "feature/test-branch",
        upstream_ref: str = "upstream/main",
    ) -> None:
        self.repo_path = repo_path
        self._changed_files = changed_files
        self._file_contents = file_contents
        self._category = category
        self._fork_ref = fork_ref
        self._upstream_ref = upstream_ref

    def _blob(self, ref: str, file_path: str) -> str:
        """Role-shaped blob for *ref*. Distinct strings double as blob hashes."""
        base = self._file_contents.get(file_path, f"# content of {file_path}\n")
        if self._category == "B":
            return (
                base + "    # upstream change\n" if ref == self._upstream_ref else base
            )
        # "C": fork and upstream make CONFLICTING edits to the SAME (first) base
        # line, so every RuleBasedResolver pattern (line-addition union /
        # adjacent-edit / whitespace-only / import-union) declines and the
        # scripted LLM analyst actually runs — these tests exercise the
        # analyst-driven semantic-merge / escalation flow, not the rule resolver.
        lines = base.split("\n")
        if ref == self._fork_ref:
            lines[0] = lines[0] + "  # fork-edit"
            return "\n".join(lines)
        if ref == self._upstream_ref:
            lines[0] = lines[0] + "  # upstream-edit"
            return "\n".join(lines)
        return base

    def _worktree_blob(self, file_path: str) -> str:
        base = self._file_contents.get(file_path, f"# content of {file_path}\n")
        if self._category == "B":
            return base + "    # upstream change\n"  # take_target → upstream content
        # "C" merged result keeps the fork's edit (no fork loss) and upstream's.
        lines = base.split("\n")
        lines[0] = lines[0] + "  # fork-edit  # upstream-edit"
        return "\n".join(lines)

    def get_merge_base(self, upstream_ref: str, fork_ref: str) -> str:
        return self._MERGE_BASE

    def get_changed_files(self, base: str, head: str) -> list[tuple[str, str]]:
        return self._changed_files

    def get_unified_diff(self, base: str, head: str, file_path: str) -> str:
        return (
            f"--- a/{file_path}\n+++ b/{file_path}\n"
            "@@ -1,2 +1,3 @@\n def existing():\n-    pass\n+    return 1\n"
        )

    def get_file_content(self, ref: str, file_path: str) -> str | None:
        if file_path not in self._file_contents:
            return f"# content of {file_path}\n"
        return self._blob(ref, file_path)

    def get_three_way_diff(
        self, base: str, fork_ref: str, upstream_ref: str, file_path: str
    ) -> tuple[str | None, str | None, str | None]:
        return (
            self._blob(base, file_path),
            self._blob(fork_ref, file_path),
            self._blob(upstream_ref, file_path),
        )

    def write_file_content(self, file_path: str, content: str) -> None:
        dest = self.repo_path / file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    def create_working_branch(self, branch_name: str, base_ref: str) -> str:
        """In-memory stand-in for U7 working-branch creation. The orchestrator
        calls this when ``enable_working_branch`` (default True) is set; the fake
        just resolves the ``{timestamp}`` template and returns the name (no real
        branch — these tests operate on tmp_path directly)."""
        from datetime import datetime

        return branch_name.replace(
            "{timestamp}", datetime.now().strftime("%Y%m%d-%H%M%S")
        )

    # ── read surface the current pipeline introspects (safe in-memory stubs) ──
    # All refs resolve to identical blobs (no spurious drift / rename / fork-loss
    # detection) so these tests exercise the LLM-driven flow, not git heuristics.
    def get_status(self) -> list[tuple[str, str]]:
        return []

    def _sha(self, content: str) -> str:
        # Real git blob sha so the patch applier's post-write self-check
        # (``_git_blob_sha(written) == get_worktree_blob_sha``) matches what was
        # actually written to disk — required once a file reaches the real
        # semantic-merge write path (previously short-circuited by the resolver).
        from src.tools.patch_applier import _git_blob_sha

        return _git_blob_sha(content.encode("utf-8"))

    def list_files_with_hashes(self, ref: str) -> dict[str, str]:
        return {fp: self._sha(self._blob(ref, fp)) for fp in self._file_contents}

    def list_files(self, ref: str) -> list[str]:
        return list(self._file_contents)

    def get_unmerged_files(self) -> list[str]:
        return []

    def get_file_hash(self, ref: str, file_path: str) -> str | None:
        if file_path in self._file_contents:
            return self._sha(self._blob(ref, file_path))
        return None

    def get_worktree_blob_sha(self, file_path: str) -> str | None:
        # Post-write: hash the ACTUAL on-disk file so the patch applier's
        # self-check passes. Pre-write: fall back to the synthetic merged blob.
        dest = self.repo_path / file_path
        if dest.exists():
            return self._sha(dest.read_text(encoding="utf-8"))
        if file_path in self._file_contents:
            return self._sha(self._worktree_blob(file_path))
        return None

    # W1 status-returning twins (consumed by the B-class sanity, executor
    # fork-export, and judge take-verification gates). The fake never errors, so
    # a present value is OK and a missing one is ABSENT (never GIT_ERROR).
    def get_file_hash_checked(
        self, ref: str, file_path: str
    ) -> tuple[str | None, GitReadStatus]:
        val = self.get_file_hash(ref, file_path)
        return val, GitReadStatus.OK if val is not None else GitReadStatus.ABSENT

    def get_worktree_blob_sha_checked(
        self, file_path: str
    ) -> tuple[str | None, GitReadStatus]:
        val = self.get_worktree_blob_sha(file_path)
        return val, GitReadStatus.OK if val is not None else GitReadStatus.ABSENT

    def get_file_content_checked(
        self, ref: str, file_path: str
    ) -> tuple[str | None, GitReadStatus]:
        val = self.get_file_content(ref, file_path)
        return val, GitReadStatus.OK if val is not None else GitReadStatus.ABSENT

    def get_file_bytes(self, ref: str, file_path: str) -> bytes | None:
        content = self.get_file_content(ref, file_path)
        return content.encode("utf-8") if content is not None else None

    def file_exists_at_ref(self, ref: str, file_path: str) -> bool:
        return file_path in self._file_contents

    def detect_renames(self, base_ref: str, head_ref: str) -> list[tuple[str, str]]:
        return []

    def list_commits(self, base: str, head: str) -> list[dict[str, object]]:
        return []

    def get_commit_messages(
        self, file_path: str, ref: str, limit: int = 10
    ) -> list[str]:
        return []

    def three_way_merge_file(
        self, base_ref: str, ours_ref: str, theirs_ref: str, file_path: str
    ) -> str | None:
        # no native clean merge → defer to the LLM path (matches the conflict
        # scenarios these tests script).
        return None

    def checkout_file(self, ref: str, file_path: str) -> bool:
        return True

    def grep_in_files(self, *args: object, **kwargs: object) -> list[object]:
        return []

    # ── write / commit surface (no-op; files land on disk via write_file_content) ──
    def stage_files(self, file_paths: list[str]) -> None:
        return None

    def has_staged_changes(self) -> bool:
        return True

    def commit_staged(self, message: str) -> str:
        return "c0ffee00"

    def commit_with_author(
        self, message: str, author_name: str, author_email: str
    ) -> str:
        return "c0ffee00"

    def get_head_sha(self) -> str:
        return "head0000"

    def reload_index(self) -> None:
        return None


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def patch_llm_factory(mocker):
    """Prevent LLMClientFactory from checking env vars during Agent.__init__,
    and give the client benign awaitable completion methods.

    Tests override ``agent._call_llm_with_retry`` per-instance for the agents
    they script. Agents the current pipeline also invokes but the test did NOT
    script (e.g. the Judge's final-verdict synthesis) otherwise fall through to
    the real ``_call_llm_with_retry`` and await a bare ``MagicMock`` → a
    ``TypeError`` that triggers 3 slow retries and a degraded fallback. Returning
    a client whose async methods yield a benign empty-JSON keeps those unscripted
    calls fast and non-crashing; deterministic fallbacks then handle the result.
    """
    from unittest.mock import AsyncMock

    from src.llm.client import LLMResponse

    client = MagicMock()
    client.complete = AsyncMock(return_value="{}")
    client.complete_meta = AsyncMock(
        return_value=LLMResponse(text="{}", stop_reason="stop")
    )
    client.structured_json = AsyncMock(return_value="{}")
    client.complete_structured = AsyncMock(return_value=MagicMock())
    mocker.patch(
        "src.llm.client.LLMClientFactory.create",
        return_value=client,
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
    # upstream-only change (B-class) → clean auto-merge, no conflict analysis.
    return FakeGitTool(tmp_path, changed, contents, category="B")


@pytest.fixture
def fake_git_auto_risky(tmp_path) -> FakeGitTool:
    changed = [("M", "src/service.py")]
    contents = {"src/service.py": "class Service:\n    def run(self): pass\n"}
    # both sides changed (C-class) → conflict analysis path.
    return FakeGitTool(tmp_path, changed, contents, category="C")
