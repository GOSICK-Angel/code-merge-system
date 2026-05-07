"""Unit tests for InitializePhase ↔ forks-profile routing.

Synthetic fixtures only.
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
from src.models.decision import DecisionSource, MergeDecision
from src.models.diff import FileChangeCategory, FileStatus
from src.models.state import MergeState


def _write_profile(repo_root: Path, body: str) -> None:
    merge_dir = repo_root / ".merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "forks-profile.yaml").write_text(body, encoding="utf-8")


def _make_config(tmp_path: Path, **fc_overrides) -> MergeConfig:
    fc_kwargs = {
        "always_take_upstream_patterns": [],
        "always_take_current_patterns": [],
    }
    fc_kwargs.update(fc_overrides)
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="feature/fork",
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


class TestForksProfileRouting:
    def test_no_profile_file_is_noop(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state, ctx, {"src/foo.py": FileChangeCategory.B}
        )
        assert consumed == set()
        assert state.forks_profile is None
        assert state.file_decision_records == {}

    def test_empty_profile_is_noop(self, tmp_path: Path):
        _write_profile(tmp_path, "version: 1\n")
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state, ctx, {"src/foo.py": FileChangeCategory.B}
        )
        assert consumed == set()
        assert state.forks_profile is not None
        assert state.forks_profile.is_empty()
        assert state.file_decision_records == {}

    def test_removed_domain_routes_d_missing_as_take_current(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            (
                "removed_domains:\n"
                "  - name: alpha\n"
                "    paths:\n"
                '      - "svc/alpha/**"\n'
                '    reason: "out of scope"\n'
                '    removed_in: "abc1234"\n'
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        file_categories = {
            "svc/alpha/login.py": FileChangeCategory.D_MISSING,
            "svc/alpha/handler.py": FileChangeCategory.B,
            "svc/alpha/util.py": FileChangeCategory.C,
            "src/unrelated.py": FileChangeCategory.B,
        }
        consumed = phase._apply_forks_profile_routing(state, ctx, file_categories)
        assert consumed == {
            "svc/alpha/login.py",
            "svc/alpha/handler.py",
            "svc/alpha/util.py",
        }
        assert "src/unrelated.py" not in state.file_decision_records

        rec = state.file_decision_records["svc/alpha/login.py"]
        assert rec.decision == MergeDecision.TAKE_CURRENT
        assert rec.file_status == FileStatus.DELETED
        assert rec.decision_source == DecisionSource.AUTO_PLANNER
        assert rec.agent == "forks_profile_routing"
        assert "alpha" in rec.rationale
        assert "out of scope" in rec.rationale

        rec_b = state.file_decision_records["svc/alpha/handler.py"]
        assert rec_b.decision == MergeDecision.TAKE_CURRENT
        assert rec_b.file_status == FileStatus.MODIFIED

    def test_rewritten_module_escalate_human_routes_b_class(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "svc/auth/**"\n'
                "    policy: escalate_human\n"
                '    note: "custom SSO"\n'
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {"svc/auth/login.py": FileChangeCategory.C},
        )
        assert consumed == {"svc/auth/login.py"}
        rec = state.file_decision_records["svc/auth/login.py"]
        assert rec.decision == MergeDecision.ESCALATE_HUMAN
        assert rec.agent == "forks_profile_routing"
        assert "escalate_human" in rec.rationale
        assert "custom SSO" in rec.rationale

    def test_rewritten_module_take_current_with_diff_note(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "pkg/registry.json"\n'
                "    policy: take_current_with_diff_note\n"
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state, ctx, {"pkg/registry.json": FileChangeCategory.C}
        )
        assert consumed == {"pkg/registry.json"}
        rec = state.file_decision_records["pkg/registry.json"]
        assert rec.decision == MergeDecision.TAKE_CURRENT
        assert "take_current_with_diff_note" in rec.rationale

    def test_rewritten_module_semantic_merge_with_alert_does_not_force(
        self, tmp_path: Path
    ):
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "svc/ai/**"\n'
                "    policy: semantic_merge_with_alert\n"
                '    note: "fault-tolerant fallback"\n'
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state, ctx, {"svc/ai/model.py": FileChangeCategory.C}
        )
        assert consumed == set()
        assert "svc/ai/model.py" not in state.file_decision_records

    def test_rewritten_takes_priority_over_removed_domain(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            (
                "removed_domains:\n"
                "  - name: alpha\n"
                "    paths:\n"
                '      - "svc/alpha/**"\n'
                "rewritten_modules:\n"
                '  - path: "svc/alpha/auth.py"\n'
                "    policy: escalate_human\n"
            ),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        consumed = phase._apply_forks_profile_routing(
            state,
            ctx,
            {
                "svc/alpha/auth.py": FileChangeCategory.C,
                "svc/alpha/util.py": FileChangeCategory.C,
            },
        )
        assert consumed == {"svc/alpha/auth.py", "svc/alpha/util.py"}
        assert (
            state.file_decision_records["svc/alpha/auth.py"].decision
            == MergeDecision.ESCALATE_HUMAN
        )
        assert (
            state.file_decision_records["svc/alpha/util.py"].decision
            == MergeDecision.TAKE_CURRENT
        )

    def test_profile_decision_skipped_by_apply_forced_decisions(self, tmp_path: Path):
        _write_profile(
            tmp_path,
            (
                "rewritten_modules:\n"
                '  - path: "svc/auth/**"\n'
                "    policy: escalate_human\n"
            ),
        )
        config = _make_config(
            tmp_path,
            always_take_upstream_patterns=["svc/auth/**"],
        )
        mock_git = MagicMock()
        mock_git.get_file_bytes.return_value = b"x\n"
        ctx = _make_ctx(config, git_tool=mock_git)
        state = MergeState(config=config)
        phase = InitializePhase()

        file_categories = {"svc/auth/login.py": FileChangeCategory.C}
        profile_consumed = phase._apply_forks_profile_routing(
            state, ctx, file_categories
        )
        assert profile_consumed == {"svc/auth/login.py"}

        forced_consumed = phase._apply_forced_decisions(state, ctx, file_categories)
        assert forced_consumed == set()
        rec = state.file_decision_records["svc/auth/login.py"]
        assert rec.decision == MergeDecision.ESCALATE_HUMAN
        assert mock_git.get_file_bytes.call_count == 0

    def test_invalid_profile_logs_and_returns_empty_no_state_write(
        self, tmp_path: Path, caplog
    ):
        _write_profile(
            tmp_path,
            ('rewritten_modules:\n  - path: "svc/auth/**"\n    policy: not_a_policy\n'),
        )
        config = _make_config(tmp_path)
        ctx = _make_ctx(config)
        state = MergeState(config=config)
        phase = InitializePhase()

        with caplog.at_level("ERROR"):
            consumed = phase._apply_forks_profile_routing(
                state, ctx, {"svc/auth/foo.py": FileChangeCategory.B}
            )
        assert consumed == set()
        assert state.file_decision_records == {}
        assert any("forks-profile" in r.message for r in caplog.records)
