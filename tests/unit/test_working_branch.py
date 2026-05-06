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
        ["git", "init"],
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


def test_enable_working_branch_defaults_false():
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="main")
    assert cfg.enable_working_branch is False


def test_enable_working_branch_can_be_set():
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="main",
        enable_working_branch=True,
    )
    assert cfg.enable_working_branch is True


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
