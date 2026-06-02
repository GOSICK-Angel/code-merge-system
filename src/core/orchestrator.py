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
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

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

from src.cli.paths import (
    ensure_merge_dir,
    get_project_hit_stats_path,
    get_project_memory_db_path,
    get_run_dir,
    get_system_log_dir,
    is_dev_mode,
)
from src.core.checkpoint import Checkpoint
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
from src.core.coordinator import Coordinator
from src.core.state_machine import StateMachine
from src.memory.bootstrap import _BOOTSTRAP_TAG, bootstrap_from_claude_md
from src.memory.content_quality import enforce_actionable
from src.memory.hit_tracker import MemoryHitTracker
from src.memory.sqlite_store import SQLiteMemoryStore
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.config import MergeConfig
from src.models.decision import DecisionSource
from src.models.state import MergeState, RunBudgetExceeded, SystemStatus
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


def _mark_suspended_phase(state: MergeState, target_status: SystemStatus) -> None:
    """Flip the just-run phase from ``running`` → ``awaiting`` when it suspends
    to AWAITING_HUMAN.

    A phase that yields to human review returns without marking its own
    PhaseResult terminal, so it stays ``running`` forever — and across resume
    cycles several phases pile up as simultaneously RUNNING in the UI. Reflect
    the suspension so only a genuinely-executing phase reads as running.
    """
    if target_status != SystemStatus.AWAITING_HUMAN:
        return
    cur_key = (
        state.current_phase.value
        if hasattr(state.current_phase, "value")
        else str(state.current_phase)
    )
    pr = state.phase_results.get(cur_key)
    if pr is not None and pr.status == "running":
        state.phase_results[cur_key] = pr.model_copy(
            update={"status": "awaiting", "completed_at": datetime.now()}
        )


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
        run_dir = Path(config.output.debug_directory) / "checkpoints"
        self.checkpoint = Checkpoint(
            run_dir, debug_checkpoints=config.output.debug_checkpoints
        )

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

        # --- coordinator ---
        self._coordinator = Coordinator(config)

        # --- memory ---
        self._memory_store: MemoryStore | SQLiteMemoryStore = MemoryStore()
        self._memory_hit_tracker = MemoryHitTracker()
        self._summarizer = PhaseSummarizer(
            upstream_ref=config.upstream_ref,
            repair_recipe_enabled=getattr(config.memory, "repair_recipe_enabled", True),
        )
        self._phases_since_last_extract: int = 0

        # --- hooks (C1) ---
        self._hooks = HookManager()

        # --- cost tracking (C3) ---
        self._cost_tracker = CostTracker()
        # Snapshot of state.cost_summary captured at run() start. Used to
        # preserve prior-process spending across resume; in-process tracker
        # only knows about LLM calls since this Orchestrator was created.
        self._prior_cost_summary: dict[str, Any] = {}

        # --- logging/activity ---
        self._log_handler: logging.FileHandler | None = None
        self._run_dir_log_handler: logging.FileHandler | None = None
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
        # Capture prior spending from any previous process (e.g. checkpoint
        # resume) before this run does anything. _snapshot_telemetry adds the
        # in-process CostTracker on top of this so cumulative totals survive
        # resumes. Without this snapshot, subsequent _snapshot_telemetry()
        # calls would either lose prior spend or double-count it.
        import copy

        self._prior_cost_summary = copy.deepcopy(state.cost_summary or {})

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

        db_path = get_project_memory_db_path(self.config.repo_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_store = SQLiteMemoryStore.open(db_path)
        state.memory_db_path = str(db_path)
        self._memory_hit_tracker.set_persist_path(
            get_project_hit_stats_path(self.config.repo_path)
        )
        try:
            bootstrap_from_claude_md(self._memory_store, self.config.repo_path)
        except Exception:
            logger.debug("memory bootstrap failed (non-blocking)", exc_info=True)
        self._inject_memory()
        self._inject_hooks()

        if self.config.enable_working_branch and state.active_branch is None:
            branch = self.git_tool.create_working_branch(
                self.config.working_branch, self.config.fork_ref
            )
            # In-place assignment is critical: the Web bridge captures
            # ``state`` by reference in ``MergeWSBridge.__init__`` and
            # observes mutations live. ``state.model_copy(update=...)``
            # would swap the local variable to a new object — bridge
            # would then forever serialize a stale "initialized" status
            # because subsequent ``state.status = X`` mutations land on
            # the new object the bridge can't see. The shared
            # ``messages`` list (shallow-copied by model_copy) made the
            # bug doubly confusing: messages stayed in sync, status did
            # not. Direct attribute assignment keeps every observer's
            # reference valid.
            state.active_branch = branch
            logger.info(
                "Working branch created: %s (fork_ref=%s)", branch, self.config.fork_ref
            )

        try:
            while state.status in PHASE_MAP and state.status not in _TERMINAL:
                if state.dry_run and state.status == SystemStatus.AUTO_MERGING:
                    self.state_machine.transition(
                        state,
                        SystemStatus.AWAITING_HUMAN,
                        "dry-run: analysis and plan complete; skipping executor/judge",
                    )
                    self._snapshot_telemetry(state)
                    self.checkpoint.save(state, "dry_run_halt")
                    self._finalize_log(state, run_start)
                    return state

                # G5 ceiling check is gated on ``status != AWAITING_HUMAN``
                # rather than short-circuiting the whole loop. The earlier
                # blanket guard ("if status == AWAITING_HUMAN: return")
                # accidentally suppressed ``PHASE_MAP[AWAITING_HUMAN] =
                # HumanReviewPhase`` dispatch too, which broke the web
                # approve-flow: ``bridge._apply_user_plan_decisions`` would
                # write ``plan_human_review`` into in-memory state and wake
                # the orchestrator, but the guard returned before
                # HumanReviewPhase could observe the approval and transition
                # to AUTO_MERGING. The narrower guard below keeps the U-P2.13
                # invariant (no double-fire of the ceiling transition) while
                # letting HumanReviewPhase actually run.
                ceiling = state.config.max_cost_usd
                if ceiling is not None and state.status != SystemStatus.AWAITING_HUMAN:
                    # _prior_cost_summary holds spending from earlier processes
                    # (resume case); the in-process tracker holds spending
                    # since this Orchestrator started. Add them — never read
                    # state.cost_summary here, that field is the merged
                    # snapshot (prior + current) and would double-count.
                    prior = self._prior_cost_summary.get("total_cost_usd", 0.0)
                    spent = float(prior) + self._cost_tracker.total_cost_usd
                    if spent >= ceiling:
                        self.state_machine.transition(
                            state,
                            SystemStatus.AWAITING_HUMAN,
                            f"cost ceiling reached: ${spent:.4f} >= ${ceiling:.4f}",
                        )
                        self._snapshot_telemetry(state)
                        self.checkpoint.save(state, "cost_ceiling_halt")
                        self._finalize_log(state, run_start)
                        return state

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
                _mark_suspended_phase(state, outcome.target_status)

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
                    self._snapshot_telemetry(state)
                    self.checkpoint.save(state, outcome.checkpoint_tag)

                if outcome.extra.get("paused"):
                    self._finalize_log(state, run_start)
                    return state

        except RunBudgetExceeded as e:
            logger.warning(
                "Run budget exceeded: spent=$%.4f >= limit=$%.4f phase=%s",
                e.spent,
                e.limit,
                e.phase,
            )
            self._snapshot_telemetry(state)
            self._write_budget_exceeded_report(state, e)
            try:
                self.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    f"run budget exceeded in phase {e.phase!r}: "
                    f"spent=${e.spent:.4f} limit=${e.limit:.4f}",
                )
            except ValueError:
                # Already in AWAITING_HUMAN (e.g. concurrent ceiling check) —
                # the partial report + checkpoint tag still wins.
                pass
            self.checkpoint.save(state, "budget_exceeded")

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
            self._snapshot_telemetry(state)
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
            checkpoint=self.checkpoint,
            memory_store=self._memory_store,  # type: ignore[arg-type]
            summarizer=self._summarizer,
            memory_hit_tracker=self._memory_hit_tracker,
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
            coordinator=self._coordinator,
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
                store = store.add_entry(enforce_actionable(entry))
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

        self._phases_since_last_extract += 1
        if self.memory_extractor is not None and self._should_llm_extract(phase, state):
            try:
                llm_entries = await self.memory_extractor.extract(phase, state)  # type: ignore[attr-defined]
                store = self._memory_store
                for entry in llm_entries:
                    store = store.add_entry(enforce_actionable(entry))
                self._memory_store = store
                self._phases_since_last_extract = 0
            except Exception as exc:
                logger.warning("LLM memory extraction failed for %s: %s", phase, exc)

        if phase == "judge_review":
            try:
                self._record_memory_outcomes(state)
            except Exception as exc:
                logger.warning("Memory outcome recording failed: %s", exc)
            try:
                self._apply_outcome_confidence_writeback(state)
            except Exception as exc:
                logger.warning("Outcome confidence write-back failed: %s", exc)
            try:
                self._apply_suppress_harmful_entries(state)
            except Exception as exc:
                logger.warning("Harmful-entry suppression failed: %s", exc)

    def _record_memory_outcomes(self, state: MergeState) -> None:
        """P1-B: fuse deterministic signals into the per-file memory outcome
        that feeds OPP-5 write-back and P1-A suppression, then credit/blame the
        entries injected for each file.

        Runs once after judge_review — the verdict already reflects the
        post-judge build check. With the default ``["judge"]`` this reproduces
        the prior passed/failed split byte-for-byte. Adding ``"compile"`` demotes
        a judge-passed compiled-language file to a failure when the build check
        failed this run, so memory that produced an uncompilable merge earns no
        credit. Deterministic only — no LLM self-report."""
        verdict = state.judge_verdict
        if verdict is None:
            return
        cfg = getattr(self.config, "memory", None)
        sources = list(getattr(cfg, "writeback_signal_sources", None) or ["judge"])
        tracker = self._memory_hit_tracker
        demoted: frozenset[str] = frozenset()
        if "compile" in sources and any(
            issue.issue_type == "build_check_failed" for issue in verdict.issues
        ):
            from src.tools.compile_gate import compiled_language_paths

            demoted = frozenset(compiled_language_paths(verdict.passed_files))
        for fp in verdict.passed_files:
            tracker.record_outcome(fp, success=fp not in demoted)
        for fp in verdict.failed_files:
            tracker.record_outcome(fp, success=False)

    def _apply_outcome_confidence_writeback(self, state: MergeState) -> None:
        """OPP-5: nudge persisted memory confidence toward judge outcomes.

        Default OFF. When enabled, each tracked entry with at least
        ``min_observations`` pass/fail observations has its stored confidence
        moved by ``k * outcome_score``. Human-decided files (mirroring the
        repair-skips-human rule) and bootstrap (human-authored) entries are
        never touched, so outcome noise cannot demote a human-resolved entry.
        """
        cfg = getattr(self.config, "memory", None)
        if cfg is None or not getattr(cfg, "outcome_confidence_writeback", False):
            return
        scores = self._memory_hit_tracker.outcome_scores(
            cfg.outcome_writeback_min_observations
        )
        if not scores:
            return
        human_files = {
            fp
            for fp, record in state.file_decision_records.items()
            if record.decision_source
            in (DecisionSource.HUMAN, DecisionSource.BATCH_HUMAN)
        }
        deltas: dict[str, float] = {}
        for entry in self._memory_store.to_memory().entries:
            score = scores.get(entry.entry_id)
            if score is None:
                continue
            if _BOOTSTRAP_TAG in entry.tags:
                continue
            if human_files and any(fp in human_files for fp in entry.file_paths):
                continue
            deltas[entry.entry_id] = cfg.outcome_writeback_k * score
        if deltas:
            self._memory_store = self._memory_store.adjust_confidence(deltas)
            logger.info(
                "OPP-5: nudged confidence of %d memory entries by judge outcomes",
                len(deltas),
            )

    def _apply_suppress_harmful_entries(self, state: MergeState) -> None:
        """P1-A: persistently soft-delete stably-harmful memory entries.

        Default OFF. Persistent suppress is durable and cross-run, so its bar
        is deliberately stricter than the transient read-time O-M6 filter:
        ``suppress_harmful_threshold`` (≈ near-universal failure, not a slim
        majority) AND ``suppress_min_fail_count`` absolute fails. A
        deterministic-confound guard further skips entries whose only judged
        file association is with files that failed via a *deterministic* veto
        this run — a deterministic gate ignores memory, so blaming the injected
        entry is the PR-0d single-arm false positive (metrics §9.7). Human and
        bootstrap entries are exempt, mirroring OPP-5."""
        cfg = getattr(self.config, "memory", None)
        if cfg is None or not getattr(cfg, "persist_suppress", False):
            return
        harmful_ids = self._memory_hit_tracker.harmful_entry_ids(
            threshold=getattr(cfg, "suppress_harmful_threshold", -0.8),
            min_observations=cfg.suppress_min_observations,
            min_fail_count=getattr(cfg, "suppress_min_fail_count", 5),
        )
        if not harmful_ids:
            return
        human_files = {
            fp
            for fp, record in state.file_decision_records.items()
            if record.decision_source
            in (DecisionSource.HUMAN, DecisionSource.BATCH_HUMAN)
        }
        verdict = getattr(state, "judge_verdict", None)
        passed_files = set(verdict.passed_files) if verdict else set()
        # Files that failed via a deterministic veto this run — their failure is
        # independent of injected memory, so an entry tied only to them is a
        # correlational (not causal) "harm".
        det_fail_files = (
            {
                issue.file_path
                for issue in verdict.issues
                if issue.veto_condition and issue.file_path in set(verdict.failed_files)
            }
            if verdict
            else set()
        )
        suppressed = 0
        for entry in self._memory_store.to_memory().entries:
            if entry.entry_id not in harmful_ids or entry.suppressed:
                continue
            if _BOOTSTRAP_TAG in entry.tags:
                continue
            if human_files and any(fp in human_files for fp in entry.file_paths):
                continue
            entry_files = set(entry.file_paths)
            if (
                det_fail_files
                and entry_files & det_fail_files
                and not (entry_files & passed_files)
            ):
                continue
            self._memory_store = self._memory_store.suppress_entry(
                entry.entry_id, reason="P1-A: stably-harmful judge outcomes"
            )
            suppressed += 1
        if suppressed:
            logger.info(
                "P1-A: persistently suppressed %d stably-harmful memory entries",
                suppressed,
            )

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
        # O-M2: capture failure-mode insights after Coordinator escalations.
        if (
            getattr(cfg, "extract_on_meta_review", False)
            and state.coordinator_directives
            and any(
                d.trigger in {"judge_stall", "plan_dispute"}
                for d in state.coordinator_directives
            )
        ):
            return True
        # O-M (6.3): periodic L2 aggregation on long happy-path runs.
        every_n = getattr(cfg, "periodic_extraction_every_n_phases", 0)
        if every_n > 0 and self._phases_since_last_extract >= every_n:
            return True
        return False

    def _inject_memory(self) -> None:
        memory_cfg = getattr(self.config, "memory", None)
        # P0 ablation: when inject_enabled is False, leave each agent's store
        # at None so get_memory_context() returns "" — the "memory=off" arm.
        # Extraction/write-back still run at the orchestrator level; only
        # read-time prompt injection is suppressed.
        inject_enabled = getattr(memory_cfg, "inject_enabled", True)
        for agent in self._all_agents:
            if inject_enabled:
                agent.set_memory_store(self._memory_store)  # type: ignore[arg-type]
            agent.set_memory_hit_tracker(self._memory_hit_tracker)
            agent.set_memory_config(memory_cfg)
            agent.set_upstream_ref(self.config.upstream_ref)

    def _inject_cost_tracker(self, phase: str = "") -> None:
        for agent in self._all_agents:
            agent.set_cost_tracker(self._cost_tracker, phase=phase)
            # U2: every cost-tracked agent also gets the budget cap + the
            # activity callback so it can emit a one-shot budget_warning
            # the first time cumulative spend crosses warn_pct.
            agent.set_budget(
                self.config.max_cost_usd,
                self.config.per_run_cost_warn_pct,
                token_limit=self.config.max_total_tokens,
            )
            if self._on_activity is not None:
                agent.set_activity_callback(self._on_activity)

    def _write_budget_exceeded_report(
        self, state: MergeState, exc: RunBudgetExceeded
    ) -> None:
        """U2: drop a partial-run report when the per-run budget trips.

        Best-effort — write failures are logged but never re-raise so the
        AWAITING_HUMAN transition itself always wins.
        """
        try:
            run_dir = get_run_dir(self.config.repo_path, state.run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            report_path = run_dir / "budget_exceeded_report.md"
            report_path.write_text(
                "# Run Budget Exceeded\n\n"
                f"- run_id: `{state.run_id}`\n"
                f"- phase: `{exc.phase}`\n"
                f"- spent: ${exc.spent:.4f}\n"
                f"- limit: ${exc.limit:.4f}\n\n"
                "The orchestrator transitioned the run to "
                "`AWAITING_HUMAN` once the cumulative LLM cost reached the "
                "configured `max_cost_usd` ceiling. Resume after raising "
                "the limit or accept partial results from the checkpoint.\n",
                encoding="utf-8",
            )
        except Exception:
            logger.debug("budget_exceeded_report.md write failed", exc_info=True)

    def _snapshot_telemetry(self, state: MergeState) -> None:
        """Persist CostTracker / MemoryHitTracker summaries onto state.

        Without this, halts at AWAITING_HUMAN exit before
        report_generation runs and the run's token/cost/memory data is
        lost entirely (checkpoint shows zero LLM activity even when the
        planner + planner_judge made several calls).

        On resume the in-process CostTracker starts fresh, so summary()
        only reflects spending of the current process. Merge it with the
        prior checkpoint snapshot so cumulative totals (and the cost
        ceiling check) remain correct across resumes.
        """
        try:
            current = self._cost_tracker.summary()
            state.cost_summary = self._merge_cost_summaries(
                self._prior_cost_summary, current
            )
        except Exception:
            logger.debug("cost_tracker.summary() failed", exc_info=True)
        try:
            state.memory_summary = self._memory_hit_tracker.summary()
        except Exception:
            logger.debug("memory_hit_tracker.summary() failed", exc_info=True)

    @staticmethod
    def _merge_cost_summaries(
        prior: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        """Combine two CostTracker.summary() outputs additively.

        Used by _snapshot_telemetry to preserve spending recorded in
        prior runs (e.g. before a checkpoint resume).
        """

        def _add_scalar(a: float | int, b: float | int) -> float:
            return round(float(a) + float(b), 6)

        def _add_token_dict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, int]:
            keys = {"input", "output", "cache_read", "cache_write"}
            return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in keys}

        def _add_grouped(
            a: dict[str, dict[str, Any]],
            b: dict[str, dict[str, Any]],
        ) -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for key in set(a) | set(b):
                ae = a.get(key, {})
                be = b.get(key, {})
                row: dict[str, Any] = {}
                for field in {"calls", "cost_usd", "tokens"} | set(ae) | set(be):
                    if field == "calls":
                        row["calls"] = int(ae.get("calls", 0)) + int(be.get("calls", 0))
                    elif field == "tokens":
                        row["tokens"] = int(ae.get("tokens", 0)) + int(
                            be.get("tokens", 0)
                        )
                    elif field == "cost_usd":
                        row["cost_usd"] = _add_scalar(
                            ae.get("cost_usd", 0.0), be.get("cost_usd", 0.0)
                        )
                out[key] = row
            return out

        merged: dict[str, Any] = {
            "total_cost_usd": _add_scalar(
                prior.get("total_cost_usd", 0.0),
                current.get("total_cost_usd", 0.0),
            ),
            "total_calls": int(prior.get("total_calls", 0))
            + int(current.get("total_calls", 0)),
            "total_tokens": _add_token_dict(
                prior.get("total_tokens", {}) or {},
                current.get("total_tokens", {}) or {},
            ),
            "by_agent": _add_grouped(
                prior.get("by_agent", {}) or {},
                current.get("by_agent", {}) or {},
            ),
            "by_phase": _add_grouped(
                prior.get("by_phase", {}) or {},
                current.get("by_phase", {}) or {},
            ),
            "by_model": _add_grouped(
                prior.get("by_model", {}) or {},
                current.get("by_model", {}) or {},
            ),
        }
        return merged

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

        # Co-locate a copy of the run log next to checkpoint.json so it is
        # discoverable. The canonical log lives under the platformdirs data
        # dir (``~/Library/Application Support/...`` on macOS) — far from the
        # ``.merge/runs/<id>/`` directory users actually open. The Web UI's
        # "check the merge process logs" hint is useless without this.
        run_dir = get_run_dir(self.config.repo_path, run_id)
        if run_dir.resolve() != log_dir.resolve():
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                run_dir_handler = logging.FileHandler(
                    run_dir / "run.log", encoding="utf-8"
                )
                run_dir_handler.setFormatter(formatter)
                run_dir_handler.setLevel(logging.DEBUG)
                root.addHandler(run_dir_handler)
                self._run_dir_log_handler = run_dir_handler
            except OSError as exc:
                logger.warning("Could not open co-located run log: %s", exc)

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
        if self._run_dir_log_handler:
            root.removeHandler(self._run_dir_log_handler)
            self._run_dir_log_handler.close()
            self._run_dir_log_handler = None
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
