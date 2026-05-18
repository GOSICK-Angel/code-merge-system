"""U-P4.4 / U-P4.5 + plan §2 Phase 4 列名第 1 用例 — U7 worktree default regression net.

Complements ``test_working_branch.py`` (U-P4.1/4.2/4.3 cover the field
default itself). Here we cover:

* ``test_worktree_enabled_by_default_in_new_state`` — plan §2 Phase 4
  named test #1: a fresh ``MergeState(config=MergeConfig())`` exposes
  ``config.enable_working_branch is True`` end-to-end via the state.
* ``test_orchestrator_creates_branch_on_run_when_enabled`` (U-P4.4) —
  the orchestrator's init block (``orchestrator.py:240-247``) actually
  fires when the new default is in effect, no explicit flag in config.
* ``test_existing_yaml_explicit_false_still_respected`` — a user yaml
  pinning ``enable_working_branch: False`` still wins over the new
  default; orchestrator skips the branch creation.
* ``test_setup_wizard_defaults_worktree_checkbox_on`` (U-P4.5) — the
  ``.merge/config.yaml`` synth path defaults the field to True and
  exposes the user-facing hint string.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from src.cli.commands.setup import (
    ENABLE_WORKING_BRANCH_HINT,
    _default_config_data,
)
from src.models.config import MergeConfig
from src.models.state import MergeState


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in [
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


def test_worktree_enabled_by_default_in_new_state(tmp_path: Path):
    """plan §2 Phase 4 列名 #1: default propagates end-to-end through MergeState."""
    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="main")
    state = MergeState(config=cfg)
    assert state.config.enable_working_branch is True
    # ``active_branch`` stays None until orchestrator init phase populates it.
    assert state.active_branch is None


async def test_orchestrator_creates_branch_on_run_when_enabled(tmp_path: Path):
    """U-P4.4: orchestrator init block actually fires under the new default.

    No explicit ``enable_working_branch=True`` — the new schema default
    drives the branch creation. We capture ``GitTool.create_working_branch``
    and assert it was called with the configured template + base ref.
    """
    repo = _make_repo(tmp_path)
    # NB: no enable_working_branch arg → relies on the new default.
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="main",
        repo_path=str(repo),
        working_branch="merge/auto-{timestamp}",
    )
    assert cfg.enable_working_branch is True

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
    # branch name follows the documented template
    assert result.active_branch.startswith("merge/auto-")


async def test_existing_yaml_explicit_false_still_respected(tmp_path: Path):
    """plan §2 Phase 4 列名 #3: explicit False (e.g. from existing
    .merge/config.yaml) wins over the new default."""
    repo = _make_repo(tmp_path)
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="main",
        repo_path=str(repo),
        working_branch="merge/auto-{timestamp}",
        enable_working_branch=False,  # explicitly pinned to legacy behavior
    )
    assert cfg.enable_working_branch is False

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
        result = await orch.run(state)

    mock_create.assert_not_called()
    assert result.active_branch is None


class TestSetupWizardDefault:
    """U-P4.5: the wizard's yaml-synth path bakes in the new default
    + exposes the user-facing rationale string."""

    def _payload(self):
        from src.models.setup import ProviderConfig, SetupPayload

        return SetupPayload(
            target_branch="upstream/main",
            fork_ref="main",
            project_context="ctx",
            anthropic=ProviderConfig(
                enabled=True,
                key_supplied=True,
                models=["claude-opus-4-6"],
            ),
            default_provider="anthropic",
        )

    def test_default_config_enables_worktree(self, tmp_path):
        data = _default_config_data(self._payload(), str(tmp_path))
        # (a) wizard "checkbox" defaults to True (yaml field flipped on).
        assert data["enable_working_branch"] is True
        # (c) field name unchanged (matches MergeConfig key, not renamed).
        assert "enable_working_branch" in data

    def test_hint_string_carries_user_facing_rationale(self):
        # (b) description / hint string contains the user-facing keyword
        # locked by lock #3 / doc §5.7.2.
        assert "推荐" in ENABLE_WORKING_BRANCH_HINT
        assert "fork_ref" in ENABLE_WORKING_BRANCH_HINT
