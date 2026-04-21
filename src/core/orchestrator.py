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
from typing import Literal

from src.agents.base_agent import BaseAgent
from src.agents.registry import AgentRegistry

# Import agent modules to trigger self-registration
import src.agents.planner_agent  # noqa: F401
import src.agents.planner_judge_agent  # noqa: F401
import src.agents.conflict_analyst_agent  # noqa: F401
import src.agents.executor_agent  # noqa: F401
import src.agents.judge_agent  # noqa: F401
import src.agents.human_interface_agent  # noqa: F401
import src.agents.memory_extractor_agent  # noqa: F401

from src.cli.paths import ensure_merge_dir, get_run_dir, get_system_log_dir, is_dev_mode
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
from src.core.phases.base import ActivityEvent, OnActivityCallback
from src.core.state_machine import StateMachine
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.tools.gate_runner import GateRunner
from src.tools.git_tool import GitTool
from src.core.hooks import HookManager
from src.tools.cost_tracker import CostTracker
from src.tools.structured_logger import create_structured_handler
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
        run_dir = Path(config.output.debug_directory) / "checkpoints"
        self.checkpoint = Checkpoint(
            run_dir, debug_checkpoints=config.output.debug_checkpoints
        )
        self.phase_runner = PhaseRunner(batch_size=10, max_concurrency=5)

        # --- agents (B3: registry-based creation with DI override) ---
        agent_map = agents or AgentRegistry.create_all(config, git_tool=self.git_tool)
        self.planner = agent_map["planner"]
        self.planner_judge = agent_map["planner_judge"]
        self.conflict_analyst = agent_map["conflict_analyst"]
        self.executor = agent_map["executor"]
        self.judge = agent_map["judge"]
        self.human_interface = agent_map["human_interface"]
        self.memory_extractor = agent_map.get("memory_extractor")

        self._all_agents = list(agent_map.values())

        # --- memory ---
        self._memory_store = MemoryStore()
        self._summarizer = PhaseSummarizer()

        # --- hooks (C1) ---
        self._hooks = HookManager()

        # --- cost tracking (C3) ---
        self._cost_tracker = CostTracker()

        # --- logging/activity ---
        self._log_handler: logging.FileHandler | None = None
        self._structured_handler: logging.FileHandler | None = None
        self._trace_logger: TraceLogger | None = None
        self._on_activity: OnActivityCallback | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def hooks(self) -> HookManager:
        """Expose hook manager for external registration."""
        return self._hooks

    def set_activity_callback(self, cb: OnActivityCallback) -> None:
        self._on_activity = cb

    async def run(self, state: MergeState) -> MergeState:
        # Re-initialize checkpoint with the per-run directory now that run_id is known.
        run_dir = get_run_dir(self.config.repo_path, state.run_id)
        self.checkpoint = Checkpoint(
            run_dir, debug_checkpoints=self.config.output.debug_checkpoints
        )
        if not is_dev_mode():
            ensure_merge_dir(self.config.repo_path)

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

        db_path = run_dir / "memory.db"
        self._memory_store = SQLiteMemoryStore.open(db_path)
        state.memory_db_path = str(db_path)
        self._inject_memory()
        self._inject_hooks()

        try:
            while state.status in PHASE_MAP and state.status not in _TERMINAL:
                phase_cls = PHASE_MAP[state.status]
                phase = phase_cls()
                ctx = self._build_context()
                previous_status = state.status

                agent, before_msg, after_msg = _PHASE_ACTIVITY.get(
                    state.status, (phase.name, "running", "done")
                )
                self._inject_cost_tracker(phase=phase.name)
                self._emit(agent, before_msg, event_type="start", phase=phase.name)
                await self._hooks.emit(
                    "phase:before",
                    phase=phase.name,
                    status=state.status,
                    state=state,
                )

                t0 = time.monotonic()
                outcome = await phase.run(state, ctx)
                elapsed = time.monotonic() - t0

                self._emit(
                    agent,
                    after_msg,
                    event_type="complete",
                    phase=phase.name,
                    elapsed=elapsed,
                )
                await self._hooks.emit(
                    "phase:after",
                    phase=phase.name,
                    status=state.status,
                    outcome=outcome,
                    elapsed=elapsed,
                    state=state,
                )
                logger.info("Phase %s completed in %.1fs", phase.name, elapsed)

                if self._trace_logger:
                    self._trace_logger.record_phase_transition(
                        run_id=state.run_id,
                        from_status=previous_status.value
                        if hasattr(previous_status, "value")
                        else str(previous_status),
                        to_status=outcome.target_status.value
                        if hasattr(outcome.target_status, "value")
                        else str(outcome.target_status),
                        triggered_by=phase.name,
                        elapsed=elapsed,
                        reason=outcome.reason,
                    )

                if outcome.should_update_memory:
                    await self._update_memory(outcome.memory_phase, state)
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

        await self._hooks.emit(
            "merge:complete",
            state=state,
            elapsed=time.monotonic() - run_start,
        )
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
            hooks=self._hooks,
            cost_tracker=self._cost_tracker,
            agents={
                "planner": self.planner,
                "planner_judge": self.planner_judge,
                "conflict_analyst": self.conflict_analyst,
                "executor": self.executor,
                "judge": self.judge,
                "human_interface": self.human_interface,
            },
        )

    def _emit(
        self,
        agent: str,
        action: str,
        event_type: Literal["start", "progress", "complete", "error"] = "progress",
        phase: str = "",
        elapsed: float | None = None,
    ) -> None:
        if self._on_activity:
            self._on_activity(
                ActivityEvent(
                    agent=agent,
                    action=action,
                    phase=phase,
                    event_type=event_type,
                    elapsed=elapsed,
                )
            )

    async def _update_memory(self, phase: str, state: MergeState) -> None:
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
            logger.info(
                "Memory updated after %s: %d entries total, %d new, %d superseded removed",
                phase,
                store.entry_count,
                len(entries),
                removed,
            )
        except Exception as e:
            logger.warning("Memory summarization failed for %s: %s", phase, e)

        if self.memory_extractor is not None and self._should_llm_extract(phase, state):
            try:
                llm_entries = await self.memory_extractor.extract(phase, state)
                for e in llm_entries:
                    self._memory_store.add_entry(e)
            except Exception as e:
                logger.warning("LLM memory extraction failed for %s: %s", phase, e)

    def _should_llm_extract(self, phase: str, state: MergeState) -> bool:
        cfg = getattr(self.config, "memory", None)
        if cfg is None or not cfg.llm_extraction:
            return False
        if state.errors:
            return True
        if phase == "planning" and state.plan_disputes:
            return True
        if (
            phase == "judge_review"
            and state.judge_repair_rounds >= cfg.min_judge_repair_rounds
        ):
            return True
        return False

    def _inject_memory(self) -> None:
        for agent in self._all_agents:
            agent.set_memory_store(self._memory_store)

    def _inject_cost_tracker(self, phase: str = "") -> None:
        for agent in self._all_agents:
            agent.set_cost_tracker(self._cost_tracker, phase=phase)

    def _inject_hooks(self) -> None:
        for agent in self._all_agents:
            agent.set_hooks(self._hooks)

    # ------------------------------------------------------------------
    # Run-level logging
    # ------------------------------------------------------------------

    def _setup_run_logger(self, run_id: str) -> Path:
        log_dir = get_system_log_dir(self.config.repo_path)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"run_{run_id}.log"

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

        if self.config.output.structured_logs:
            structured_path = str(log_dir / f"run_{run_id}.jsonl")
            self._structured_handler = create_structured_handler(structured_path)
            root.addHandler(self._structured_handler)

        if self.config.output.include_llm_traces:
            self._trace_logger = TraceLogger(str(log_dir), run_id)
            for agent in self._all_agents:
                agent.set_trace_logger(self._trace_logger)

        return log_path

    def _teardown_run_logger(self) -> None:
        root = logging.getLogger()
        if self._log_handler:
            root.removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None
        if self._structured_handler:
            root.removeHandler(self._structured_handler)
            self._structured_handler.close()
            self._structured_handler = None

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
        if self._cost_tracker.total_calls > 0:
            logger.info("Cost summary: %s", self._cost_tracker.summary())
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
