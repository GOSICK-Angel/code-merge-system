"""P2-3 (§6.2 items 2 & 3) tests.

Item 2: executor's semantic_merge failure path stashes the upstream
diff to a patch file instead of silently dropping it.

Item 3: judge consults the frozen fork-divergence map so intentional
fork actions (deletions, rewrites) are surfaced as INFO instead of
CRITICAL.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import git
import pytest

from src.models.diff import ForkDivergence
from src.tools.diff_stasher import safe_patch_filename, stash_upstream_diff
from src.tools.file_classifier import compute_fork_divergence_map


def test_safe_patch_filename_flattens_path_separators():
    assert (
        safe_patch_filename("backend/src/services/auth/auth.service.ts")
        == "backend__src__services__auth__auth.service.ts.patch"
    )


def test_safe_patch_filename_handles_windows_separator():
    assert (
        safe_patch_filename("backend\\src\\services\\auth.ts")
        == "backend__src__services__auth.ts.patch"
    )


def test_stash_upstream_diff_writes_patch_with_header(tmp_path: Path):
    git_tool = MagicMock()
    git_tool.repo.git.diff.return_value = (
        "--- a/foo.ts\n+++ b/foo.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )

    stash_dir = tmp_path / "stashes"
    out = stash_upstream_diff(
        "src/foo.ts",
        "base-sha",
        "upstream/main",
        git_tool,
        stash_dir,
    )

    assert out is not None
    assert out.exists()
    assert out.name == "src__foo.ts.patch"
    text = out.read_text(encoding="utf-8")
    assert "# file:     src/foo.ts" in text
    assert "# base:     base-sha" in text
    assert "# upstream: upstream/main" in text
    assert "--- a/foo.ts" in text


def test_stash_upstream_diff_returns_none_on_empty_diff(tmp_path: Path):
    git_tool = MagicMock()
    git_tool.repo.git.diff.return_value = ""

    out = stash_upstream_diff(
        "src/foo.ts",
        "base-sha",
        "upstream/main",
        git_tool,
        tmp_path / "stashes",
    )

    assert out is None
    assert not (tmp_path / "stashes").exists()


def test_stash_upstream_diff_returns_none_when_refs_missing(tmp_path: Path):
    git_tool = MagicMock()
    out = stash_upstream_diff("src/foo.ts", "", "upstream/main", git_tool, tmp_path)
    assert out is None
    out2 = stash_upstream_diff("src/foo.ts", "base", "", git_tool, tmp_path)
    assert out2 is None
    git_tool.repo.git.diff.assert_not_called()


def test_stash_upstream_diff_swallows_git_failure(tmp_path: Path):
    git_tool = MagicMock()
    git_tool.repo.git.diff.side_effect = git.GitCommandError("diff", 128)

    out = stash_upstream_diff(
        "src/foo.ts",
        "base-sha",
        "upstream/main",
        git_tool,
        tmp_path / "stashes",
    )
    assert out is None


def _make_git_tool_with_hashes(
    base: dict[str, str], head: dict[str, str], up: dict[str, str]
) -> MagicMock:
    git_tool = MagicMock()

    def fake_list(ref: str) -> dict[str, str]:
        return {
            "base": base,
            "head": head,
            "upstream": up,
        }[ref]

    git_tool.list_files_with_hashes.side_effect = fake_list
    return git_tool


def test_compute_fork_divergence_map_classifies_all_six_kinds():
    git_tool = _make_git_tool_with_hashes(
        base={
            "unchanged.py": "h1",
            "fork_modified.py": "h2",
            "fork_deleted.py": "h3",
            "upstream_only.py": "h4",
        },
        head={
            "unchanged.py": "h1",
            "fork_modified.py": "h2-fork",
            "upstream_only.py": "h4",
            "fork_only.py": "h5",
        },
        up={
            "unchanged.py": "h1",
            "fork_modified.py": "h2-up",
            "fork_deleted.py": "h3",
            "upstream_only.py": "h4-up",
            "upstream_added.py": "h6",
        },
    )

    out = compute_fork_divergence_map("base", "head", "upstream", git_tool)

    assert out["unchanged.py"] == ForkDivergence.UNCHANGED
    assert out["fork_modified.py"] == ForkDivergence.FORK_MODIFIED
    assert out["fork_deleted.py"] == ForkDivergence.FORK_DELETED
    assert out["fork_only.py"] == ForkDivergence.FORK_ONLY
    assert out["upstream_only.py"] == ForkDivergence.UPSTREAM_ONLY_CHANGE
    assert out["upstream_added.py"] == ForkDivergence.UPSTREAM_ADDED


def test_compute_fork_divergence_map_handles_empty_base():
    git_tool = _make_git_tool_with_hashes(
        base={}, head={"a.py": "h"}, up={"a.py": "h", "b.py": "h2"}
    )
    out = compute_fork_divergence_map("", "head", "upstream", git_tool)
    assert out["a.py"] == ForkDivergence.UNCHANGED
    assert out["b.py"] == ForkDivergence.UPSTREAM_ADDED


@pytest.mark.asyncio
async def test_executor_stash_helper_writes_patch_on_failure(tmp_path: Path):
    """Item 2 wiring: when execute_semantic_merge falls back, the
    helper produces a patch and surfaces the path in the rationale."""
    from src.agents.executor_agent import ExecutorAgent
    from src.models.config import AgentLLMConfig, MergeConfig
    from src.models.state import MergeState

    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        repo_path=str(tmp_path),
    )
    state = MergeState(config=cfg)
    state.merge_base_commit = "base-sha"

    git_tool = MagicMock()
    git_tool.repo.git.diff.return_value = (
        "--- a/foo.ts\n+++ b/foo.ts\n@@ -1 +1 @@\n-old\n+new\n"
    )

    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
    )
    agent = ExecutorAgent(llm_cfg, git_tool=git_tool)

    note = agent._stash_upstream_diff_for_escalation("src/foo.ts", state)
    assert note is not None
    assert "upstream delta stashed at" in note
    assert "src__foo.ts.patch" in note


def test_executor_stash_helper_returns_none_without_git_tool():
    from src.agents.executor_agent import ExecutorAgent
    from src.models.config import AgentLLMConfig, MergeConfig
    from src.models.state import MergeState

    state = MergeState(
        config=MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    )
    state.merge_base_commit = "base"
    llm_cfg = AgentLLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
    )
    agent = ExecutorAgent(llm_cfg, git_tool=None)

    assert agent._stash_upstream_diff_for_escalation("src/foo.ts", state) is None
