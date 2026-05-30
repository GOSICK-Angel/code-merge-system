"""P3 (Wave 4): the build_check dependency made visible + optionally enforced.

The always-on syntax gate is balance-only for compiled languages — real
compile-level correctness depends on an operator-configured build_check / gate.
P3 surfaces that dependency: (a) a report-time ``no_compile_gate`` advisory into
``state.errors`` (→ partial_failure) when compiled-language files were
auto-merged with no gate, and (b) an opt-in soft gate that routes such a merge to
AWAITING_HUMAN instead of a silent green COMPLETED.
"""

from __future__ import annotations

from datetime import datetime

from src.models.config import (
    BuildCheckConfig,
    GateCommandConfig,
    GateConfig,
    MergeConfig,
)
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.state import MergeState
from src.tools.compile_gate import (
    auto_merged_compiled_paths_without_gate,
    has_compile_gate,
)


def _state(**config_overrides) -> MergeState:
    config = MergeConfig(
        upstream_ref="upstream/main", fork_ref="feature/fork", **config_overrides
    )
    return MergeState(config=config)


def _decided(
    state: MergeState,
    file_path: str,
    decision: MergeDecision = MergeDecision.SEMANTIC_MERGE,
    source: DecisionSource = DecisionSource.AUTO_EXECUTOR,
) -> None:
    state.file_decision_records[file_path] = FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=decision,
        decision_source=source,
        confidence=0.9,
        rationale="t",
        timestamp=datetime.now(),
    )


# --------------------------------------------------------------------------- #
# has_compile_gate predicate
# --------------------------------------------------------------------------- #
class TestHasCompileGate:
    def test_default_has_none(self) -> None:
        assert has_compile_gate(_state().config) is False

    def test_build_check_counts(self) -> None:
        cfg = _state(build_check=BuildCheckConfig(enabled=True, command="tsc")).config
        assert has_compile_gate(cfg) is True

    def test_gate_command_counts(self) -> None:
        cfg = _state(
            gate=GateConfig(
                enabled=True,
                commands=[GateCommandConfig(name="tc", command="tsc --noEmit")],
            )
        ).config
        assert has_compile_gate(cfg) is True


# --------------------------------------------------------------------------- #
# auto_merged_compiled_paths_without_gate
# --------------------------------------------------------------------------- #
class TestAtRiskSet:
    def test_compiled_auto_merged_no_gate_flagged(self) -> None:
        state = _state()
        _decided(state, "src/a.ts")
        _decided(state, "src/b.py")  # python: has a real checker, not balance-only
        assert auto_merged_compiled_paths_without_gate(state) == ["src/a.ts"]

    def test_suppressed_when_gate_configured(self) -> None:
        state = _state(build_check=BuildCheckConfig(enabled=True, command="tsc"))
        _decided(state, "src/a.ts")
        assert auto_merged_compiled_paths_without_gate(state) == []

    def test_human_decided_not_at_risk(self) -> None:
        state = _state()
        _decided(state, "src/a.ts", source=DecisionSource.HUMAN)
        assert auto_merged_compiled_paths_without_gate(state) == []

    def test_escalated_not_at_risk(self) -> None:
        state = _state()
        _decided(state, "src/a.ts", decision=MergeDecision.ESCALATE_HUMAN)
        assert auto_merged_compiled_paths_without_gate(state) == []


# --------------------------------------------------------------------------- #
# P3(a) report-time advisory
# --------------------------------------------------------------------------- #
class TestReportAdvisory:
    def _ctx(self):
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.notify = MagicMock()
        return ctx

    def test_advisory_recorded_when_no_gate(self) -> None:
        from src.core.phases.report_generation import _check_compile_gate_advisory

        state = _state()
        _decided(state, "src/a.ts")
        _check_compile_gate_advisory(state, self._ctx())
        msgs = [e["message"] for e in state.errors]
        assert any("no_compile_gate" in m for m in msgs)

    def test_no_advisory_with_gate(self) -> None:
        from src.core.phases.report_generation import _check_compile_gate_advisory

        state = _state(build_check=BuildCheckConfig(enabled=True, command="tsc"))
        _decided(state, "src/a.ts")
        _check_compile_gate_advisory(state, self._ctx())
        assert state.errors == []

    def test_dry_run_skips(self) -> None:
        from src.core.phases.report_generation import _check_compile_gate_advisory

        state = _state()
        state.dry_run = True
        _decided(state, "src/a.ts")
        _check_compile_gate_advisory(state, self._ctx())
        assert state.errors == []
