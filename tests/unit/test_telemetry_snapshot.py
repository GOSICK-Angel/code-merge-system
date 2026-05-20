"""Verify Orchestrator._snapshot_telemetry persists cost and memory
summaries onto MergeState before checkpoint.

Regression target: previously, runs that halted at AWAITING_HUMAN exited
before report_generation and lost all token/cost telemetry. The
checkpoint showed zero LLM activity even when planner + planner_judge
made multiple calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import git as _git

from src.core.orchestrator import Orchestrator
from src.models.config import MergeConfig, OutputConfig
from src.models.state import MergeState, SystemStatus
from src.tools.cost_tracker import CostTracker, TokenUsage


def _make_config(tmp_path) -> MergeConfig:
    # Orchestrator instantiates GitTool which requires a real repo.
    if not (tmp_path / ".git").exists():
        _git.Repo.init(str(tmp_path))
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
    )


class TestTelemetrySnapshot:
    def test_state_has_cost_summary_field_defaulting_to_none(self, tmp_path):
        state = MergeState(config=_make_config(tmp_path))
        assert state.cost_summary is None
        assert state.memory_summary is None

    def test_snapshot_telemetry_populates_cost_summary(self, tmp_path):
        config = _make_config(tmp_path)
        orch = Orchestrator(config, agents={})

        orch._cost_tracker = CostTracker()
        orch._cost_tracker.record(
            agent="planner",
            phase="planning",
            model="claude-opus-4-6",
            provider="anthropic",
            usage=TokenUsage(input_tokens=5000, output_tokens=1200),
            elapsed_seconds=3.2,
        )

        state = MergeState(config=config)
        orch._snapshot_telemetry(state)

        assert state.cost_summary is not None
        assert state.cost_summary["total_calls"] == 1
        assert state.cost_summary["total_tokens"]["input"] == 5000
        assert state.cost_summary["total_tokens"]["output"] == 1200
        assert "planner" in state.cost_summary["by_agent"]
        assert state.cost_summary["by_agent"]["planner"]["calls"] == 1

    def test_snapshot_telemetry_populates_memory_summary(self, tmp_path):
        config = _make_config(tmp_path)
        orch = Orchestrator(config, agents={})

        orch._memory_hit_tracker.record_call(
            "planning",
            {"l0": 3, "l1_patterns": 2, "l1_decisions": 0, "l2": 4},
        )

        state = MergeState(config=config)
        orch._snapshot_telemetry(state)

        assert state.memory_summary is not None
        assert state.memory_summary["total_calls"] == 1
        assert "by_phase" in state.memory_summary
        assert "by_layer" in state.memory_summary

    def test_snapshot_telemetry_swallows_tracker_errors(self, tmp_path):
        config = _make_config(tmp_path)
        orch = Orchestrator(config, agents={})

        broken = MagicMock()
        broken.summary.side_effect = RuntimeError("boom")
        orch._cost_tracker = broken

        broken_mem = MagicMock()
        broken_mem.summary.side_effect = RuntimeError("boom")
        orch._memory_hit_tracker = broken_mem

        state = MergeState(config=config)
        orch._snapshot_telemetry(state)
        assert state.cost_summary is None
        assert state.memory_summary is None

    def test_cost_summary_serializes_through_pydantic(self, tmp_path):
        """Round-trip via model_dump_json so we know the field will land
        in checkpoint.json exactly as expected."""
        import json as json_lib

        config = _make_config(tmp_path)
        orch = Orchestrator(config, agents={})
        orch._cost_tracker.record(
            agent="planner_judge",
            phase="plan_review",
            model="gpt-5.4",
            provider="openai",
            usage=TokenUsage(input_tokens=20000, output_tokens=400),
            elapsed_seconds=8.0,
        )

        state = MergeState(config=config)
        orch._snapshot_telemetry(state)

        payload = json_lib.loads(state.model_dump_json())
        assert payload["cost_summary"]["total_calls"] == 1
        assert payload["cost_summary"]["by_model"]["gpt-5.4"]["calls"] == 1


class TestCostCeiling:
    """Verify max_cost_usd halts the orchestrator when threshold exceeded."""

    def test_max_cost_usd_defaults_to_five_dollars(self, tmp_path):
        config = _make_config(tmp_path)
        assert config.max_cost_usd == 5.0
        assert isinstance(config.max_cost_usd, float)

    def test_max_cost_usd_can_be_disabled_with_none(self, tmp_path):
        """Explicit None still legal — backwards-compatible disable path
        (plan §3.1 Q1; orchestrator ceiling check short-circuits on None)."""
        if not (tmp_path / ".git").exists():
            _git.Repo.init(str(tmp_path))
        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            repo_path=str(tmp_path),
            output=OutputConfig(directory=str(tmp_path / "outputs")),
            max_cost_usd=None,
        )
        assert config.max_cost_usd is None

    async def test_orchestrator_halts_when_cost_ceiling_exceeded(self, tmp_path):
        from src.models.config import MergeConfig, OutputConfig

        if not (tmp_path / ".git").exists():
            _git.Repo.init(str(tmp_path))

        config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            repo_path=str(tmp_path),
            output=OutputConfig(directory=str(tmp_path / "outputs")),
            max_cost_usd=1.0,
        )

        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents={})

        orch._cost_tracker.record(
            agent="planner",
            phase="planning",
            model="claude-opus-4-6",
            provider="anthropic",
            usage=TokenUsage(input_tokens=1_000_000, output_tokens=500_000),
            elapsed_seconds=5.0,
        )

        state = MergeState(config=config)
        state.status = SystemStatus.AUTO_MERGING

        with (
            patch.object(orch, "_finalize_log"),
            patch.object(orch, "_snapshot_telemetry"),
            patch.object(orch.checkpoint, "save"),
        ):
            result = await orch.run(state)

        assert result.status == SystemStatus.AWAITING_HUMAN


class TestSuspendedPhaseStatus:
    """A phase that suspends to AWAITING_HUMAN must read as ``awaiting``, not
    ``running`` — otherwise the pipeline shows several phases RUNNING at once.
    """

    def _state_with_running(self, tmp_path, phase):
        from src.models.state import PhaseResult

        config = _make_config(tmp_path)
        state = MergeState(config=config)
        state.current_phase = phase
        state.phase_results[phase.value] = PhaseResult(phase=phase, status="running")
        return state

    def test_running_phase_flips_to_awaiting_on_suspend(self, tmp_path):
        from src.core.orchestrator import _mark_suspended_phase
        from src.models.plan import MergePhase

        state = self._state_with_running(tmp_path, MergePhase.AUTO_MERGE)
        _mark_suspended_phase(state, SystemStatus.AWAITING_HUMAN)
        assert state.phase_results["auto_merge"].status == "awaiting"
        assert state.phase_results["auto_merge"].completed_at is not None

    def test_non_awaiting_target_leaves_running(self, tmp_path):
        from src.core.orchestrator import _mark_suspended_phase
        from src.models.plan import MergePhase

        state = self._state_with_running(tmp_path, MergePhase.AUTO_MERGE)
        _mark_suspended_phase(state, SystemStatus.AUTO_MERGING)
        assert state.phase_results["auto_merge"].status == "running"

    def test_completed_phase_not_downgraded(self, tmp_path):
        from src.core.orchestrator import _mark_suspended_phase
        from src.models.plan import MergePhase
        from src.models.state import PhaseResult

        config = _make_config(tmp_path)
        state = MergeState(config=config)
        state.current_phase = MergePhase.JUDGE_REVIEW
        state.phase_results["judge_review"] = PhaseResult(
            phase=MergePhase.JUDGE_REVIEW, status="completed"
        )
        _mark_suspended_phase(state, SystemStatus.AWAITING_HUMAN)
        assert state.phase_results["judge_review"].status == "completed"


class TestDryRun:
    """Verify that dry_run=True stops the orchestrator before AUTO_MERGING."""

    def test_dry_run_field_defaults_false(self, tmp_path):
        config = _make_config(tmp_path)
        state = MergeState(config=config)
        assert state.dry_run is False

    def test_dry_run_field_set_true(self, tmp_path):
        config = _make_config(tmp_path)
        state = MergeState(config=config, dry_run=True)
        assert state.dry_run is True

    async def test_orchestrator_halts_at_awaiting_human_when_dry_run(self, tmp_path):
        """When state.dry_run=True and next status is AUTO_MERGING, the
        orchestrator must redirect to AWAITING_HUMAN without starting any
        executor or judge phases."""
        config = _make_config(tmp_path)

        with patch("src.core.orchestrator.GitTool") as MockGit:
            mock_git = MockGit.return_value
            mock_git.get_merge_base.return_value = "abc123"
            mock_git.get_changed_files.return_value = []

            orch = Orchestrator(config, agents={})

            state = MergeState(config=config, dry_run=True)
            state.status = SystemStatus.AUTO_MERGING

            with (
                patch.object(orch, "_finalize_log"),
                patch.object(orch, "_snapshot_telemetry"),
                patch.object(orch.checkpoint, "save"),
            ):
                result = await orch.run(state)

        assert result.status == SystemStatus.AWAITING_HUMAN
