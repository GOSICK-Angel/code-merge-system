import logging
from datetime import datetime
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus, PhaseResult
from src.models.plan import MergePhase
from src.models.diff import FileDiff, RiskLevel, FileStatus
from src.models.decision import MergeDecision, FileDecisionRecord, DecisionSource
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
from src.tools.file_classifier import compute_risk_score, classify_file, is_security_sensitive
from src.tools.report_writer import write_markdown_report, write_json_report


logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config: MergeConfig):
        self.config = config
        git_tool = GitTool(config.repo_path)

        self.planner = PlannerAgent(config.agents.planner)
        self.planner_judge = PlannerJudgeAgent(config.agents.planner_judge)
        self.conflict_analyst = ConflictAnalystAgent(config.agents.conflict_analyst, git_tool=git_tool)
        self.executor = ExecutorAgent(config.agents.executor, git_tool=git_tool)
        self.judge = JudgeAgent(config.agents.judge, git_tool=git_tool)
        self.human_interface = HumanInterfaceAgent(config.agents.human_interface)

        self.git_tool = git_tool
        self.state_machine = StateMachine()
        self.message_bus = MessageBus()
        self.checkpoint = Checkpoint(config.output.directory)
        self.phase_runner = PhaseRunner(batch_size=10, max_concurrency=5)

    async def run(self, state: MergeState) -> MergeState:
        self.checkpoint.register_signal_handler(state)

        try:
            if state.status == SystemStatus.INITIALIZED:
                await self._initialize(state)

            if state.status == SystemStatus.PLANNING:
                await self._run_phase1(state)
                self.checkpoint.save(state, "after_phase1")

            if state.status == SystemStatus.PLAN_REVIEWING:
                await self._run_phase1_5(state)
                self.checkpoint.save(state, "after_phase1_5")

            if state.status == SystemStatus.AUTO_MERGING:
                await self._run_phase2(state)
                self.checkpoint.save(state, "after_phase2")

            if state.status == SystemStatus.ANALYZING_CONFLICTS:
                await self._run_phase3(state)
                self.checkpoint.save(state, "after_phase3")

            if state.status == SystemStatus.AWAITING_HUMAN:
                self.checkpoint.save(state, "awaiting_human")
                return state

            if state.status == SystemStatus.JUDGE_REVIEWING:
                await self._run_phase5(state)
                self.checkpoint.save(state, "after_phase5")

            if state.status == SystemStatus.GENERATING_REPORT:
                await self._run_phase6(state)
                self.checkpoint.save(state, "completed")

        except Exception as e:
            logger.error(f"Orchestration failed: {e}", exc_info=True)
            state.errors.append({
                "timestamp": datetime.now().isoformat(),
                "phase": state.current_phase.value if hasattr(state.current_phase, "value") else str(state.current_phase),
                "message": str(e),
            })
            try:
                self.state_machine.transition(state, SystemStatus.FAILED, str(e))
            except ValueError:
                pass
            self.checkpoint.save(state, "failed")

        return state

    async def _initialize(self, state: MergeState) -> None:
        merge_base = self.git_tool.get_merge_base(
            state.config.upstream_ref, state.config.fork_ref
        )
        object.__setattr__(state, "_merge_base", merge_base)

        changed_files = self.git_tool.get_changed_files(merge_base, state.config.fork_ref)
        file_diffs: list[FileDiff] = []

        for status_char, file_path in changed_files:
            raw_diff = self.git_tool.get_unified_diff(merge_base, state.config.fork_ref, file_path)
            file_status = _parse_file_status(status_char)
            language = detect_language(file_path)

            fd = build_file_diff(file_path, raw_diff, file_status)
            sensitive = is_security_sensitive(file_path, state.config.file_classifier)
            fd = fd.model_copy(
                update={
                    "language": language,
                    "is_security_sensitive": sensitive,
                }
            )
            score = compute_risk_score(fd, state.config.file_classifier)
            fd = fd.model_copy(update={"risk_score": score})
            risk_level = classify_file(fd, state.config.file_classifier)
            fd = fd.model_copy(update={"risk_level": risk_level})
            file_diffs.append(fd)

        object.__setattr__(state, "_file_diffs", file_diffs)
        self.state_machine.transition(state, SystemStatus.PLANNING, "initialization complete")

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
            self.state_machine.transition(state, SystemStatus.PLAN_REVIEWING, "phase 1 complete")
        except Exception as e:
            phase_result = phase_result.model_copy(update={"status": "failed", "error": str(e)})
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
            readonly = ReadOnlyStateView(state)

            verdict = await self.planner_judge.review_plan(
                state.merge_plan, file_diffs, round_num
            )
            state.plan_judge_verdict = verdict

            if verdict.result == PlanJudgeResult.APPROVED:
                phase_result = phase_result.model_copy(
                    update={"status": "completed", "completed_at": datetime.now()}
                )
                state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result
                self.state_machine.transition(state, SystemStatus.AUTO_MERGING, "plan approved")
                return

            elif verdict.result == PlanJudgeResult.CRITICAL_REPLAN:
                self.state_machine.transition(state, SystemStatus.PLANNING, "critical replan required")
                await self._run_phase1(state)
                return

            elif round_num < self.config.max_plan_revision_rounds:
                self.state_machine.transition(
                    state, SystemStatus.PLAN_REVISING,
                    f"revision needed (round {round_num + 1}/{self.config.max_plan_revision_rounds})"
                )
                state.current_phase = MergePhase.PLAN_REVISING
                revised_plan = await self.planner.revise_plan(state, verdict.issues)
                state.merge_plan = revised_plan
                state.file_classifications = {
                    fp: batch.risk_level
                    for batch in revised_plan.phases
                    for fp in batch.file_paths
                }
                self.state_machine.transition(state, SystemStatus.PLAN_REVIEWING, "revision complete")
                state.current_phase = MergePhase.PLAN_REVIEW
            else:
                phase_result = phase_result.model_copy(
                    update={"status": "completed", "completed_at": datetime.now()}
                )
                state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result
                self.state_machine.transition(
                    state, SystemStatus.AWAITING_HUMAN,
                    "plan review exceeded max revision rounds"
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
        for fd in (getattr(state, "_file_diffs", None) or []):
            file_diffs_map[fd.file_path] = fd

        auto_safe_files: list[str] = []
        for batch in state.merge_plan.phases:
            if batch.risk_level in (RiskLevel.AUTO_SAFE, RiskLevel.DELETED_ONLY):
                auto_safe_files.extend(batch.file_paths)

        batch_count = 0
        for file_path in auto_safe_files:
            fd = file_diffs_map.get(file_path)
            if fd is None:
                continue

            strategy = MergeDecision.TAKE_TARGET
            if fd.risk_level == RiskLevel.DELETED_ONLY:
                strategy = MergeDecision.SKIP

            record = await self.executor.execute_auto_merge(fd, strategy, state)
            state.file_decision_records[file_path] = record

            batch_count += 1
            if batch_count % 10 == 0:
                self.checkpoint.save(state, f"phase2_batch_{batch_count}")

        has_risky = any(
            batch.risk_level in (RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY)
            for batch in state.merge_plan.phases
        )

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.AUTO_MERGE.value] = phase_result

        if state.plan_disputes:
            self.state_machine.transition(
                state, SystemStatus.PLAN_DISPUTE_PENDING, "executor raised plan dispute"
            )
            await self._handle_plan_dispute(state, state.plan_disputes[-1])
        elif has_risky:
            self.state_machine.transition(
                state, SystemStatus.ANALYZING_CONFLICTS, "proceeding to conflict analysis"
            )
        else:
            self.state_machine.transition(
                state, SystemStatus.JUDGE_REVIEWING, "no risky files, skip to judge review"
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
        for fd in (getattr(state, "_file_diffs", None) or []):
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
                state, SystemStatus.AWAITING_HUMAN, f"{len(needs_human)} files need human review"
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

        readonly = ReadOnlyStateView(state)
        msg = await self.judge.run(readonly)
        verdict = msg.payload.get("verdict")
        if verdict:
            from src.models.judge import JudgeVerdict
            state.judge_verdict = JudgeVerdict.model_validate(verdict)

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.JUDGE_REVIEW.value] = phase_result

        if state.judge_verdict is None:
            self.state_machine.transition(
                state, SystemStatus.GENERATING_REPORT, "judge review complete (no verdict)"
            )
            return

        from src.models.judge import VerdictType
        verdict_type = state.judge_verdict.verdict
        if verdict_type == VerdictType.PASS:
            self.state_machine.transition(
                state, SystemStatus.GENERATING_REPORT, "judge verdict: PASS"
            )
        elif verdict_type == VerdictType.CONDITIONAL:
            self.state_machine.transition(
                state, SystemStatus.AWAITING_HUMAN, "judge verdict: CONDITIONAL"
            )
        else:
            self.state_machine.transition(
                state, SystemStatus.FAILED, "judge verdict: FAIL"
            )

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

            phase_result = phase_result.model_copy(
                update={"status": "completed", "completed_at": datetime.now()}
            )
            state.phase_results[MergePhase.REPORT.value] = phase_result
            self.state_machine.transition(state, SystemStatus.COMPLETED, "reports generated")
        except Exception as e:
            state.errors.append({
                "timestamp": datetime.now().isoformat(),
                "phase": "report",
                "message": f"Report generation failed (non-blocking): {e}",
            })
            phase_result = phase_result.model_copy(update={"status": "completed", "error": str(e)})
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
            self.state_machine.transition(state, SystemStatus.PLAN_REVIEWING, "dispute revision complete")

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
                    state, SystemStatus.AWAITING_HUMAN, "dispute could not be resolved automatically"
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


def _select_merge_strategy(analysis, thresholds) -> MergeDecision:
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


def _build_human_decision_request(fd: FileDiff, analysis) -> HumanDecisionRequest:
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
