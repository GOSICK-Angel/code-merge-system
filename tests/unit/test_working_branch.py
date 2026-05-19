"""Tests for enable_working_branch / working_branch functionality (fix 7.4)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models.config import MergeConfig
from src.models.state import MergeState
from src.tools.git_tool import GitTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in [
        # `--initial-branch=main` is required on CI runners whose global
        # `init.defaultBranch` is unset (git defaults to `master` there),
        # otherwise the subsequent `git checkout main` fails with
        # "pathspec 'main' did not match any file(s) known to git".
        ["git", "init", "--initial-branch=main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)

    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def _current_branch(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_config(repo_path: str, enable: bool = False) -> MergeConfig:
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="main",
        repo_path=repo_path,
        enable_working_branch=enable,
        working_branch="merge/auto-{timestamp}",
    )


# ---------------------------------------------------------------------------
# MergeConfig / MergeState field tests
# ---------------------------------------------------------------------------


def test_enable_working_branch_defaults_true():
    """U7 / U-P4.1: ``enable_working_branch`` flipped from False to True so a
    half-finished run never pollutes ``fork_ref`` HEAD. Renamed from the
    legacy ``test_enable_working_branch_defaults_false``; assertion
    migrated ``is False → is True``."""
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="main")
    assert cfg.enable_working_branch is True


def test_enable_working_branch_can_be_set():
    """U-P4.2: explicit ``True`` still works (no-op vs. new default)."""
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="main",
        enable_working_branch=True,
    )
    assert cfg.enable_working_branch is True


def test_enable_working_branch_can_be_disabled_with_explicit_false():
    """U-P4.3: explicit ``False`` is NOT silently overridden by the new
    default — backward-compat path for users who pinned the legacy
    in-place behavior."""
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="main",
        enable_working_branch=False,
    )
    assert cfg.enable_working_branch is False
    assert isinstance(cfg.enable_working_branch, bool)


def test_active_branch_defaults_none():
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="main")
    state = MergeState(config=cfg)
    assert state.active_branch is None


def test_active_branch_model_copy_is_immutable():
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="main")
    state = MergeState(config=cfg)
    updated = state.model_copy(update={"active_branch": "merge/auto-20260101-120000"})
    assert updated.active_branch == "merge/auto-20260101-120000"
    assert state.active_branch is None


# ---------------------------------------------------------------------------
# GitTool.create_working_branch
# ---------------------------------------------------------------------------


def test_create_working_branch_no_placeholder(tmp_path: Path):
    repo = _make_repo(tmp_path)
    git = GitTool(str(repo))
    result = git.create_working_branch("merge/test-branch", "main")
    assert result == "merge/test-branch"
    assert _current_branch(repo) == "merge/test-branch"


def test_create_working_branch_expands_timestamp(tmp_path: Path):
    repo = _make_repo(tmp_path)
    git = GitTool(str(repo))
    result = git.create_working_branch("merge/auto-{timestamp}", "main")
    assert result.startswith("merge/auto-")
    assert "{timestamp}" not in result
    assert _current_branch(repo) == result


def test_create_working_branch_returns_resolved_name(tmp_path: Path):
    repo = _make_repo(tmp_path)
    git = GitTool(str(repo))
    result = git.create_working_branch("merge/static", "main")
    assert result == "merge/static"
    assert _current_branch(repo) == "merge/static"


# ---------------------------------------------------------------------------
# Orchestrator: working branch init block
# ---------------------------------------------------------------------------


async def test_orchestrator_skips_branch_when_disabled(tmp_path: Path):
    repo = _make_repo(tmp_path)
    cfg = _make_config(str(repo), enable=False)

    from src.core.orchestrator import Orchestrator

    orch = Orchestrator(cfg)
    state = MergeState(config=cfg)

    with (
        patch.object(orch, "_inject_memory"),
        patch.object(orch, "_inject_hooks"),
        patch.object(orch.git_tool, "create_working_branch") as mock_create,
        patch.object(orch.checkpoint, "register_signal_handler"),
        patch.object(orch.checkpoint, "save"),
        patch("src.core.orchestrator.PHASE_MAP", {}),
    ):
        await orch.run(state)

    mock_create.assert_not_called()


async def test_orchestrator_creates_branch_when_enabled(tmp_path: Path):
    repo = _make_repo(tmp_path)
    cfg = _make_config(str(repo), enable=True)

    from src.core.orchestrator import Orchestrator

    orch = Orchestrator(cfg)
    state = MergeState(config=cfg)

    with (
        patch.object(orch, "_inject_memory"),
        patch.object(orch, "_inject_hooks"),
        patch.object(
            orch.git_tool,
            "create_working_branch",
            return_value="merge/auto-20260101-120000",
        ) as mock_create,
        patch.object(orch.checkpoint, "register_signal_handler"),
        patch.object(orch.checkpoint, "save"),
        patch("src.core.orchestrator.PHASE_MAP", {}),
    ):
        result = await orch.run(state)

    mock_create.assert_called_once_with("merge/auto-{timestamp}", "main")
    assert result.active_branch == "merge/auto-20260101-120000"


async def test_orchestrator_active_branch_assignment_keeps_state_identity(
    tmp_path: Path,
):
    """Fix 10: orchestrator must NOT replace ``state`` with a fresh
    ``model_copy`` when injecting ``active_branch``. The Web bridge
    captures ``state`` by reference in ``__init__`` and observes
    mutations live; a model_copy leaks the WS snapshot to a frozen
    "initialized" state forever — that's the bug behind the forgejo
    run where the UI stayed on INITIALIZED for minutes while messages
    list showed actual transitions (shared list reference).

    The contract: ``id(input) == id(returned)`` AND
    ``input.active_branch`` reflects the new branch in place.
    """
    repo = _make_repo(tmp_path)
    cfg = _make_config(str(repo), enable=True)

    from src.core.orchestrator import Orchestrator

    orch = Orchestrator(cfg)
    state = MergeState(config=cfg)
    state_id = id(state)

    with (
        patch.object(orch, "_inject_memory"),
        patch.object(orch, "_inject_hooks"),
        patch.object(
            orch.git_tool,
            "create_working_branch",
            return_value="merge/auto-fix10-test",
        ),
        patch.object(orch.checkpoint, "register_signal_handler"),
        patch.object(orch.checkpoint, "save"),
        patch("src.core.orchestrator.PHASE_MAP", {}),
    ):
        result = await orch.run(state)

    # The state object passed in MUST be the one returned — bridge holds
    # this reference and observes mutations live.
    assert id(result) == state_id, (
        "orchestrator replaced state object; bridge would lose sync"
    )
    # active_branch must be set on the in-place object the bridge sees.
    assert state.active_branch == "merge/auto-fix10-test"


async def test_orchestrator_skips_branch_creation_on_resume(tmp_path: Path):
    repo = _make_repo(tmp_path)
    cfg = _make_config(str(repo), enable=True)

    from src.core.orchestrator import Orchestrator

    orch = Orchestrator(cfg)
    state = MergeState(config=cfg, active_branch="merge/auto-20260101-120000")

    with (
        patch.object(orch, "_inject_memory"),
        patch.object(orch, "_inject_hooks"),
        patch.object(orch.git_tool, "create_working_branch") as mock_create,
        patch.object(orch.checkpoint, "register_signal_handler"),
        patch.object(orch.checkpoint, "save"),
        patch("src.core.orchestrator.PHASE_MAP", {}),
    ):
        result = await orch.run(state)

    mock_create.assert_not_called()
    assert result.active_branch == "merge/auto-20260101-120000"


def test_create_working_branch_resets_dirty_index(tmp_path: Path):
    """When the git index has unresolved merge conflicts (ls-files --unmerged
    returns output), create_working_branch must reset --hard before checking out
    the base_ref so a subsequent run is not blocked by stale conflict markers."""
    import subprocess

    repo = _make_repo(tmp_path)

    # Create a second branch with a conflicting file so we can stage a conflict.
    subprocess.run(
        ["git", "checkout", "-b", "other"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("branch-other content")
    subprocess.run(
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "other"], cwd=str(repo), check=True, capture_output=True
    )

    subprocess.run(
        ["git", "checkout", "main"], cwd=str(repo), check=True, capture_output=True
    )
    (repo / "README.md").write_text("main content")
    subprocess.run(
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "main2"], cwd=str(repo), check=True, capture_output=True
    )

    # Produce a merge conflict in the index (merge will fail but leave markers staged).
    subprocess.run(["git", "merge", "other"], cwd=str(repo), capture_output=True)

    # Verify the index is dirty with unmerged files.
    result = subprocess.run(
        ["git", "ls-files", "--unmerged"], cwd=str(repo), capture_output=True, text=True
    )
    assert result.stdout.strip(), "pre-condition: index must have unmerged files"

    git_tool = GitTool(str(repo))
    branch_name = git_tool.create_working_branch("merge/clean-start", "main")

    assert branch_name == "merge/clean-start"
    assert _current_branch(repo) == "merge/clean-start"

    # After reset --hard the unmerged state must be gone.
    ls = subprocess.run(
        ["git", "ls-files", "--unmerged"], cwd=str(repo), capture_output=True, text=True
    )
    assert ls.stdout.strip() == ""
