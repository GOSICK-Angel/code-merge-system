from datetime import datetime
from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff, FileChangeCategory, RiskLevel, FileStatus
from src.models.conflict import ConflictAnalysis
from src.models.decision import MergeDecision, FileDecisionRecord, DecisionSource
from src.models.judge import RepairInstruction
from src.models.human import HumanDecisionRequest
from src.models.dispute import PlanDisputeRequest
from src.models.state import MergeState
from src.llm.prompts.executor_prompts import (
    EXECUTOR_SYSTEM,
    build_semantic_merge_prompt,
)
from src.llm.response_parser import parse_merge_result
from src.tools.patch_applier import apply_with_snapshot, create_escalate_record
from src.tools.git_tool import GitTool


class ExecutorAgent(BaseAgent):
    agent_type = AgentType.EXECUTOR

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool

    async def run(self, state: MergeState) -> AgentMessage:
        if state.merge_plan is None:
            return AgentMessage(
                sender=AgentType.EXECUTOR,
                receiver=AgentType.ORCHESTRATOR,
                phase=MergePhase.AUTO_MERGE,
                message_type=MessageType.ERROR,
                subject="No merge plan available",
                payload={},
            )

        processed = 0
        disputes: list[str] = []

        file_diffs_map: dict[str, FileDiff] = {}
        if hasattr(state, "_file_diffs"):
            for fd in state._file_diffs or []:
                file_diffs_map[fd.file_path] = fd

        for batch in state.merge_plan.phases:
            if batch.risk_level not in (RiskLevel.AUTO_SAFE, RiskLevel.DELETED_ONLY):
                continue

            for file_path in batch.file_paths:
                fd = file_diffs_map.get(file_path)
                category = batch.change_category or (fd.change_category if fd else None)
                strategy = self._select_strategy_by_category(category, batch.risk_level)

                if strategy == MergeDecision.SKIP:
                    if fd is not None:
                        record = await self.execute_auto_merge(fd, strategy, state)
                        state.file_decision_records[file_path] = record
                        processed += 1
                    continue

                if category == FileChangeCategory.D_MISSING:
                    record = await self._copy_from_upstream(file_path, state)
                    state.file_decision_records[file_path] = record
                    processed += 1
                    continue

                if fd is None:
                    continue

                record = await self.execute_auto_merge(fd, strategy, state)
                state.file_decision_records[file_path] = record
                processed += 1

        return AgentMessage(
            sender=AgentType.EXECUTOR,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.AUTO_MERGE,
            message_type=MessageType.PHASE_COMPLETED,
            subject=f"Processed {processed} auto-merge files",
            payload={"processed": processed, "disputes": disputes},
        )

    def _select_strategy_by_category(
        self,
        category: FileChangeCategory | None,
        risk_level: RiskLevel,
    ) -> MergeDecision:
        if category == FileChangeCategory.B:
            return MergeDecision.TAKE_TARGET
        if category == FileChangeCategory.D_MISSING:
            return MergeDecision.TAKE_TARGET
        if category == FileChangeCategory.A or category == FileChangeCategory.E:
            return MergeDecision.SKIP
        if category == FileChangeCategory.D_EXTRA:
            return MergeDecision.SKIP
        if category == FileChangeCategory.C:
            if risk_level == RiskLevel.HUMAN_REQUIRED:
                return MergeDecision.ESCALATE_HUMAN
            if risk_level == RiskLevel.AUTO_RISKY:
                return MergeDecision.SEMANTIC_MERGE
            return MergeDecision.TAKE_TARGET
        if risk_level == RiskLevel.DELETED_ONLY:
            return MergeDecision.SKIP
        return MergeDecision.TAKE_TARGET

    async def _copy_from_upstream(
        self, file_path: str, state: MergeState
    ) -> FileDecisionRecord:
        if self.git_tool is None:
            return create_escalate_record(
                file_path, "No git tool available", phase="auto_merge", agent="executor"
            )

        content = self.git_tool.get_file_content(state.config.upstream_ref, file_path)
        if content is None:
            return create_escalate_record(
                file_path,
                "Could not fetch upstream content for D-missing file",
                phase="auto_merge",
                agent="executor",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )
        return await apply_with_snapshot(
            file_path,
            content,
            self.git_tool,
            state,
            phase=current_phase_str,
            agent="executor",
            decision=MergeDecision.TAKE_TARGET,
            rationale="D-missing: copying new file from upstream",
        )

    async def execute_auto_merge(
        self,
        file_diff: FileDiff,
        strategy: MergeDecision,
        state: MergeState,
    ) -> FileDecisionRecord:
        if self.git_tool is None:
            return create_escalate_record(
                file_diff.file_path,
                "No git tool available",
                phase="auto_merge",
                agent="executor",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        if strategy == MergeDecision.TAKE_TARGET:
            content = self.git_tool.get_file_content(
                state.config.upstream_ref, file_diff.file_path
            )
            if content is None:
                return create_escalate_record(
                    file_diff.file_path,
                    "Could not fetch target content",
                    phase=current_phase_str,
                )
            return await apply_with_snapshot(
                file_diff.file_path,
                content,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=strategy,
                rationale="Taking target (upstream) version",
            )

        elif strategy == MergeDecision.TAKE_CURRENT:
            content = self.git_tool.get_file_content(
                state.config.fork_ref, file_diff.file_path
            )
            if content is None:
                return create_escalate_record(
                    file_diff.file_path,
                    "Could not fetch current content",
                    phase=current_phase_str,
                )
            return await apply_with_snapshot(
                file_diff.file_path,
                content,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=strategy,
                rationale="Taking current (fork) version",
            )

        elif strategy == MergeDecision.SKIP:
            return FileDecisionRecord(
                file_path=file_diff.file_path,
                file_status=file_diff.file_status,
                decision=MergeDecision.SKIP,
                decision_source=DecisionSource.AUTO_PLANNER,
                rationale="File skipped per plan",
                phase=current_phase_str,
                agent="executor",
                timestamp=datetime.now(),
            )

        return create_escalate_record(
            file_diff.file_path,
            f"Unsupported auto-merge strategy: {strategy}",
            phase=current_phase_str,
        )

    async def execute_semantic_merge(
        self,
        file_diff: FileDiff,
        conflict_analysis: ConflictAnalysis,
        state: MergeState,
    ) -> FileDecisionRecord:
        if self.git_tool is None:
            return create_escalate_record(
                file_diff.file_path,
                "No git tool available",
            )

        current_content = self.git_tool.get_file_content(
            state.config.fork_ref, file_diff.file_path
        )
        target_content = self.git_tool.get_file_content(
            state.config.upstream_ref, file_diff.file_path
        )

        if current_content is None or target_content is None:
            return create_escalate_record(
                file_diff.file_path,
                "Could not fetch file contents for semantic merge",
            )

        enriched_context = state.config.project_context
        builder = None
        if self._memory_store:
            from src.llm.prompt_builders import AgentPromptBuilder

            builder = AgentPromptBuilder(self.llm_config, self._memory_store)
            memory_text = builder.build_memory_context_text([file_diff.file_path])
            if memory_text:
                enriched_context = (
                    f"{enriched_context}\n\n{memory_text}"
                    if enriched_context
                    else memory_text
                )

        if builder is not None:
            diff_ranges = _extract_diff_ranges(file_diff)
            content_budget = builder.compute_content_budget(
                EXECUTOR_SYSTEM + enriched_context
            )
            budget_tokens = content_budget // 4
            current_content = builder.build_staged_content(
                current_content,
                file_diff.file_path,
                diff_ranges,
                budget_tokens // 2,
            )
            target_content = builder.build_staged_content(
                target_content,
                file_diff.file_path,
                diff_ranges,
                budget_tokens // 2,
            )

        prompt = build_semantic_merge_prompt(
            file_diff,
            conflict_analysis,
            current_content,
            target_content,
            enriched_context,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=EXECUTOR_SYSTEM)
            merged_content = parse_merge_result(str(raw))
        except Exception as e:
            return create_escalate_record(
                file_diff.file_path,
                f"Semantic merge LLM call failed: {e}",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )
        return await apply_with_snapshot(
            file_diff.file_path,
            merged_content,
            self.git_tool,
            state,
            phase=current_phase_str,
            agent="executor",
            decision=MergeDecision.SEMANTIC_MERGE,
            rationale=conflict_analysis.rationale,
            confidence=conflict_analysis.confidence,
        )

    async def execute_human_decision(
        self,
        request: HumanDecisionRequest,
        state: MergeState,
    ) -> FileDecisionRecord:
        if request.human_decision is None:
            return create_escalate_record(
                request.file_path,
                "Human decision not provided",
            )

        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        if request.human_decision == MergeDecision.MANUAL_PATCH:
            if not request.custom_content:
                return create_escalate_record(
                    request.file_path,
                    "MANUAL_PATCH selected but no custom content provided",
                )
            if self.git_tool is None:
                return create_escalate_record(request.file_path, "No git tool")
            return await apply_with_snapshot(
                request.file_path,
                request.custom_content,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=MergeDecision.MANUAL_PATCH,
                rationale=request.reviewer_notes or "Manual patch applied",
            )

        fd_map: dict[str, FileDiff] = {}
        if hasattr(state, "_file_diffs"):
            for fd in state._file_diffs or []:
                fd_map[fd.file_path] = fd

        fd = fd_map.get(request.file_path)
        if fd is None:
            fd = FileDiff(
                file_path=request.file_path,
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.HUMAN_REQUIRED,
                risk_score=0.9,
            )

        record = await self.execute_auto_merge(fd, request.human_decision, state)
        return FileDecisionRecord(
            record_id=record.record_id,
            file_path=record.file_path,
            file_status=record.file_status,
            decision=record.decision,
            decision_source=DecisionSource.HUMAN,
            confidence=record.confidence,
            rationale=request.reviewer_notes or record.rationale,
            original_snapshot=record.original_snapshot,
            merged_content_preview=record.merged_content_preview,
            human_notes=request.reviewer_notes,
            phase=record.phase,
            agent=record.agent,
            timestamp=datetime.now(),
        )

    async def repair(
        self,
        instructions: list[RepairInstruction],
        state: MergeState,
    ) -> list[FileDecisionRecord]:
        if self.git_tool is None:
            return []

        results: list[FileDecisionRecord] = []
        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        for instr in instructions:
            if not instr.is_repairable:
                continue

            current_content = self.git_tool.get_file_content(
                state.config.fork_ref, instr.file_path
            )
            target_content = self.git_tool.get_file_content(
                state.config.upstream_ref, instr.file_path
            )

            if current_content is None and target_content is None:
                continue

            prompt = (
                f"Repair the file '{instr.file_path}' based on this instruction:\n"
                f"{instr.instruction}\n\n"
                f"Current content:\n```\n{current_content or '(file does not exist)'}\n```\n\n"
                f"Upstream content:\n```\n{target_content or '(file does not exist)'}\n```\n\n"
                "Output ONLY the repaired file content, no explanation."
            )
            messages = [{"role": "user", "content": prompt}]

            try:
                raw = await self._call_llm_with_retry(messages, system=EXECUTOR_SYSTEM)
                repaired = parse_merge_result(str(raw))
            except Exception as exc:
                self.logger.warning("Repair failed for %s: %s", instr.file_path, exc)
                continue

            record = await apply_with_snapshot(
                instr.file_path,
                repaired,
                self.git_tool,
                state,
                phase=current_phase_str,
                agent="executor",
                decision=MergeDecision.SEMANTIC_MERGE,
                rationale=f"Repair: {instr.instruction}",
            )
            results.append(record)
            state.file_decision_records[instr.file_path] = record

        return results

    def raise_plan_dispute(
        self,
        file_diff: FileDiff,
        reason: str,
        suggested: dict[str, RiskLevel],
        impact: str,
        state: MergeState,
    ) -> PlanDisputeRequest:
        current_phase_str = (
            state.current_phase.value
            if hasattr(state.current_phase, "value")
            else str(state.current_phase)
        )

        dispute = PlanDisputeRequest(
            raised_by=AgentType.EXECUTOR.value,
            phase=current_phase_str,
            disputed_files=list(suggested.keys()),
            dispute_reason=reason,
            suggested_reclassification=suggested,
            impact_assessment=impact,
        )

        state.plan_disputes.append(dispute)
        return dispute

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status in (
            SystemStatus.AUTO_MERGING,
            SystemStatus.ANALYZING_CONFLICTS,
        )


def _extract_diff_ranges(file_diff: FileDiff) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if file_diff.hunks:
        for hunk in file_diff.hunks:
            ranges.append((hunk.start_line_current, hunk.end_line_current))
    elif file_diff.lines_added > 0 or file_diff.lines_deleted > 0:
        ranges.append((1, file_diff.lines_added + file_diff.lines_deleted + 100))
    return ranges


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("executor", ExecutorAgent, extra_kwargs=["git_tool"])
