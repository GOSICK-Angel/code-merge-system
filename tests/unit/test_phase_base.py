import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import FrozenInstanceError

from src.core.phases.base import (
    ActivityEvent,
    OnActivityCallback,
    Phase,
    PhaseContext,
    PhaseOutcome,
)
from src.models.state import MergeState, SystemStatus
from src.models.config import MergeConfig


def _make_config(**overrides):
    defaults = {"upstream_ref": "upstream/main", "fork_ref": "fork/main"}
    defaults.update(overrides)
    return MergeConfig(**defaults)


def _make_context(**overrides):
    defaults = {
        "config": _make_config(),
        "git_tool": MagicMock(),
        "gate_runner": MagicMock(),
        "state_machine": MagicMock(),
        "message_bus": MagicMock(),
        "checkpoint": MagicMock(),
        "phase_runner": MagicMock(),
        "memory_store": MagicMock(),
        "summarizer": MagicMock(),
    }
    defaults.update(overrides)
    return PhaseContext(**defaults)


def _make_state():
    return MergeState(config=_make_config())


class ConcretePhase(Phase):
    name = "test_phase"

    def __init__(self, outcome=None):
        super().__init__()
        self._outcome = outcome or PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="done",
        )

    async def execute(self, state, ctx):
        return self._outcome


class TrackingPhase(Phase):
    name = "tracking"

    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    async def before(self, state, ctx):
        self.calls.append("before")

    async def execute(self, state, ctx):
        self.calls.append("execute")
        return PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="ok",
        )

    async def after(self, state, outcome, ctx):
        self.calls.append("after")


class TestPhaseContext:
    def test_frozen(self):
        ctx = _make_context()
        with pytest.raises(FrozenInstanceError):
            ctx.config = _make_config()

    def test_notify_calls_emit(self):
        cb = MagicMock()
        ctx = _make_context(emit=cb)
        ctx.notify("planner", "generating plan")
        cb.assert_called_once()
        event = cb.call_args[0][0]
        assert isinstance(event, ActivityEvent)
        assert event.agent == "planner"
        assert event.action == "generating plan"
        assert event.event_type == "progress"

    def test_notify_no_emit_is_noop(self):
        ctx = _make_context(emit=None)
        ctx.notify("planner", "test")

    def test_agents_dict_default_empty(self):
        ctx = _make_context()
        assert ctx.agents == {}

    def test_agents_dict_populated(self):
        mock_agent = MagicMock()
        ctx = _make_context(agents={"planner": mock_agent})
        assert ctx.agents["planner"] is mock_agent

    def test_trace_logger_default_none(self):
        ctx = _make_context()
        assert ctx.trace_logger is None


class TestPhaseOutcome:
    def test_should_checkpoint(self):
        outcome = PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="done",
            checkpoint_tag="after_phase1",
        )
        assert outcome.should_checkpoint is True

    def test_should_not_checkpoint(self):
        outcome = PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="done",
        )
        assert outcome.should_checkpoint is False

    def test_should_update_memory(self):
        outcome = PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="done",
            memory_phase="planning",
        )
        assert outcome.should_update_memory is True

    def test_should_not_update_memory(self):
        outcome = PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="done",
        )
        assert outcome.should_update_memory is False

    def test_frozen(self):
        outcome = PhaseOutcome(target_status=SystemStatus.COMPLETED, reason="done")
        with pytest.raises(FrozenInstanceError):
            outcome.reason = "changed"

    def test_extra_dict(self):
        outcome = PhaseOutcome(
            target_status=SystemStatus.COMPLETED,
            reason="done",
            extra={"files_processed": 42},
        )
        assert outcome.extra["files_processed"] == 42


class TestPhaseABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Phase()

    def test_concrete_instantiation(self):
        phase = ConcretePhase()
        assert phase.name == "test_phase"

    def test_logger_name(self):
        phase = ConcretePhase()
        assert phase.logger.name == "phase.test_phase"


class TestPhaseLifecycle:
    @pytest.mark.asyncio
    async def test_run_calls_before_execute_after(self):
        phase = TrackingPhase()
        ctx = _make_context()
        state = _make_state()

        outcome = await phase.run(state, ctx)

        assert phase.calls == ["before", "execute", "after"]
        assert outcome.target_status == SystemStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_returns_outcome(self):
        expected = PhaseOutcome(
            target_status=SystemStatus.JUDGE_REVIEWING,
            reason="proceeding to judge",
            checkpoint_tag="after_phase2",
            memory_phase="auto_merge",
        )
        phase = ConcretePhase(outcome=expected)
        ctx = _make_context()
        state = _make_state()

        result = await phase.run(state, ctx)

        assert result is expected
        assert result.target_status == SystemStatus.JUDGE_REVIEWING
        assert result.should_checkpoint is True
        assert result.should_update_memory is True

    @pytest.mark.asyncio
    async def test_default_before_after_are_noop(self):
        phase = ConcretePhase()
        ctx = _make_context()
        state = _make_state()

        await phase.before(state, ctx)
        await phase.after(
            state, PhaseOutcome(target_status=SystemStatus.COMPLETED, reason="ok"), ctx
        )

    @pytest.mark.asyncio
    async def test_execute_called_directly(self):
        phase = ConcretePhase()
        ctx = _make_context()
        state = _make_state()

        outcome = await phase.execute(state, ctx)
        assert outcome.target_status == SystemStatus.COMPLETED
