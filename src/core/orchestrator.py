import logging
import time
from datetime import datetime
from pathlib import Path
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus, PhaseResult
from src.models.plan import MergePhase, MergeLayer
from src.models.diff import FileDiff, RiskLevel, FileStatus
from src.models.decision import MergeDecision
from src.models.dispute import PlanDisputeRequest
from src.models.plan_judge import PlanJudgeResult
from src.models.human import HumanDecisionRequest, DecisionOption
from src.agents.planner_agent import PlannerAgent
from src.agents.planner_judge_agent import PlannerJudgeAgent
from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.agents.executor_agent import ExecutorAgent
from src.agents.judge_agent import JudgeAgent
from src.agents.human_interface_agent import HumanInterfaceAgent
from src.core.read_only_state_view import ReadOnlyStateView
from src.core.state_machine import StateMachine
from src.core.message_bus import MessageBus
from src.core.checkpoint import Checkpoint
from src.core.phase_runner import PhaseRunner
from src.tools.git_tool import GitTool
from src.tools.diff_parser import build_file_diff, detect_language
from src.tools.file_classifier import (
    compute_risk_score,
    classify_file,
    classify_all_files,
    category_summary,
    is_security_sensitive,
)
from src.models.diff import FileChangeCategory
from src.tools.report_writer import (
    write_markdown_report,
    write_json_report,
    write_plan_review_report,
    write_living_plan_report,
)
from src.tools.trace_logger import TraceLogger
from src.models.plan_review import PlanReviewRound, PlanHumanDecision
from src.models.conflict import ConflictAnalysis
from src.models.config import ThresholdConfig
from src.models.judge import VerdictType
from src.tools.gate_runner import GateRunner
from src.tools.pollution_auditor import PollutionAuditor
from src.tools.config_drift_detector import ConfigDriftDetector
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer


logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config: MergeConfig):
        self.config = config
        git_tool = GitTool(config.repo_path)

        self.planner = PlannerAgent(config.agents.planner)
        self.planner_judge = PlannerJudgeAgent(config.agents.planner_judge)
        self.conflict_analyst = ConflictAnalystAgent(
            config.agents.conflict_analyst, git_tool=git_tool
        )
        self.executor = ExecutorAgent(config.agents.executor, git_tool=git_tool)
        self.judge = JudgeAgent(config.agents.judge, git_tool=git_tool)
        self.human_interface = HumanInterfaceAgent(config.agents.human_interface)

        self.git_tool = git_tool
        self.gate_runner = GateRunner(Path(config.repo_path).resolve())
        self.state_machine = StateMachine()
        self.message_bus = MessageBus()
        self.checkpoint = Checkpoint(config.output.debug_directory)
        self.phase_runner = PhaseRunner(batch_size=10, max_concurrency=5)
        self._log_handler: logging.FileHandler | None = None
        self._trace_logger: TraceLogger | None = None

        self._all_agents = [
            self.planner,
            self.planner_judge,
            self.conflict_analyst,
            self.executor,
            self.judge,
            self.human_interface,
        ]
        self._memory_store = MemoryStore()
        self._summarizer = PhaseSummarizer()

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
            if state.status == SystemStatus.INITIALIZED:
                t0 = time.monotonic()
                await self._initialize(state)
                logger.info(
                    "Phase INITIALIZE completed in %.1fs — %d files collected",
                    time.monotonic() - t0,
                    len(getattr(state, "_file_diffs", []) or []),
                )

            if state.status == SystemStatus.PLANNING:
                t0 = time.monotonic()
                await self._run_phase1(state)
                self._update_memory("planning", state)
                self._inject_memory()
                self.checkpoint.save(state, "after_phase1")
                logger.info("Phase PLANNING completed in %.1fs", time.monotonic() - t0)

            if state.status == SystemStatus.PLAN_REVIEWING:
                t0 = time.monotonic()
                await self._run_phase1_5(state)
                self.checkpoint.save(state, "after_phase1_5")
                logger.info(
                    "Phase PLAN_REVIEW completed in %.1fs", time.monotonic() - t0
                )

            if state.status == SystemStatus.AUTO_MERGING:
                t0 = time.monotonic()
                await self._run_phase2(state)
                self._update_memory("auto_merge", state)
                self._inject_memory()
                self.checkpoint.save(state, "after_phase2")
                logger.info(
                    "Phase AUTO_MERGE completed in %.1fs", time.monotonic() - t0
                )

            if state.status == SystemStatus.ANALYZING_CONFLICTS:
                t0 = time.monotonic()
                await self._run_phase3(state)
                self._update_memory("conflict_analysis", state)
                self._inject_memory()
                self.checkpoint.save(state, "after_phase3")
                logger.info(
                    "Phase CONFLICT_ANALYSIS completed in %.1fs",
                    time.monotonic() - t0,
                )

            if state.status == SystemStatus.AWAITING_HUMAN:
                logger.info("Entering AWAITING_HUMAN status")
                if state.plan_human_review is not None:
                    write_plan_review_report(state, self.config.output.directory)
                    if state.plan_human_review.decision == PlanHumanDecision.APPROVE:
                        self.state_machine.transition(
                            state,
                            SystemStatus.AUTO_MERGING,
                            "plan approved by human reviewer",
                        )
                    elif state.plan_human_review.decision == PlanHumanDecision.REJECT:
                        self.state_machine.transition(
                            state,
                            SystemStatus.FAILED,
                            "plan rejected by human reviewer",
                        )
                    else:
                        self.checkpoint.save(state, "awaiting_human")
                        self._finalize_log(state, run_start)
                        return state
                else:
                    self.checkpoint.save(state, "awaiting_human")
                    self._finalize_log(state, run_start)
                    return state

            if state.status == SystemStatus.JUDGE_REVIEWING:
                t0 = time.monotonic()
                await self._run_phase5(state)
                self._update_memory("judge_review", state)
                self.checkpoint.save(state, "after_phase5")
                logger.info(
                    "Phase JUDGE_REVIEW completed in %.1fs", time.monotonic() - t0
                )

            if state.status == SystemStatus.GENERATING_REPORT:
                t0 = time.monotonic()
                await self._run_phase6(state)
                self.checkpoint.save(state, "completed")
                logger.info("Phase REPORT completed in %.1fs", time.monotonic() - t0)

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

    async def _initialize(self, state: MergeState) -> None:
        merge_base = self.git_tool.get_merge_base(
            state.config.upstream_ref, state.config.fork_ref
        )
        object.__setattr__(state, "_merge_base", merge_base)
        state.merge_base_commit = merge_base

        file_categories = classify_all_files(
            merge_base,
            state.config.fork_ref,
            state.config.upstream_ref,
            self.git_tool,
        )

        auditor = PollutionAuditor(self.git_tool)
        pollution_report = auditor.audit(
            merge_base,
            state.config.fork_ref,
            state.config.upstream_ref,
            file_categories,
        )
        state.pollution_audit = pollution_report
        if pollution_report.has_pollution:
            logger.info(
                "Pollution audit: %d files reclassified from %d prior merge commits",
                pollution_report.reclassified_count,
                len(pollution_report.prior_merge_commits),
            )
            file_categories = auditor.apply_corrections(
                file_categories, pollution_report
            )

        state.file_categories = file_categories

        cat_counts = category_summary(file_categories)
        logger.info(
            "Three-way classification: A=%d B=%d C=%d D-missing=%d D-extra=%d E=%d",
            cat_counts.get("unchanged", 0),
            cat_counts.get("upstream_only", 0),
            cat_counts.get("both_changed", 0),
            cat_counts.get("upstream_new", 0),
            cat_counts.get("current_only", 0),
            cat_counts.get("current_only_change", 0),
        )

        actionable_categories = {
            FileChangeCategory.B,
            FileChangeCategory.C,
            FileChangeCategory.D_MISSING,
        }
        actionable_paths = {
            fp for fp, cat in file_categories.items() if cat in actionable_categories
        }

        changed_files = self.git_tool.get_changed_files(
            merge_base, state.config.fork_ref
        )
        file_diffs: list[FileDiff] = []

        changed_paths_map: dict[str, str] = {fp: sc for sc, fp in changed_files}

        for file_path in sorted(actionable_paths):
            status_char = changed_paths_map.get(file_path, "M")
            cat = file_categories[file_path]

            if cat == FileChangeCategory.D_MISSING:
                file_status = FileStatus.ADDED
                raw_diff = ""
            else:
                raw_diff = self.git_tool.get_unified_diff(
                    merge_base, state.config.fork_ref, file_path
                )
                file_status = _parse_file_status(status_char)

            language = detect_language(file_path)
            fd = build_file_diff(file_path, raw_diff, file_status)
            sensitive = is_security_sensitive(file_path, state.config.file_classifier)
            fd = fd.model_copy(
                update={
                    "language": language,
                    "is_security_sensitive": sensitive,
                    "change_category": cat,
                }
            )
            score = compute_risk_score(fd, state.config.file_classifier)
            fd = fd.model_copy(update={"risk_score": score})
            risk_level = classify_file(fd, state.config.file_classifier)
            fd = fd.model_copy(update={"risk_level": risk_level})
            file_diffs.append(fd)

        object.__setattr__(state, "_file_diffs", file_diffs)

        drift_detector = ConfigDriftDetector(Path(state.config.repo_path).resolve())
        env_files, docker_env_files = drift_detector.find_env_files()
        if env_files or docker_env_files:
            drift_report = drift_detector.detect_drift_from_files(
                env_files=env_files,
                docker_env_files=docker_env_files,
            )
            state.config_drifts = drift_report
            if drift_report.has_drifts:
                logger.info(
                    "Config drift detection: %d drifts found across %d keys",
                    drift_report.drift_count,
                    drift_report.total_keys_checked,
                )

        self.state_machine.transition(
            state, SystemStatus.PLANNING, "initialization complete"
        )

    async def _run_phase1(self, state: MergeState) -> None:
        state.current_phase = MergePhase.ANALYSIS
        phase_result = PhaseResult(
            phase=MergePhase.ANALYSIS,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.ANALYSIS.value] = phase_result

        try:
            await self.planner.run(state)
            phase_result = phase_result.model_copy(
                update={"status": "completed", "completed_at": datetime.now()}
            )
            state.phase_results[MergePhase.ANALYSIS.value] = phase_result
            self.state_machine.transition(
                state, SystemStatus.PLAN_REVIEWING, "phase 1 complete"
            )
        except Exception as e:
            phase_result = phase_result.model_copy(
                update={"status": "failed", "error": str(e)}
            )
            state.phase_results[MergePhase.ANALYSIS.value] = phase_result
            raise

    async def _run_phase1_5(self, state: MergeState) -> None:
        state.current_phase = MergePhase.PLAN_REVIEW
        phase_result = PhaseResult(
            phase=MergePhase.PLAN_REVIEW,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result

        file_diffs: list[FileDiff] = getattr(state, "_file_diffs", []) or []

        for round_num in range(self.config.max_plan_revision_rounds + 1):
            state.plan_revision_rounds = round_num

            assert state.merge_plan is not None
            verdict = await self.planner_judge.review_plan(
                state.merge_plan, file_diffs, round_num
            )
            state.plan_judge_verdict = verdict

            round_log = PlanReviewRound(
                round_number=round_num,
                verdict_result=verdict.result,
                verdict_summary=verdict.summary,
                issues_count=len(verdict.issues),
                issues_detail=[
                    {
                        "file_path": issue.file_path,
                        "reason": issue.reason,
                        "current": issue.current_classification.value
                        if hasattr(issue.current_classification, "value")
                        else str(issue.current_classification),
                        "suggested": issue.suggested_classification.value
                        if hasattr(issue.suggested_classification, "value")
                        else str(issue.suggested_classification),
                    }
                    for issue in verdict.issues
                ],
            )

            if verdict.result == PlanJudgeResult.APPROVED:
                state.plan_review_log.append(round_log)
                phase_result = phase_result.model_copy(
                    update={"status": "completed", "completed_at": datetime.now()}
                )
                state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result
                write_plan_review_report(state, self.config.output.directory)
                logger.info(
                    "Plan approved by judge — awaiting human review before proceeding"
                )
                self.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    "plan approved by judge, awaiting human review",
                )
                return

            elif verdict.result == PlanJudgeResult.CRITICAL_REPLAN:
                state.plan_review_log.append(round_log)
                self.state_machine.transition(
                    state, SystemStatus.PLANNING, "critical replan required"
                )
                await self._run_phase1(state)
                return

            elif round_num < self.config.max_plan_revision_rounds:
                self.state_machine.transition(
                    state,
                    SystemStatus.PLAN_REVISING,
                    f"revision needed (round {round_num + 1}/{self.config.max_plan_revision_rounds})",
                )
                state.current_phase = MergePhase.PLAN_REVISING
                revised_plan = await self.planner.revise_plan(state, verdict.issues)
                round_log = round_log.model_copy(
                    update={
                        "planner_revision_summary": (
                            f"Revised plan with {len(verdict.issues)} issues addressed"
                        )
                    }
                )
                state.plan_review_log.append(round_log)
                state.merge_plan = revised_plan
                state.file_classifications = {
                    fp: batch.risk_level
                    for batch in revised_plan.phases
                    for fp in batch.file_paths
                }
                self.state_machine.transition(
                    state, SystemStatus.PLAN_REVIEWING, "revision complete"
                )
                state.current_phase = MergePhase.PLAN_REVIEW
            else:
                state.plan_review_log.append(round_log)
                phase_result = phase_result.model_copy(
                    update={"status": "completed", "completed_at": datetime.now()}
                )
                state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result
                write_plan_review_report(state, self.config.output.directory)
                self.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    "plan review exceeded max revision rounds",
                )
                return

    async def _run_phase2(self, state: MergeState) -> None:
        state.current_phase = MergePhase.AUTO_MERGE
        phase_result = PhaseResult(
            phase=MergePhase.AUTO_MERGE,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.AUTO_MERGE.value] = phase_result

        if state.merge_plan is None:
            raise ValueError("No merge plan available for phase 2")

        file_diffs_map: dict[str, FileDiff] = {}
        for fd in getattr(state, "_file_diffs", None) or []:
            file_diffs_map[fd.file_path] = fd

        batch_count = 0
        completed_layers: set[int] = set()
        layer_index = self._build_layer_index(state)

        for batch in state.merge_plan.phases:
            if batch.risk_level not in (RiskLevel.AUTO_SAFE, RiskLevel.DELETED_ONLY):
                continue

            if batch.layer_id is not None:
                deps_ok = self._verify_layer_deps(
                    batch.layer_id, completed_layers, state
                )
                if not deps_ok:
                    logger.warning(
                        "Skipping batch %s (layer %d): dependencies not met",
                        batch.batch_id,
                        batch.layer_id,
                    )
                    continue

            for file_path in batch.file_paths:
                category = batch.change_category
                if category is None:
                    fd = file_diffs_map.get(file_path)
                    category = fd.change_category if fd else None

                if category == FileChangeCategory.D_MISSING:
                    record = await self.executor._copy_from_upstream(file_path, state)
                    state.file_decision_records[file_path] = record
                    batch_count += 1
                    continue

                fd = file_diffs_map.get(file_path)
                if fd is None:
                    continue

                strategy = self.executor._select_strategy_by_category(
                    category, batch.risk_level
                )
                record = await self.executor.execute_auto_merge(fd, strategy, state)
                state.file_decision_records[file_path] = record
                batch_count += 1

                if batch_count % 10 == 0:
                    self.checkpoint.save(state, f"phase2_batch_{batch_count}")

            if batch.layer_id is not None and batch.layer_id not in completed_layers:
                completed_layers.add(batch.layer_id)
                layer_gates = self._get_layer_gates(batch.layer_id, layer_index)
                if layer_gates:
                    gate_ok = await self._run_gates(
                        state, f"layer_{batch.layer_id}", layer_gates
                    )
                    if not gate_ok:
                        gate_blocked = await self._handle_gate_failure(state)
                        if gate_blocked:
                            return

        gate_ok = await self._run_gates(state, "auto_merge")
        if not gate_ok:
            gate_blocked = await self._handle_gate_failure(state)
            if gate_blocked:
                return

        has_risky = any(
            batch.risk_level in (RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY)
            for batch in state.merge_plan.phases
        )

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.AUTO_MERGE.value] = phase_result

        self._append_execution_record(state, "auto_merge", phase_result, batch_count)

        if state.plan_disputes:
            self.state_machine.transition(
                state, SystemStatus.PLAN_DISPUTE_PENDING, "executor raised plan dispute"
            )
            await self._handle_plan_dispute(state, state.plan_disputes[-1])
        elif has_risky:
            self.state_machine.transition(
                state,
                SystemStatus.ANALYZING_CONFLICTS,
                "proceeding to conflict analysis",
            )
        else:
            self.state_machine.transition(
                state,
                SystemStatus.JUDGE_REVIEWING,
                "no risky files, skip to judge review",
            )

    async def _run_phase3(self, state: MergeState) -> None:
        state.current_phase = MergePhase.CONFLICT_ANALYSIS
        phase_result = PhaseResult(
            phase=MergePhase.CONFLICT_ANALYSIS,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.CONFLICT_ANALYSIS.value] = phase_result

        await self.conflict_analyst.run(state)

        file_diffs_map: dict[str, FileDiff] = {}
        for fd in getattr(state, "_file_diffs", None) or []:
            file_diffs_map[fd.file_path] = fd

        needs_human: list[str] = []
        for file_path, analysis in state.conflict_analyses.items():
            fd = file_diffs_map.get(file_path)
            if fd is None:
                continue

            strategy = _select_merge_strategy(analysis, state.config.thresholds)

            if strategy == MergeDecision.ESCALATE_HUMAN:
                needs_human.append(file_path)
                req = _build_human_decision_request(fd, analysis)
                state.human_decision_requests[file_path] = req
            elif strategy == MergeDecision.SEMANTIC_MERGE:
                record = await self.executor.execute_semantic_merge(fd, analysis, state)
                state.file_decision_records[file_path] = record
                self.checkpoint.save(state, f"phase3_{file_path.replace('/', '_')}")
            else:
                record = await self.executor.execute_auto_merge(fd, strategy, state)
                state.file_decision_records[file_path] = record

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.CONFLICT_ANALYSIS.value] = phase_result

        if needs_human:
            self.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                f"{len(needs_human)} files need human review",
            )
        else:
            self.state_machine.transition(
                state, SystemStatus.JUDGE_REVIEWING, "conflict analysis complete"
            )

    async def _run_phase5(self, state: MergeState) -> None:
        state.current_phase = MergePhase.JUDGE_REVIEW
        phase_result = PhaseResult(
            phase=MergePhase.JUDGE_REVIEW,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.JUDGE_REVIEW.value] = phase_result

        max_rounds = self.config.max_judge_repair_rounds
        state.judge_repair_rounds = 0

        for round_num in range(max_rounds):
            state.judge_repair_rounds = round_num

            readonly = ReadOnlyStateView(state)
            msg = await self.judge.run(readonly)
            verdict_data = msg.payload.get("verdict")
            if verdict_data:
                from src.models.judge import JudgeVerdict as JV

                state.judge_verdict = JV.model_validate(verdict_data)

            customization_violations = self.judge.verify_customizations(
                self.config.customizations
            )
            if state.judge_verdict and customization_violations:
                state.judge_verdict = state.judge_verdict.model_copy(
                    update={
                        "customization_violations": customization_violations,
                        "veto_triggered": True,
                        "veto_reason": (
                            f"Customization(s) lost: "
                            f"{', '.join(v.customization_name for v in customization_violations)}"
                        ),
                        "verdict": VerdictType.FAIL,
                    }
                )

            state.judge_verdicts_log.append(
                {
                    "round": round_num,
                    "verdict": state.judge_verdict.verdict.value
                    if state.judge_verdict
                    else "none",
                    "timestamp": datetime.now().isoformat(),
                    "issues_count": len(state.judge_verdict.issues)
                    if state.judge_verdict
                    else 0,
                    "veto": state.judge_verdict.veto_triggered
                    if state.judge_verdict
                    else False,
                }
            )

            self._append_judge_record(state, round_num)

            if state.judge_verdict is None:
                break

            if state.judge_verdict.verdict == VerdictType.PASS:
                logger.info("Judge PASS on round %d", round_num)
                break

            if state.judge_verdict.veto_triggered:
                logger.warning(
                    "Judge VETO on round %d: %s",
                    round_num,
                    state.judge_verdict.veto_reason,
                )
                break

            repair_instructions = self.judge.build_repair_instructions(
                state.judge_verdict.issues
            )
            state.judge_verdict = state.judge_verdict.model_copy(
                update={"repair_instructions": repair_instructions}
            )

            repairable = [r for r in repair_instructions if r.is_repairable]
            if not repairable:
                logger.info("No repairable issues on round %d, escalating", round_num)
                break

            if round_num < max_rounds - 1:
                logger.info(
                    "Repair round %d/%d: %d instructions",
                    round_num + 1,
                    max_rounds,
                    len(repairable),
                )
                await self.executor.repair(repairable, state)
                self.checkpoint.save(state, f"phase5_repair_{round_num}")

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.JUDGE_REVIEW.value] = phase_result

        gate_ok = await self._run_gates(state, "judge_review")
        if not gate_ok:
            gate_blocked = await self._handle_gate_failure(state)
            if gate_blocked:
                return

        if state.judge_verdict is None:
            self.state_machine.transition(
                state,
                SystemStatus.GENERATING_REPORT,
                "judge review complete (no verdict)",
            )
            return

        verdict_type = state.judge_verdict.verdict
        if verdict_type == VerdictType.PASS:
            self.state_machine.transition(
                state, SystemStatus.GENERATING_REPORT, "judge verdict: PASS"
            )
        elif state.judge_verdict.veto_triggered:
            self.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                f"judge VETO: {state.judge_verdict.veto_reason}",
            )
        elif verdict_type == VerdictType.CONDITIONAL:
            self.state_machine.transition(
                state, SystemStatus.AWAITING_HUMAN, "judge verdict: CONDITIONAL"
            )
        else:
            self.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                f"judge verdict: FAIL after {state.judge_repair_rounds + 1} rounds",
            )

    async def _run_gates(
        self,
        state: MergeState,
        phase_name: str,
        layer_gates: list[object] | None = None,
    ) -> bool:
        from src.models.config import GateCommandConfig

        gates: list[GateCommandConfig] = []
        if layer_gates:
            for gate in layer_gates:
                if isinstance(gate, GateCommandConfig):
                    gates.append(gate)
                elif isinstance(gate, dict):
                    gates.append(GateCommandConfig(**gate))

        if not gates:
            gates = list(self.config.gate.commands)

        if not self.config.gate.enabled or not gates:
            return True

        report = await self.gate_runner.run_all_gates(
            gates,
            state.gate_baselines or None,
        )

        gate_entry = {
            "phase": phase_name,
            "timestamp": datetime.now().isoformat(),
            "all_passed": report.all_passed,
            "results": [r.model_dump(mode="json") for r in report.results],
        }
        state.gate_history.append(gate_entry)
        self._append_gate_record(state, phase_name, gate_entry)

        if report.all_passed:
            state.consecutive_gate_failures = 0
            return True

        state.consecutive_gate_failures += 1
        failed_names = [r.gate_name for r in report.results if not r.passed]
        logger.warning(
            "Gate check failed for %s: %s (consecutive: %d/%d)",
            phase_name,
            failed_names,
            state.consecutive_gate_failures,
            self.config.gate.max_consecutive_failures,
        )
        return False

    async def _handle_gate_failure(self, state: MergeState) -> bool:
        if state.consecutive_gate_failures >= self.config.gate.max_consecutive_failures:
            logger.error(
                "Gate consecutive failures (%d) reached limit (%d), escalating to human",
                state.consecutive_gate_failures,
                self.config.gate.max_consecutive_failures,
            )
            self.state_machine.transition(
                state,
                SystemStatus.AWAITING_HUMAN,
                f"gate failures exceeded limit ({state.consecutive_gate_failures}/"
                f"{self.config.gate.max_consecutive_failures})",
            )
            return True
        return False

    def _verify_layer_deps(
        self,
        layer_id: int,
        completed_layers: set[int],
        state: MergeState,
    ) -> bool:
        if state.merge_plan is None or not state.merge_plan.layers:
            return True
        for layer in state.merge_plan.layers:
            if layer.layer_id == layer_id:
                missing = [
                    dep for dep in layer.depends_on if dep not in completed_layers
                ]
                if missing:
                    logger.warning(
                        "Layer %d (%s) blocked by incomplete dependencies: %s",
                        layer_id,
                        layer.name,
                        missing,
                    )
                    return False
                return True
        return True

    def _build_layer_index(self, state: MergeState) -> dict[int, MergeLayer]:
        if state.merge_plan is None or not state.merge_plan.layers:
            return {}
        return {layer.layer_id: layer for layer in state.merge_plan.layers}

    def _get_layer_gates(
        self,
        layer_id: int,
        layer_index: dict[int, MergeLayer],
    ) -> list[object] | None:
        layer = layer_index.get(layer_id)
        if layer is None or not layer.gate_commands:
            return None
        return list(layer.gate_commands)

    def _append_execution_record(
        self,
        state: MergeState,
        phase_id: str,
        phase_result: PhaseResult,
        files_processed: int,
    ) -> None:
        from src.models.plan import MergePlanLive, PhaseExecutionRecord

        if not isinstance(state.merge_plan, MergePlanLive):
            return

        state.merge_plan.execution_records.append(
            PhaseExecutionRecord(
                phase_id=phase_id,
                started_at=phase_result.started_at or datetime.now(),
                completed_at=phase_result.completed_at,
                files_processed=files_processed,
            )
        )

    def _append_judge_record(self, state: MergeState, round_number: int) -> None:
        from src.models.plan import MergePlanLive, PhaseJudgeRecord

        if not isinstance(state.merge_plan, MergePlanLive):
            return

        verdict = state.judge_verdict
        if verdict is None:
            return

        state.merge_plan.judge_records.append(
            PhaseJudgeRecord(
                phase_id="judge_review",
                round_number=round_number,
                verdict=verdict.verdict.value
                if hasattr(verdict.verdict, "value")
                else str(verdict.verdict),
                issues=[
                    {"file": i.file_path, "type": i.issue_type}
                    for i in verdict.issues[:20]
                ],
                veto_triggered=verdict.veto_triggered,
                repair_instructions=[
                    r.instruction for r in verdict.repair_instructions[:10]
                ],
            )
        )

    def _append_gate_record(
        self, state: MergeState, phase_id: str, gate_history_entry: dict[str, object]
    ) -> None:
        from src.models.plan import MergePlanLive, PhaseGateRecord

        if not isinstance(state.merge_plan, MergePlanLive):
            return

        results_raw = gate_history_entry.get("results", [])
        results = list(results_raw) if isinstance(results_raw, list) else []

        state.merge_plan.gate_records.append(
            PhaseGateRecord(
                phase_id=phase_id,
                gate_results=results,
                all_passed=bool(gate_history_entry.get("all_passed", False)),
            )
        )

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

    async def _run_phase6(self, state: MergeState) -> None:
        state.current_phase = MergePhase.REPORT
        phase_result = PhaseResult(
            phase=MergePhase.REPORT,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.REPORT.value] = phase_result

        output_dir = state.config.output.directory

        try:
            if "json" in state.config.output.formats:
                write_json_report(state, output_dir)
            if "markdown" in state.config.output.formats:
                write_markdown_report(state, output_dir)

            write_living_plan_report(state, output_dir)

            phase_result = phase_result.model_copy(
                update={"status": "completed", "completed_at": datetime.now()}
            )
            state.phase_results[MergePhase.REPORT.value] = phase_result
            self.state_machine.transition(
                state, SystemStatus.COMPLETED, "reports generated"
            )
        except Exception as e:
            state.errors.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "report",
                    "message": f"Report generation failed (non-blocking): {e}",
                }
            )
            phase_result = phase_result.model_copy(
                update={"status": "completed", "error": str(e)}
            )
            state.phase_results[MergePhase.REPORT.value] = phase_result
            self.state_machine.transition(
                state, SystemStatus.COMPLETED, "reports failed but marking complete"
            )

    async def _handle_plan_dispute(
        self, state: MergeState, dispute: PlanDisputeRequest
    ) -> None:
        try:
            self.state_machine.transition(
                state, SystemStatus.PLAN_REVISING, f"dispute: {dispute.dispute_reason}"
            )
            revised_plan = await self.planner.handle_dispute(state, dispute)
            state.merge_plan = revised_plan

            file_diffs: list[FileDiff] = getattr(state, "_file_diffs", []) or []
            self.state_machine.transition(
                state, SystemStatus.PLAN_REVIEWING, "dispute revision complete"
            )

            verdict = await self.planner_judge.review_plan(revised_plan, file_diffs, 0)
            state.plan_judge_verdict = verdict

            if verdict.result == PlanJudgeResult.APPROVED:
                dispute.resolved = True
                dispute.resolution_summary = "Plan revised and approved after dispute"
                self.state_machine.transition(
                    state, SystemStatus.AUTO_MERGING, "dispute resolved, plan approved"
                )
            else:
                self.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    "dispute could not be resolved automatically",
                )
        except Exception as e:
            logger.error(f"Plan dispute handling failed: {e}")
            self.state_machine.transition(
                state, SystemStatus.AWAITING_HUMAN, f"dispute handling error: {e}"
            )


def _parse_file_status(status_char: str) -> FileStatus:
    mapping = {
        "A": FileStatus.ADDED,
        "M": FileStatus.MODIFIED,
        "D": FileStatus.DELETED,
        "R": FileStatus.RENAMED,
    }
    return mapping.get(status_char.upper(), FileStatus.MODIFIED)


def _select_merge_strategy(
    analysis: ConflictAnalysis, thresholds: ThresholdConfig
) -> MergeDecision:
    from src.models.conflict import ConflictType

    if analysis.confidence < thresholds.human_escalation:
        return MergeDecision.ESCALATE_HUMAN

    if analysis.conflict_type == ConflictType.LOGIC_CONTRADICTION:
        if analysis.confidence < 0.90:
            return MergeDecision.ESCALATE_HUMAN

    if analysis.conflict_type == ConflictType.SEMANTIC_EQUIVALENT:
        if analysis.confidence >= thresholds.auto_merge_confidence:
            return MergeDecision.TAKE_TARGET

    if analysis.can_coexist and analysis.confidence >= thresholds.auto_merge_confidence:
        return MergeDecision.SEMANTIC_MERGE

    if analysis.is_security_sensitive:
        return MergeDecision.ESCALATE_HUMAN

    if analysis.confidence >= thresholds.auto_merge_confidence:
        return analysis.recommended_strategy

    return MergeDecision.ESCALATE_HUMAN


def _build_human_decision_request(
    fd: FileDiff, analysis: ConflictAnalysis
) -> HumanDecisionRequest:
    from datetime import datetime

    rec_val = analysis.recommended_strategy

    options = [
        DecisionOption(
            option_key="A",
            decision=MergeDecision.TAKE_CURRENT,
            description="Keep fork (current) version",
        ),
        DecisionOption(
            option_key="B",
            decision=MergeDecision.TAKE_TARGET,
            description="Take upstream (target) version",
        ),
        DecisionOption(
            option_key="C",
            decision=MergeDecision.SEMANTIC_MERGE,
            description="Attempt semantic merge",
        ),
        DecisionOption(
            option_key="D",
            decision=MergeDecision.MANUAL_PATCH,
            description="Provide custom content",
        ),
    ]

    return HumanDecisionRequest(
        file_path=fd.file_path,
        priority=1 if fd.is_security_sensitive else 5,
        conflict_points=analysis.conflict_points,
        context_summary=f"File {fd.file_path} has conflicts requiring human review",
        upstream_change_summary=f"Upstream added {fd.lines_added} lines",
        fork_change_summary=f"Fork deleted {fd.lines_deleted} lines",
        analyst_recommendation=rec_val,
        analyst_confidence=analysis.confidence,
        analyst_rationale=analysis.rationale,
        options=options,
        created_at=datetime.now(),
    )
