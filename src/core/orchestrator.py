"""Orchestrator — thin Phase dispatcher.

After the A3 refactor the Orchestrator is ~200 LOC.  It only:
1. Builds a PhaseContext from config, tools, and agents.
2. Dispatches Phase classes in a status-driven loop.
3. Applies PhaseOutcome (memory update, checkpoint).
4. Provides the global exception safety-net.

All domain logic lives in ``src.core.phases.*``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from src.agents.base_agent import BaseAgent
from src.agents.registry import AgentRegistry

# Import agent modules to trigger self-registration
import src.agents.planner_agent  # noqa: F401
import src.agents.planner_judge_agent  # noqa: F401
import src.agents.conflict_analyst_agent  # noqa: F401
import src.agents.executor_agent  # noqa: F401
import src.agents.judge_agent  # noqa: F401
import src.agents.human_interface_agent  # noqa: F401

from src.core.checkpoint import Checkpoint
from src.core.message_bus import MessageBus
from src.core.phase_runner import PhaseRunner
from src.core.phases import (
    AutoMergePhase,
    ConflictAnalysisPhase,
    HumanReviewPhase,
    InitializePhase,
    JudgeReviewPhase,
    Phase,
    PhaseContext,
    PlanningPhase,
    PlanReviewPhase,
    ReportGenerationPhase,
)
from src.core.state_machine import StateMachine
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.tools.gate_runner import GateRunner
from src.tools.git_tool import GitTool
from src.tools.trace_logger import TraceLogger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status → Phase mapping
# ---------------------------------------------------------------------------
PHASE_MAP: dict[SystemStatus, type[Phase]] = {
    SystemStatus.INITIALIZED: InitializePhase,
    SystemStatus.PLANNING: PlanningPhase,
    SystemStatus.PLAN_REVIEWING: PlanReviewPhase,
    SystemStatus.AUTO_MERGING: AutoMergePhase,
    SystemStatus.ANALYZING_CONFLICTS: ConflictAnalysisPhase,
    SystemStatus.AWAITING_HUMAN: HumanReviewPhase,
    SystemStatus.JUDGE_REVIEWING: JudgeReviewPhase,
    SystemStatus.GENERATING_REPORT: ReportGenerationPhase,
}

# Per-status activity notifications (agent_name, before_msg, after_msg)
_PHASE_ACTIVITY: dict[SystemStatus, tuple[str, str, str]] = {
    SystemStatus.INITIALIZED: (
        "orchestrator",
        "Initializing — collecting file diffs",
        "Initialization done",
    ),
    SystemStatus.PLANNING: ("planner", "Generating merge plan", "Plan generated"),
    SystemStatus.PLAN_REVIEWING: (
        "planner_judge",
        "Reviewing merge plan",
        "Plan review done",
    ),
    SystemStatus.AUTO_MERGING: (
        "executor",
        "Auto-merging safe files",
        "Auto-merge done",
    ),
    SystemStatus.ANALYZING_CONFLICTS: (
        "conflict_analyst",
        "Analyzing conflicts",
        "Conflict analysis done",
    ),
    SystemStatus.JUDGE_REVIEWING: (
        "judge",
        "Reviewing merge quality",
        "Judge review done",
    ),
    SystemStatus.GENERATING_REPORT: (
        "orchestrator",
        "Generating report",
        "Report generated",
    ),
}

_TERMINAL = frozenset({SystemStatus.COMPLETED, SystemStatus.FAILED})


class Orchestrator:
    """Phase dispatcher — delegates all domain work to Phase classes."""

    OnActivityCallback = Callable[[str, str], None]

    def __init__(
        self,
        config: MergeConfig,
        agents: dict[str, BaseAgent] | None = None,
    ) -> None:
        self.config = config

        # --- tools ---
        self.git_tool = GitTool(config.repo_path)
        self.gate_runner = GateRunner(Path(config.repo_path).resolve())
        self.state_machine = StateMachine()
        self.message_bus = MessageBus()
        self.checkpoint = Checkpoint(config.output.debug_directory)
        self.phase_runner = PhaseRunner(batch_size=10, max_concurrency=5)

        # --- agents (B3: registry-based creation with DI override) ---
        agent_map = agents or AgentRegistry.create_all(config, git_tool=self.git_tool)
        self.planner = agent_map["planner"]
        self.planner_judge = agent_map["planner_judge"]
        self.conflict_analyst = agent_map["conflict_analyst"]
        self.executor = agent_map["executor"]
        self.judge = agent_map["judge"]
        self.human_interface = agent_map["human_interface"]

        self._all_agents = list(agent_map.values())

        # --- memory ---
        self._memory_store = MemoryStore()
        self._summarizer = PhaseSummarizer()

        # --- logging/activity ---
        self._log_handler: logging.FileHandler | None = None
        self._trace_logger: TraceLogger | None = None
        self._on_activity: Orchestrator.OnActivityCallback | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_activity_callback(self, cb: OnActivityCallback) -> None:
        self._on_activity = cb

    async def run(self, state: MergeState) -> MergeState:
        self._setup_run_logger(state.run_id)
        logger.info("=== Merge run %s started ===", state.run_id)
        logger.info(
            "Config: upstream=%s, fork=%s, max_files_per_run=%d, language=%s",
            self.config.upstream_ref,
            self.config.fork_ref,
            self.config.max_files_per_run,
            self.config.output.language,
        )
        run_start = time.monotonic()
        self.checkpoint.register_signal_handler(state)

        self._memory_store = MemoryStore.from_memory(state.memory)
        self._inject_memory()

        try:
            while state.status in PHASE_MAP and state.status not in _TERMINAL:
                phase_cls = PHASE_MAP[state.status]
                phase = phase_cls()
                ctx = self._build_context()

                agent, before_msg, after_msg = _PHASE_ACTIVITY.get(
                    state.status, (phase.name, "running", "done")
                )
                self._emit(agent, before_msg)

                t0 = time.monotonic()
                outcome = await phase.run(state, ctx)
                elapsed = time.monotonic() - t0

                self._emit(agent, after_msg)
                logger.info("Phase %s completed in %.1fs", phase.name, elapsed)

                if outcome.should_update_memory:
                    self._update_memory(outcome.memory_phase, state)
                    self._inject_memory()

                if outcome.should_checkpoint:
                    self.checkpoint.save(state, outcome.checkpoint_tag)

                if outcome.extra.get("paused"):
                    self._finalize_log(state, run_start)
                    return state

        except Exception as e:
            logger.error("Orchestration failed: %s", e, exc_info=True)
            state.errors.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "phase": state.current_phase.value
                    if hasattr(state.current_phase, "value")
                    else str(state.current_phase),
                    "message": str(e),
                }
            )
            try:
                self.state_machine.transition(state, SystemStatus.FAILED, str(e))
            except ValueError:
                pass
            self.checkpoint.save(state, "failed")

        self._finalize_log(state, run_start)
        return state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_context(self) -> PhaseContext:
        return PhaseContext(
            config=self.config,
            git_tool=self.git_tool,
            gate_runner=self.gate_runner,
            state_machine=self.state_machine,
            message_bus=self.message_bus,
            checkpoint=self.checkpoint,
            phase_runner=self.phase_runner,
            memory_store=self._memory_store,
            summarizer=self._summarizer,
            trace_logger=self._trace_logger,
            emit=self._on_activity,
            agents={
                "planner": self.planner,
                "planner_judge": self.planner_judge,
                "conflict_analyst": self.conflict_analyst,
                "executor": self.executor,
                "judge": self.judge,
                "human_interface": self.human_interface,
            },
        )

    def _emit(self, agent: str, action: str) -> None:
        if self._on_activity:
            self._on_activity(agent, action)

    def _update_memory(self, phase: str, state: MergeState) -> None:
        method = getattr(self._summarizer, f"summarize_{phase}", None)
        if method is None:
            return
        try:
            phase_summary, entries = method(state)
            store = self._memory_store.record_phase_summary(phase_summary)
            for entry in entries:
                store = store.add_entry(entry)
            count_before = store.entry_count
            store = store.remove_superseded(phase)
            removed = count_before - store.entry_count
            self._memory_store = store
            state.memory = store.to_memory()
            logger.info(
                "Memory updated after %s: %d entries total, %d new, %d superseded removed",
                phase,
                store.entry_count,
                len(entries),
                removed,
            )
        except Exception as e:
            logger.warning("Memory summarization failed for %s: %s", phase, e)

    def _inject_memory(self) -> None:
        for agent in self._all_agents:
            agent.set_memory_store(self._memory_store)

    # ------------------------------------------------------------------
    # Run-level logging
    # ------------------------------------------------------------------

    def _setup_run_logger(self, run_id: str) -> Path:
        debug_dir = Path(self.config.output.debug_directory)
        debug_dir.mkdir(parents=True, exist_ok=True)
        log_path = debug_dir / f"run_{run_id}.log"

        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)

        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)

        for noisy in ("httpx", "httpcore", "anthropic", "openai", "git.cmd"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        self._log_handler = handler

        if self.config.output.include_llm_traces:
            self._trace_logger = TraceLogger(str(debug_dir), run_id)
            for agent in self._all_agents:
                agent.set_trace_logger(self._trace_logger)

        return log_path

    def _teardown_run_logger(self) -> None:
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None

    def _finalize_log(self, state: MergeState, run_start: float) -> None:
        elapsed = time.monotonic() - run_start
        logger.info(
            "=== Merge run %s finished — status=%s, elapsed=%.1fs ===",
            state.run_id,
            state.status.value if hasattr(state.status, "value") else state.status,
            elapsed,
        )
        if self._trace_logger:
            utilization_summary = self._trace_logger.get_utilization_summary()
            if utilization_summary:
                logger.info("Context utilization summary: %s", utilization_summary)
        self._teardown_run_logger()


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (used by tests and phase modules)
# ---------------------------------------------------------------------------
from src.core.phases.conflict_analysis import (  # noqa: E402
    _build_human_decision_request,
    _select_merge_strategy,
)
from src.core.phases.initialize import _parse_file_status  # noqa: E402

__all__ = [
    "Orchestrator",
    "PHASE_MAP",
    "_build_human_decision_request",
    "_parse_file_status",
    "_select_merge_strategy",
]
