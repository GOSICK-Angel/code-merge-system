"""U-P2.1 ~ U-P2.6 / U-P2.9 ~ U-P2.13 — per-run budget cap regression net.

Covers:
* BaseAgent._call_llm_with_retry pre/post budget gate (U-P2.1, U-P2.9, U-P2.10)
* Orchestrator except RunBudgetExceeded → AWAITING_HUMAN + partial report
  (U-P2.2, U-P2.5)
* budget_warning activity event at warn_pct first crossing (U-P2.3)
* max_cost_usd=None disables cap (U-P2.4, U-P2.8 already covered in
  test_telemetry_snapshot)
* end-to-end double-transition guard (U-P2.6)
* RunBudgetExceeded.phase sourced from current_phase (U-P2.12)
* G5 ceiling check short-circuits when status == AWAITING_HUMAN (U-P2.13)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import git as _git
import pytest

from src.agents.base_agent import BaseAgent
from src.core.orchestrator import Orchestrator
from src.models.config import AgentLLMConfig, MergeConfig, OutputConfig
from src.models.message import AgentType
from src.models.state import MergeState, RunBudgetExceeded, SystemStatus
from src.tools.cost_tracker import CostTracker, TokenUsage


def _make_config(tmp_path, **overrides) -> MergeConfig:
    if not (tmp_path / ".git").exists():
        _git.Repo.init(str(tmp_path))
    defaults = dict(
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
    )
    defaults.update(overrides)
    return MergeConfig(**defaults)


class _StubAgent(BaseAgent):
    """Minimal BaseAgent subclass for exercising _check_budget without
    actually hitting an LLM provider."""

    agent_type = AgentType.PLANNER
    contract_name = None

    def __init__(self):
        super().__init__(AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"))

    async def run(self, state):  # pragma: no cover — unused
        raise NotImplementedError

    def can_handle(self, state) -> bool:  # pragma: no cover — unused
        return False


def _make_stub_tracker(total: float) -> CostTracker:
    """Return a CostTracker whose total_cost_usd reads back as ``total``.

    Patching the @property directly is fragile because it lives on the
    class; instead we record a single high-cost entry and rely on real
    accounting. Pricing for the dummy model is zero by default, so we
    inject a custom pricing table that produces an exact cost.
    """
    from src.tools.cost_tracker import PricingEntry

    tracker = CostTracker(
        pricing={"x": PricingEntry(input_per_m=total * 1e6, output_per_m=0.0)}
    )
    tracker.record(
        agent="planner",
        phase="planning",
        model="x",
        provider="anthropic",
        usage=TokenUsage(input_tokens=1, output_tokens=0),
    )
    return tracker


class TestBudgetCheckUnit:
    """U-P2.1 / U-P2.9 / U-P2.12: _check_budget raises when spent >= limit."""

    def test_budget_exceeded_at_hard_cap_raises(self):
        agent = _StubAgent()
        agent.set_cost_tracker(_make_stub_tracker(5.01), phase="planning")
        agent.set_budget(limit_usd=5.0, warn_pct=0.8)
        with pytest.raises(RunBudgetExceeded) as exc:
            agent._check_budget()
        assert exc.value.limit == 5.0
        assert exc.value.spent >= 5.0
        assert exc.value.phase == "planning"

    def test_budget_exceeded_at_exact_limit(self):
        """Boundary: spent == limit triggers (>=, not >)."""
        agent = _StubAgent()
        agent.set_cost_tracker(_make_stub_tracker(5.0), phase="conflict_analysis")
        agent.set_budget(limit_usd=5.0, warn_pct=0.8)
        with pytest.raises(RunBudgetExceeded):
            agent._check_budget()

    def test_phase_sourced_from_current_phase(self):
        agent = _StubAgent()
        agent.set_cost_tracker(_make_stub_tracker(99.0), phase="judge_review")
        agent.set_budget(limit_usd=5.0, warn_pct=0.8)
        with pytest.raises(RunBudgetExceeded) as exc:
            agent._check_budget()
        assert exc.value.phase == "judge_review"

    def test_budget_disabled_when_limit_is_none(self):
        agent = _StubAgent()
        agent.set_cost_tracker(_make_stub_tracker(99.0), phase="planning")
        agent.set_budget(limit_usd=None, warn_pct=0.8)
        # Must not raise — disabled cap.
        agent._check_budget()


class TestBudgetWarning:
    """U-P2.3: first crossing of warn_pct emits a budget_warning event."""

    def test_first_crossing_emits_once(self):
        agent = _StubAgent()
        events = []
        agent.set_activity_callback(lambda evt: events.append(evt))
        agent.set_budget(limit_usd=5.0, warn_pct=0.8)
        # Pre-warn band (3.5 / 5.0 = 70%) → no event.
        agent.set_cost_tracker(_make_stub_tracker(3.5), phase="planning")
        agent._check_budget()
        assert len(events) == 0
        # Cross warn band (4.1 / 5.0 = 82%) → emit one event.
        agent.set_cost_tracker(_make_stub_tracker(4.1), phase="planning")
        agent._check_budget()
        assert len(events) == 1
        assert events[0].action == "budget_warning"
        assert events[0].extra["pct"] == pytest.approx(0.82, abs=0.01)
        # Already-emitted state must not refire even higher in the band.
        agent.set_cost_tracker(_make_stub_tracker(4.8), phase="planning")
        agent._check_budget()
        assert len(events) == 1


class TestBudgetGateInsideCallLLM:
    """U-P2.10: pre-call gate raises before LLM is invoked; post-call gate
    raises after a successful call that pushed spend past the cap."""

    async def test_pre_call_check_blocks_llm(self):
        agent = _StubAgent()
        agent.set_cost_tracker(_make_stub_tracker(5.5), phase="planning")
        agent.set_budget(limit_usd=5.0, warn_pct=0.8)

        provider_call = AsyncMock()
        agent.llm.complete = provider_call  # type: ignore[method-assign]

        with pytest.raises(RunBudgetExceeded):
            await agent._call_llm_with_retry([{"role": "user", "content": "hi"}])
        provider_call.assert_not_called()


class TestOrchestratorBudgetExceeded:
    """U-P2.2 / U-P2.5: orchestrator catches RunBudgetExceeded, transitions
    to AWAITING_HUMAN, writes partial report, tags checkpoint."""

    async def test_transitions_to_awaiting_human_and_writes_report(self, tmp_path):
        config = _make_config(tmp_path, max_cost_usd=5.0)
        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents={})
        state = MergeState(config=config)
        state.run_id = "test-run"
        state.status = SystemStatus.AUTO_MERGING

        # Stub the phase loop so a single phase raises RunBudgetExceeded.
        async def boom_phase_run(*args, **kwargs):
            raise RunBudgetExceeded(spent=5.01, limit=5.0, phase="auto_merging")

        with (
            patch(
                "src.core.orchestrator.PHASE_MAP",
                {SystemStatus.AUTO_MERGING: MagicMock},
            ),
            patch.object(orch, "_inject_cost_tracker"),
            patch.object(orch, "_build_context"),
            patch.object(orch, "_finalize_log"),
            patch.object(orch, "_snapshot_telemetry"),
            patch("src.core.orchestrator.Checkpoint.save") as save_mock,
            patch.object(orch._hooks, "emit", new=AsyncMock()),
        ):
            phase_instance = MagicMock()
            phase_instance.run = AsyncMock(side_effect=boom_phase_run)
            phase_instance.name = "auto_merging"
            phase_cls_mock = MagicMock(return_value=phase_instance)
            with patch(
                "src.core.orchestrator.PHASE_MAP",
                {SystemStatus.AUTO_MERGING: phase_cls_mock},
            ):
                result = await orch.run(state)

        assert result.status == SystemStatus.AWAITING_HUMAN
        # Last checkpoint save tag must be budget_exceeded.
        tags = [c.args[1] for c in save_mock.call_args_list]
        assert "budget_exceeded" in tags
        # Partial report file exists.
        report = tmp_path / ".merge" / "runs" / "test-run" / "budget_exceeded_report.md"
        assert report.exists()
        body = report.read_text()
        assert "5.0" in body and "auto_merging" in body and "spent" in body

    async def test_g5_ceiling_check_skips_when_status_awaiting_human(self, tmp_path):
        """U-P2.13: once status is AWAITING_HUMAN the per-iteration ceiling
        check must short-circuit before re-triggering a transition."""
        config = _make_config(tmp_path, max_cost_usd=5.0)
        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents={})
        state = MergeState(config=config)
        state.status = SystemStatus.AWAITING_HUMAN

        # Inject enough cost that the G5 ceiling would trip if reached.
        orch._cost_tracker.record(
            agent="planner",
            phase="planning",
            model="claude-opus-4-6",
            provider="anthropic",
            usage=TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        )

        with (
            patch.object(orch, "_finalize_log"),
            patch.object(orch, "_snapshot_telemetry"),
            patch("src.core.orchestrator.Checkpoint.save") as save,
        ):
            result = await orch.run(state)

        assert result.status == SystemStatus.AWAITING_HUMAN
        # Short-circuit path doesn't tag a fresh cost_ceiling_halt checkpoint.
        tags = [c.args[1] for c in save.call_args_list]
        assert "cost_ceiling_halt" not in tags

    async def test_budget_double_transition_end_to_end_scenario(self, tmp_path):
        """U-P2.6: BaseAgent raise → orchestrator transition; the same run
        continuing to the next loop iteration must not re-transition."""
        config = _make_config(tmp_path, max_cost_usd=5.0)
        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents={})
        state = MergeState(config=config)
        state.run_id = "double-tx"
        state.status = SystemStatus.AUTO_MERGING

        async def boom_phase_run(*args, **kwargs):
            raise RunBudgetExceeded(spent=5.01, limit=5.0, phase="auto_merging")

        phase_instance = MagicMock()
        phase_instance.run = AsyncMock(side_effect=boom_phase_run)
        phase_instance.name = "auto_merging"
        phase_cls_mock = MagicMock(return_value=phase_instance)

        with (
            patch(
                "src.core.orchestrator.PHASE_MAP",
                {SystemStatus.AUTO_MERGING: phase_cls_mock},
            ),
            patch.object(orch, "_inject_cost_tracker"),
            patch.object(orch, "_build_context"),
            patch.object(orch, "_finalize_log"),
            patch.object(orch, "_snapshot_telemetry"),
            patch("src.core.orchestrator.Checkpoint.save") as save_mock,
            patch.object(orch._hooks, "emit", new=AsyncMock()),
        ):
            result = await orch.run(state)

        assert result.status == SystemStatus.AWAITING_HUMAN
        tags = [c.args[1] for c in save_mock.call_args_list]
        # Exactly one budget_exceeded tag — no follow-up cost_ceiling_halt.
        assert tags.count("budget_exceeded") == 1
        assert "cost_ceiling_halt" not in tags
