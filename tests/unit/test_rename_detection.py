"""Unit tests for 7.3: rename detection via git diff -M.

Covers:
- GitTool.detect_renames output parsing
- MergeState.rename_pairs field
- build_classification_prompt rename injection
- InitializePhase storing rename_pairs on state
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.llm.prompts.planner_prompts import build_classification_prompt
from src.models.config import MergeConfig, OutputConfig
from src.models.state import MergeState
from src.tools.git_tool import GitTool


# ---------------------------------------------------------------------------
# GitTool.detect_renames
# ---------------------------------------------------------------------------


class TestDetectRenames:
    def _make_git_tool(self, diff_output: str) -> GitTool:
        tool = object.__new__(GitTool)
        mock_repo = MagicMock()
        mock_repo.git.diff.return_value = diff_output
        tool.repo = mock_repo
        tool.repo_path = MagicMock()
        return tool

    def test_empty_output_returns_empty_list(self):
        tool = self._make_git_tool("")
        assert tool.detect_renames("base", "head") == []

    def test_parses_single_rename(self):
        output = "R100\told/path/foo.go\tnew/path/foo.go"
        tool = self._make_git_tool(output)
        result = tool.detect_renames("base", "head")
        assert result == [("old/path/foo.go", "new/path/foo.go")]

    def test_parses_multiple_renames(self):
        output = (
            "R100\tinternal/core/local_runtime/foo.go\tinternal/core/plugin_manager/local_runtime/foo.go\n"
            "R85\tinternal/core/bar.go\tinternal/core/plugin_manager/bar.go\n"
            "M\tsome/unchanged.go\n"
            "A\tnew_file.go"
        )
        tool = self._make_git_tool(output)
        result = tool.detect_renames("base", "head")
        assert len(result) == 2
        assert (
            "internal/core/local_runtime/foo.go",
            "internal/core/plugin_manager/local_runtime/foo.go",
        ) in result
        assert ("internal/core/bar.go", "internal/core/plugin_manager/bar.go") in result

    def test_ignores_non_rename_lines(self):
        output = "M\tmodified.go\nA\tadded.go\nD\tdeleted.go"
        tool = self._make_git_tool(output)
        assert tool.detect_renames("base", "head") == []

    def test_git_error_returns_empty_list(self):
        import git as _git

        tool = object.__new__(GitTool)
        mock_repo = MagicMock()
        mock_repo.git.diff.side_effect = _git.GitCommandError("diff", 128)
        tool.repo = mock_repo
        tool.repo_path = MagicMock()
        assert tool.detect_renames("base", "head") == []

    def test_malformed_line_skipped(self):
        output = "R100\tonly_one_field\nR90\told.go\tnew.go"
        tool = self._make_git_tool(output)
        result = tool.detect_renames("base", "head")
        assert result == [("old.go", "new.go")]


# ---------------------------------------------------------------------------
# MergeState.rename_pairs
# ---------------------------------------------------------------------------


class TestMergeStateRenamePairs:
    def _make_config(self, tmp_path) -> MergeConfig:
        return MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            repo_path=str(tmp_path),
            output=OutputConfig(directory=str(tmp_path / "out")),
        )

    def test_defaults_to_empty_list(self, tmp_path):
        state = MergeState(config=self._make_config(tmp_path))
        assert state.rename_pairs == []

    def test_accepts_list_of_tuples(self, tmp_path):
        state = MergeState(
            config=self._make_config(tmp_path),
            rename_pairs=[("old/a.go", "new/a.go"), ("old/b.go", "new/b.go")],
        )
        assert len(state.rename_pairs) == 2
        assert state.rename_pairs[0] == ("old/a.go", "new/a.go")

    def test_serializes_through_pydantic(self, tmp_path):
        import json

        state = MergeState(
            config=self._make_config(tmp_path),
            rename_pairs=[("src/old.go", "src/new.go")],
        )
        payload = json.loads(state.model_dump_json())
        assert payload["rename_pairs"] == [["src/old.go", "src/new.go"]]


# ---------------------------------------------------------------------------
# build_classification_prompt rename injection
# ---------------------------------------------------------------------------


class TestBuildClassificationPromptRenameInjection:
    def test_no_rename_pairs_no_section(self):
        prompt = build_classification_prompt([], "ctx", 0, 1, rename_pairs=None)
        assert "Detected File Renames" not in prompt

    def test_empty_rename_pairs_no_section(self):
        prompt = build_classification_prompt([], "ctx", 0, 1, rename_pairs=[])
        assert "Detected File Renames" not in prompt

    def test_rename_pairs_injected_into_prompt(self):
        pairs = [("old/foo.go", "new/foo.go"), ("old/bar.go", "new/bar.go")]
        prompt = build_classification_prompt([], "ctx", 0, 1, rename_pairs=pairs)
        assert "Detected File Renames" in prompt
        assert "old/foo.go → new/foo.go" in prompt
        assert "old/bar.go → new/bar.go" in prompt
        assert "treat them as related" in prompt


# ---------------------------------------------------------------------------
# InitializePhase stores rename_pairs on state
# ---------------------------------------------------------------------------


class TestInitializePhaseStoresRenamePairs:
    def test_rename_pairs_written_to_state(self, tmp_path):
        from src.core.phases.initialize import InitializePhase
        from src.core.phases.base import PhaseContext
        from src.core.state_machine import StateMachine
        from src.core.message_bus import MessageBus
        from src.memory.store import MemoryStore
        from src.memory.summarizer import PhaseSummarizer

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            repo_path=str(tmp_path),
            output=OutputConfig(directory=str(tmp_path / "out")),
        )
        state = MergeState(config=config)

        mock_git = MagicMock()
        mock_git.get_merge_base.return_value = "abc123"
        mock_git.get_changed_files.return_value = []
        mock_git.detect_renames.side_effect = [
            [("old/foo.go", "new/foo.go")],
            [],
        ]

        ctx = PhaseContext(
            config=config,
            git_tool=mock_git,
            gate_runner=MagicMock(),
            state_machine=StateMachine(),
            message_bus=MessageBus(),
            checkpoint=MagicMock(),
            memory_store=MemoryStore(),
            summarizer=PhaseSummarizer(),
            trace_logger=None,
            emit=None,
            agents={},
        )

        phase = InitializePhase()
        with (
            patch("src.core.phases.initialize.classify_all_files", return_value={}),
            patch(
                "src.core.phases.initialize.category_summary",
                return_value={
                    "unchanged": 0,
                    "upstream_new": 0,
                    "current_only": 0,
                    "both_changed": 0,
                    "upstream_only": 0,
                    "current_only_change": 0,
                },
            ),
            patch("src.core.phases.initialize.PollutionAuditor"),
            patch(
                "src.core.phases.initialize.ConfigDriftDetector",
                return_value=MagicMock(
                    find_env_files=MagicMock(return_value=([], [])),
                    detect_config_drift=MagicMock(return_value=MagicMock(drifts=[])),
                ),
            ),
            patch("src.core.phases.initialize.InterfaceChangeExtractor"),
            patch("src.core.phases.initialize.ReverseImpactScanner"),
            patch("src.core.phases.initialize.SyncPointDetector"),
            patch(
                "src.core.phases.initialize.CommitReplayer",
                return_value=MagicMock(
                    classify_commits_with_partial=MagicMock(return_value=([], [], []))
                ),
            ),
            patch("src.core.state_machine.StateMachine.transition"),
        ):
            phase._run_sync(state, ctx)

        assert state.rename_pairs == [("old/foo.go", "new/foo.go")]


# ---------------------------------------------------------------------------
# P1-5: same-namespace guards on top of git's similarity-based detection
# ---------------------------------------------------------------------------


class TestApplyRenameGuards:
    def _import(self):
        from src.core.phases.initialize import _apply_rename_guards
        from src.models.config import RenameDetectionConfig

        return _apply_rename_guards, RenameDetectionConfig

    def test_default_off_passes_through_unchanged(self):
        apply, RDC = self._import()
        pairs = [
            ("vendor_a/x.py", "vendor_b/y.py"),
            ("vendor_a/x.py", "vendor_a/y.py"),
        ]
        kept, dropped = apply(pairs, RDC())
        assert kept == pairs
        assert dropped == 0

    def test_require_same_parent_dir_drops_cross_namespace(self):
        apply, RDC = self._import()
        pairs = [
            ("models/novita/llm/foo.yaml", "models/aihubmix/llm/bar.yaml"),
            ("models/x/llm/a.yaml", "models/x/llm/b.yaml"),
        ]
        kept, dropped = apply(pairs, RDC(require_same_parent_dir=True))
        assert kept == [("models/x/llm/a.yaml", "models/x/llm/b.yaml")]
        assert dropped == 1

    def test_require_same_prefix_segments_two(self):
        apply, RDC = self._import()
        pairs = [
            ("models/novita/x.yaml", "models/aihubmix/x.yaml"),
            ("models/x/llm/a.yaml", "models/x/llm/b.yaml"),
            ("tools/foo/a.py", "tools/foo/b.py"),
        ]
        kept, dropped = apply(pairs, RDC(require_same_prefix_segments=2))
        assert kept == [
            ("models/x/llm/a.yaml", "models/x/llm/b.yaml"),
            ("tools/foo/a.py", "tools/foo/b.py"),
        ]
        assert dropped == 1

    def test_require_same_prefix_segments_drops_short_paths(self):
        """A path shorter than N segments cannot satisfy a prefix-of-N
        guard — drop, don't false-accept."""
        apply, RDC = self._import()
        pairs = [
            ("a/b/c.py", "a/b/d.py"),
            ("a.py", "a/b/c.py"),
        ]
        kept, dropped = apply(pairs, RDC(require_same_prefix_segments=2))
        assert ("a/b/c.py", "a/b/d.py") in kept
        assert ("a.py", "a/b/c.py") not in kept
        assert dropped == 1

    def test_both_guards_combine(self):
        apply, RDC = self._import()
        pairs = [
            ("models/x/a.py", "models/x/b.py"),
            ("models/x/a.py", "models/y/a.py"),
        ]
        kept, dropped = apply(
            pairs,
            RDC(require_same_parent_dir=True, require_same_prefix_segments=2),
        )
        assert kept == [("models/x/a.py", "models/x/b.py")]
        assert dropped == 1
