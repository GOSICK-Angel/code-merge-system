from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.core.state_machine import StateMachine
from src.core.message_bus import MessageBus
from src.core.checkpoint import Checkpoint
from src.core.phase_runner import PhaseRunner
from src.tools.git_tool import GitTool
from src.tools.gate_runner import GateRunner
from src.tools.trace_logger import TraceLogger
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.core.hooks import HookManager
from src.tools.cost_tracker import CostTracker


@dataclass(frozen=True)
class ActivityEvent:
    agent: str
    action: str
    phase: str
    event_type: Literal["start", "progress", "complete", "error"]
    elapsed: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


OnActivityCallback = Callable[[ActivityEvent], None]


@dataclass(frozen=True)
class PhaseContext:
    """Shared dependencies injected into each Phase.

    All phases receive the same context instance per run.
    Individual phases pick what they need — unused fields
    carry no cost.
    """

    config: MergeConfig
    git_tool: GitTool
    gate_runner: GateRunner
    state_machine: StateMachine
    message_bus: MessageBus
    checkpoint: Checkpoint
    phase_runner: PhaseRunner
    memory_store: MemoryStore
    summarizer: PhaseSummarizer
    trace_logger: TraceLogger | None = None
    emit: OnActivityCallback | None = None
    hooks: HookManager = field(default_factory=HookManager)
    cost_tracker: CostTracker = field(default_factory=CostTracker)
    agents: dict[str, Any] = field(default_factory=dict)

    def notify(self, agent: str, action: str) -> None:
        if self.emit is not None:
            self.emit(
                ActivityEvent(
                    agent=agent, action=action, phase="", event_type="progress"
                )
            )


@dataclass(frozen=True)
class PhaseOutcome:
    """Result returned by Phase.execute().

    The Orchestrator uses this to drive the next state
    transition and checkpoint.
    """

    target_status: SystemStatus
    reason: str
    checkpoint_tag: str = ""
    memory_phase: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def should_checkpoint(self) -> bool:
        return bool(self.checkpoint_tag)

    @property
    def should_update_memory(self) -> bool:
        return bool(self.memory_phase)


class Phase(ABC):
    """Base class for all orchestration phases.

    Lifecycle:
        1. before()   — optional pre-checks, logging
        2. execute()  — core logic (must implement)
        3. after()    — optional cleanup, checkpoint

    Subclasses must set ``name`` and implement ``execute()``.
    """

    name: str = ""

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"phase.{self.name}")

    async def before(self, state: MergeState, ctx: PhaseContext) -> None:
        """Hook called before execute(). Override for pre-checks."""

    @abstractmethod
    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        """Core phase logic. Must return a PhaseOutcome."""
        ...

    async def after(
        self,
        state: MergeState,
        outcome: PhaseOutcome,
        ctx: PhaseContext,
    ) -> None:
        """Hook called after execute(). Override for cleanup."""

    async def run(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        """Full lifecycle: before → execute → after.

        Orchestrator calls this, not execute() directly.
        """
        await self.before(state, ctx)
        outcome = await self.execute(state, ctx)
        await self.after(state, outcome, ctx)
        return outcome
