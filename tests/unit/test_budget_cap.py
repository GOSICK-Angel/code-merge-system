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


class TestOrchestratorAwaitingHumanDispatch:
    """Regression guard for the web approve-flow.

    Prior to the fix the orchestrator main loop had a blanket
    ``if state.status == AWAITING_HUMAN: return`` short-circuit at the
    top, which prevented ``PHASE_MAP[AWAITING_HUMAN] = HumanReviewPhase``
    from ever dispatching on re-entry. The bridge would set
    ``plan_human_review`` and wake the orchestrator, but the loop kept
    returning before HumanReviewPhase could observe the approval and
    transition to AUTO_MERGING. These tests pin the dispatch path so
    the regression cannot be reintroduced silently.
    """

    async def test_awaiting_human_dispatches_human_review_phase(self, tmp_path):
        """When ``orch.run(state)`` is entered with status=AWAITING_HUMAN,
        ``PHASE_MAP[AWAITING_HUMAN]`` MUST be invoked. The earlier
        blanket short-circuit returned before the dispatch, breaking the
        web approve flow."""
        from src.models.state import SystemStatus

        config = _make_config(tmp_path)
        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents={})
        state = MergeState(config=config)
        state.run_id = "awaiting-dispatch"
        state.status = SystemStatus.AWAITING_HUMAN

        # Stub HumanReviewPhase via PHASE_MAP — we don't care about its
        # internal logic here, only that it was actually instantiated and
        # ``run`` was awaited. Pause the loop afterwards so the test
        # doesn't dispatch follow-on phases.
        from src.core.phases.base import PhaseOutcome

        async def paused_run(*args, **kwargs):
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason="test-stop",
                checkpoint_tag="awaiting_human",
                extra={"paused": True},
            )

        phase_instance = MagicMock()
        phase_instance.run = AsyncMock(side_effect=paused_run)
        phase_instance.name = "human_review"
        phase_cls_mock = MagicMock(return_value=phase_instance)

        with (
            patch(
                "src.core.orchestrator.PHASE_MAP",
                {SystemStatus.AWAITING_HUMAN: phase_cls_mock},
            ),
            patch.object(orch, "_inject_cost_tracker"),
            patch.object(orch, "_build_context"),
            patch.object(orch, "_finalize_log"),
            patch.object(orch, "_snapshot_telemetry"),
            patch("src.core.orchestrator.Checkpoint.save"),
            patch.object(orch._hooks, "emit", new=AsyncMock()),
        ):
            await orch.run(state)

        # The regression was: phase_cls_mock never called → HumanReviewPhase
        # never dispatched → bridge approval wakes orchestrator but state
        # stays at AWAITING_HUMAN forever.
        phase_cls_mock.assert_called_once()
        phase_instance.run.assert_awaited_once()

    async def test_plan_human_review_approve_routes_to_auto_merging(self, tmp_path):
        """End-to-end: ``state.plan_human_review.decision == APPROVE`` with
        no undecided ``pending_user_decisions`` MUST cause the real
        HumanReviewPhase to transition to AUTO_MERGING on the same
        ``orch.run`` call. Earlier the dispatch was suppressed and the
        run stayed parked at AWAITING_HUMAN."""
        from src.models.plan_review import PlanHumanDecision, PlanHumanReview
        from src.models.state import SystemStatus

        config = _make_config(tmp_path)
        with patch("src.core.orchestrator.GitTool"):
            orch = Orchestrator(config, agents={})
        state = MergeState(config=config)
        state.run_id = "approve-routes"
        state.status = SystemStatus.AWAITING_HUMAN
        state.plan_human_review = PlanHumanReview(
            decision=PlanHumanDecision.APPROVE,
            reviewer_name="web_user",
        )
        # Empty pending_user_decisions means HumanReviewPhase's O-L4 guard
        # ("any item.user_choice is None") doesn't fire — the path under
        # test is the clean APPROVE → AUTO_MERGING route.
        state.pending_user_decisions = []

        # Stop the loop right after the transition by pointing AUTO_MERGING
        # to a paused stub, so the assertion can observe status without
        # the real AutoMergePhase running.
        from src.core.phases.base import PhaseOutcome
        from src.core.phases.human_review import HumanReviewPhase

        async def auto_merge_stub(*args, **kwargs):
            return PhaseOutcome(
                target_status=SystemStatus.AUTO_MERGING,
                reason="test-stop",
                checkpoint_tag="auto_merge_stop",
                extra={"paused": True},
            )

        auto_merge_phase = MagicMock()
        auto_merge_phase.run = AsyncMock(side_effect=auto_merge_stub)
        auto_merge_phase.name = "auto_merge"
        auto_merge_cls = MagicMock(return_value=auto_merge_phase)

        with (
            patch(
                "src.core.orchestrator.PHASE_MAP",
                {
                    SystemStatus.AWAITING_HUMAN: HumanReviewPhase,
                    SystemStatus.AUTO_MERGING: auto_merge_cls,
                },
            ),
            patch.object(orch, "_inject_cost_tracker"),
            patch.object(orch, "_finalize_log"),
            patch.object(orch, "_snapshot_telemetry"),
            patch("src.core.orchestrator.Checkpoint.save"),
            patch("src.core.phases.human_review.write_plan_review_report"),
            patch("src.core.phases.human_review.write_merge_plan_report"),
            patch.object(orch._hooks, "emit", new=AsyncMock()),
        ):
            result = await orch.run(state)

        # Before the fix: status stayed AWAITING_HUMAN, auto_merge_cls
        # never called.
        auto_merge_cls.assert_called_once()
        # Final state reflects the AUTO_MERGING dispatch that the stub
        # paused inside.
        assert result.status == SystemStatus.AUTO_MERGING
