"""Plan-stage stash for ``take_current_with_diff_note`` policy.

Verifies that when forks-profile routes a file via
``take_current_with_diff_note``, the upstream-side delta is captured to a
patch file under ``<run>/upstream_diff_stashes/`` and the patch path is
recorded in the decision rationale.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.core.phases.base import PhaseContext
from src.core.phases.initialize import InitializePhase
from src.models.config import (
    FileClassifierConfig,
    MergeConfig,
    OutputConfig,
)
from src.models.diff import FileChangeCategory
from src.models.state import MergeState


def _write_profile(repo_root: Path, body: str) -> None:
    merge_dir = repo_root / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "forks-profile.yaml").write_text(body, encoding="utf-8")


def _make_config(tmp_path: Path) -> MergeConfig:
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        file_classifier=FileClassifierConfig(),
    )


def _make_ctx(config: MergeConfig, git_tool) -> PhaseContext:
    from src.core.state_machine import StateMachine
    from src.memory.store import MemoryStore
    from src.memory.summarizer import PhaseSummarizer

    return PhaseContext(
        config=config,
        git_tool=git_tool,
        gate_runner=MagicMock(),
        state_machine=StateMachine(),
        checkpoint=MagicMock(),
        memory_store=MemoryStore(),
        summarizer=PhaseSummarizer(),
        trace_logger=None,
        emit=None,
        agents={},
    )


def _make_git_tool_with_diff(diff_text: str) -> MagicMock:
    git = MagicMock()
    git.repo.git.diff.return_value = diff_text
    return git


class TestForksProfileStash:
    def test_take_current_with_diff_note_writes_patch_file(
        self, tmp_path: Path
    ) -> None:
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "pkg/registry.json"\n'
                "    policy: take_current_with_diff_note\n"
                '    note: "fork canonical"\n'
            ),
        )
        config = _make_config(tmp_path)
        diff = (
            "diff --git a/pkg/registry.json b/pkg/registry.json\n"
            "--- a/pkg/registry.json\n"
            "+++ b/pkg/registry.json\n"
            "@@ -1 +1 @@\n"
            '-{"v":1}\n'
            '+{"v":2}\n'
        )
        git = _make_git_tool_with_diff(diff)
        ctx = _make_ctx(config, git_tool=git)
        state = MergeState(config=config)
        state.merge_base_commit = "abc1234"

        phase = InitializePhase()
        consumed = phase._apply_forks_profile_routing(
            state, ctx, {"pkg/registry.json": FileChangeCategory.C}
        )
        assert consumed == {"pkg/registry.json"}

        git.repo.git.diff.assert_called_once_with(
            "abc1234..upstream/main", "--", "pkg/registry.json"
        )

        candidates = [
            tmp_path / "outputs" / "debug" / "upstream_diff_stashes",
            tmp_path / ".merge" / "runs" / state.run_id / "upstream_diff_stashes",
        ]
        existing = [d for d in candidates if d.exists()]
        assert existing, f"no stash dir under {candidates}"
        stash_dir = existing[0]
        patches = list(stash_dir.glob("*.patch"))
        assert len(patches) == 1
        body = patches[0].read_text(encoding="utf-8")
        assert "pkg/registry.json" in body
        assert '+{"v":2}' in body

        rec = state.file_decision_records["pkg/registry.json"]
        assert "upstream delta stashed at" in rec.rationale
        assert str(patches[0]) in rec.rationale
        assert "git apply --3way" in rec.rationale

    def test_take_current_with_diff_note_no_merge_base_skips_stash(
        self, tmp_path: Path
    ) -> None:
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "pkg/x.json"\n'
                "    policy: take_current_with_diff_note\n"
            ),
        )
        config = _make_config(tmp_path)
        git = _make_git_tool_with_diff("ignored")
        ctx = _make_ctx(config, git_tool=git)
        state = MergeState(config=config)
        state.merge_base_commit = ""

        phase = InitializePhase()
        consumed = phase._apply_forks_profile_routing(
            state, ctx, {"pkg/x.json": FileChangeCategory.C}
        )
        assert consumed == {"pkg/x.json"}
        git.repo.git.diff.assert_not_called()
        rec = state.file_decision_records["pkg/x.json"]
        assert "stashed" not in rec.rationale
        assert "take_current_with_diff_note" in rec.rationale

    def test_empty_diff_produces_no_patch_and_no_stash_note(
        self, tmp_path: Path
    ) -> None:
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "pkg/y.json"\n'
                "    policy: take_current_with_diff_note\n"
            ),
        )
        config = _make_config(tmp_path)
        git = _make_git_tool_with_diff("")
        ctx = _make_ctx(config, git_tool=git)
        state = MergeState(config=config)
        state.merge_base_commit = "abc1234"

        phase = InitializePhase()
        phase._apply_forks_profile_routing(
            state, ctx, {"pkg/y.json": FileChangeCategory.C}
        )
        rec = state.file_decision_records["pkg/y.json"]
        assert "stashed" not in rec.rationale

    def test_escalate_human_policy_does_not_stash(self, tmp_path: Path) -> None:
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "svc/auth/**"\n'
                "    policy: escalate_human\n"
            ),
        )
        config = _make_config(tmp_path)
        git = _make_git_tool_with_diff("ignored")
        ctx = _make_ctx(config, git_tool=git)
        state = MergeState(config=config)
        state.merge_base_commit = "abc1234"

        phase = InitializePhase()
        phase._apply_forks_profile_routing(
            state, ctx, {"svc/auth/login.py": FileChangeCategory.C}
        )
        git.repo.git.diff.assert_not_called()
