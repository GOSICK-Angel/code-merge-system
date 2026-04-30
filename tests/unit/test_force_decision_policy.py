"""Unit tests for InitializePhase force-decision policy.

Verifies that paths matching always_take_upstream_patterns or
always_take_current_patterns are pre-decided before AI flow and
removed from actionable_paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.core.phases.base import PhaseContext
from src.core.phases.initialize import InitializePhase, _build_added_file_diff
from src.models.config import (
    FileClassifierConfig,
    MergeConfig,
    OutputConfig,
)
from src.models.decision import DecisionSource, MergeDecision
from src.models.diff import FileChangeCategory, FileStatus
from src.models.state import MergeState


def _make_config(tmp_path: Path, **fc_overrides) -> MergeConfig:
    fc_kwargs = {
        "always_take_upstream_patterns": [],
        "always_take_current_patterns": [],
    }
    fc_kwargs.update(fc_overrides)
    return MergeConfig(
        upstream_ref="upgrade/0.6.0",
        fork_ref="0.6.0",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        file_classifier=FileClassifierConfig(**fc_kwargs),
    )


def _make_ctx(config: MergeConfig, git_tool=None) -> PhaseContext:
    from src.core.state_machine import StateMachine
    from src.core.message_bus import MessageBus
    from src.core.phase_runner import PhaseRunner
    from src.memory.store import MemoryStore
    from src.memory.summarizer import PhaseSummarizer

    return PhaseContext(
        config=config,
        git_tool=git_tool or MagicMock(),
        gate_runner=MagicMock(),
        state_machine=StateMachine(),
        message_bus=MessageBus(),
        checkpoint=MagicMock(),
        phase_runner=PhaseRunner(),
        memory_store=MemoryStore(),
        summarizer=PhaseSummarizer(),
        trace_logger=None,
        emit=None,
        agents={},
    )


class TestForceDecisionPolicy:
    def test_always_take_upstream_writes_take_target_record(self, tmp_path):
        config = _make_config(
            tmp_path,
            always_take_upstream_patterns=["docs/**/*.md", "scripts/**"],
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = b"upstream content\n"
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()

        file_categories = {
            "docs/api/intro.md": FileChangeCategory.B,
            "scripts/helper.sh": FileChangeCategory.D_MISSING,
            "src/foo.py": FileChangeCategory.C,
        }

        consumed = phase._apply_forced_decisions(state, ctx, file_categories)

        assert consumed == {"docs/api/intro.md", "scripts/helper.sh"}
        assert "src/foo.py" not in state.file_decision_records

        rec_md = state.file_decision_records["docs/api/intro.md"]
        assert rec_md.decision == MergeDecision.TAKE_TARGET
        assert rec_md.decision_source == DecisionSource.AUTO_PLANNER
        assert rec_md.confidence == 1.0
        assert rec_md.agent == "force_decision_policy"
        assert (tmp_path / "docs/api/intro.md").read_bytes() == b"upstream content\n"

        rec_sh = state.file_decision_records["scripts/helper.sh"]
        assert rec_sh.decision == MergeDecision.TAKE_TARGET
        assert rec_sh.file_status == FileStatus.ADDED
        assert (tmp_path / "scripts/helper.sh").exists()

    def test_always_take_current_writes_take_current_record_no_io(self, tmp_path):
        config = _make_config(
            tmp_path,
            always_take_current_patterns=["internal/legacy/**"],
        )
        mock_git = MagicMock()
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()

        file_categories = {
            "internal/legacy/old.go": FileChangeCategory.B,
            "internal/legacy/serverless_only.go": FileChangeCategory.D_MISSING,
            "src/foo.py": FileChangeCategory.C,
        }

        consumed = phase._apply_forced_decisions(state, ctx, file_categories)

        assert consumed == {
            "internal/legacy/old.go",
            "internal/legacy/serverless_only.go",
        }
        assert mock_git.get_file_bytes.call_count == 0

        rec_b = state.file_decision_records["internal/legacy/old.go"]
        assert rec_b.decision == MergeDecision.TAKE_CURRENT
        assert rec_b.file_status == FileStatus.MODIFIED

        rec_d = state.file_decision_records["internal/legacy/serverless_only.go"]
        assert rec_d.decision == MergeDecision.TAKE_CURRENT
        assert rec_d.file_status == FileStatus.DELETED
        assert not (tmp_path / "internal/legacy/serverless_only.go").exists()

    def test_upstream_wins_over_current_on_overlap(self, tmp_path):
        config = _make_config(
            tmp_path,
            always_take_upstream_patterns=[".github/**"],
            always_take_current_patterns=[".github/workflows/**"],
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = b"# upstream workflow\n"
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()

        file_categories = {".github/workflows/ci.yml": FileChangeCategory.B}

        consumed = phase._apply_forced_decisions(state, ctx, file_categories)
        assert consumed == {".github/workflows/ci.yml"}

        rec = state.file_decision_records[".github/workflows/ci.yml"]
        assert rec.decision == MergeDecision.TAKE_TARGET
        assert "always_take_upstream_patterns" in rec.rationale

    def test_legacy_alias_always_take_target_patterns_still_works(self, tmp_path):
        config = _make_config(
            tmp_path,
            always_take_upstream_patterns=[],
            always_take_target_patterns=["legacy/**"],
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = b"x\n"
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()
        consumed = phase._apply_forced_decisions(
            state, ctx, {"legacy/foo.go": FileChangeCategory.B}
        )
        assert consumed == {"legacy/foo.go"}
        rec = state.file_decision_records["legacy/foo.go"]
        assert rec.decision == MergeDecision.TAKE_TARGET

    def test_no_patterns_returns_empty_set_no_io(self, tmp_path):
        config = _make_config(tmp_path)
        mock_git = MagicMock()
        ctx = _make_ctx(config, git_tool=mock_git)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forced_decisions(
            state, ctx, {"a.py": FileChangeCategory.B}
        )
        assert consumed == set()
        assert state.file_decision_records == {}
        assert mock_git.get_file_bytes.call_count == 0

    def test_force_take_target_deletes_when_upstream_absent(self, tmp_path):
        config = _make_config(
            tmp_path, always_take_upstream_patterns=["dead/**"]
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = None
        ctx = _make_ctx(config, git_tool=mock_git)

        target = tmp_path / "dead/old.go"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"to be removed")

        state = MergeState(config=config)
        phase = InitializePhase()
        phase._apply_forced_decisions(
            state, ctx, {"dead/old.go": FileChangeCategory.B}
        )

        assert not target.exists()
        rec = state.file_decision_records["dead/old.go"]
        assert "deleted" in rec.rationale

    def test_force_take_target_appends_trailing_newline_for_text(self, tmp_path):
        config = _make_config(
            tmp_path, always_take_upstream_patterns=[".github/**"]
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = b"name: ci\non: push"
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()
        phase._apply_forced_decisions(
            state, ctx, {".github/workflows/ci.yml": FileChangeCategory.B}
        )

        written = (tmp_path / ".github/workflows/ci.yml").read_bytes()
        assert written == b"name: ci\non: push\n"

    def test_force_take_target_preserves_binary_content(self, tmp_path):
        config = _make_config(
            tmp_path, always_take_upstream_patterns=["assets/**"]
        )
        binary_blob = b"\x89PNG\r\n\x1a\n\x00\x00ihdr"
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = binary_blob
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()
        phase._apply_forced_decisions(
            state, ctx, {"assets/logo.png": FileChangeCategory.D_MISSING}
        )

        assert (tmp_path / "assets/logo.png").read_bytes() == binary_blob

    def test_force_take_target_does_not_double_newline(self, tmp_path):
        config = _make_config(
            tmp_path, always_take_upstream_patterns=["docs/**"]
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = b"already terminated\n"
        ctx = _make_ctx(config, git_tool=mock_git)

        state = MergeState(config=config)
        phase = InitializePhase()
        phase._apply_forced_decisions(
            state, ctx, {"docs/readme.md": FileChangeCategory.B}
        )

        assert (tmp_path / "docs/readme.md").read_bytes() == b"already terminated\n"


class TestBuildAddedFileDiff:
    def _make_git(self, content: str | None) -> MagicMock:
        m = MagicMock()
        m.get_file_content.return_value = content
        return m

    def test_returns_empty_when_file_absent(self):
        git = self._make_git(None)
        result = _build_added_file_diff(git, "upstream/main", "new/file.go")
        assert result == ""

    def test_returns_empty_when_content_empty(self):
        git = self._make_git("")
        result = _build_added_file_diff(git, "upstream/main", "new/file.go")
        assert result == ""

    def test_all_lines_shown_when_under_limit(self):
        content = "\n".join(f"line {i}" for i in range(10))
        git = self._make_git(content)
        result = _build_added_file_diff(git, "upstream/main", "pkg/foo.go")
        assert result.startswith("--- /dev/null")
        assert "+++ b/pkg/foo.go" in result
        assert "+line 0" in result
        assert "+line 9" in result
        assert "more lines not shown" not in result

    def test_truncation_marker_shown_over_limit(self):
        content = "\n".join(f"line {i}" for i in range(300))
        git = self._make_git(content)
        result = _build_added_file_diff(git, "upstream/main", "big.go")
        lines = result.splitlines()
        plus_lines = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
        assert len(plus_lines) == 200
        assert "more lines not shown" in result

    def test_hunk_header_shows_preview_count(self):
        content = "\n".join(f"x" for _ in range(5))
        git = self._make_git(content)
        result = _build_added_file_diff(git, "ref", "a.py")
        assert "@@ -0,0 +1,5 @@" in result
