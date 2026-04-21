from __future__ import annotations

import json
import logging
from datetime import datetime

from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff, FileChangeCategory, RiskLevel, FileStatus
from src.models.conflict import ConflictAnalysis
from src.models.decision import MergeDecision, FileDecisionRecord, DecisionSource
from src.models.judge import (
    RepairInstruction,
    JudgeIssue,
    ExecutorRebuttal,
    DisputePoint,
)
from src.models.human import HumanDecisionRequest
from src.models.dispute import PlanDisputeRequest
from src.models.plan_review import UserDecisionItem, DecisionOption
from src.models.state import MergeState
from src.llm.prompts.executor_prompts import (
    EXECUTOR_SYSTEM,
    build_semantic_merge_prompt,
    build_deletion_analysis_prompt,
    build_rebuttal_prompt,
)
from src.llm.response_parser import parse_merge_result
from src.tools.patch_applier import apply_with_snapshot, create_escalate_record
from src.tools.git_tool import GitTool

logger = logging.getLogger(__name__)


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
        for fd in state.file_diffs:
            file_diffs_map[fd.file_path] = fd

        from src.tools.sentinel_scanner import SentinelScanner

        sentinel_scanner = SentinelScanner.from_config_extras(
            list(getattr(state.config, "sentinels_extra", None) or [])
        )

        for batch in state.merge_plan.phases:
            if batch.risk_level not in (RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY):
                continue

            for file_path in batch.file_paths:
                fd = file_diffs_map.get(file_path)  # type: ignore[assignment]
                category = batch.change_category or (fd.change_category if fd else None)
                strategy = self._select_strategy_by_category(category, batch.risk_level)

                if strategy == MergeDecision.SKIP:
                    if fd is not None:
                        record = await self.execute_auto_merge(fd, strategy, state)
                        state.file_decision_records[file_path] = record
                        processed += 1
                    continue

                if (
                    batch.risk_level == RiskLevel.AUTO_SAFE
                    and self.git_tool is not None
                ):
                    fork_content = self.git_tool.get_file_content(
                        state.config.fork_ref, file_path
                    )
                    if fork_content:
                        hits = sentinel_scanner.scan(fork_content, file_path)
                        if hits:
                            state.sentinel_hits[file_path] = hits
                            if fd is None:
                                from src.models.diff import FileDiff, FileStatus

                                fd = FileDiff(
                                    file_path=file_path,
                                    file_status=FileStatus.MODIFIED,
                                    risk_level=RiskLevel.AUTO_SAFE,
                                    risk_score=0.5,
                                )
                            self.raise_plan_dispute(
                                fd,
                                reason=(
                                    f"Sentinel marker(s) found in AUTO_SAFE fork "
                                    f"file '{file_path}': "
                                    + "; ".join(
                                        f"line {h.line_number}: {h.matched_text[:60]}"
                                        for h in hits[:3]
                                    )
                                ),
                                suggested={file_path: RiskLevel.HUMAN_REQUIRED},
                                impact=(
                                    "File contains fork-customization markers. "
                                    "Overwriting with upstream may silently drop "
                                    "fork-only logic."
                                ),
                                state=state,
                            )
                            disputes.append(file_path)
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
            logger.warning("Semantic merge failed for %s: %s", file_diff.file_path, e)
            return create_escalate_record(
                file_diff.file_path,
                f"SEMANTIC_MERGE_FAILED: {e}",
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
        for fd in state.file_diffs:
            fd_map[fd.file_path] = fd

        fd = fd_map.get(request.file_path)  # type: ignore[assignment]
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

        # Budget: repair prompts embed current + upstream content. Without a
        # cap a single 300k-char lock file blows past the context window
        # (we observed 93k tokens = 46% of 200k). Skip files that would
        # force an unsafe "fill in the blanks" trim — lock files and
        # generated artifacts should be regenerated by their toolchain,
        # not patched by an LLM.
        _MAX_CONTENT_CHARS_PER_SIDE = 30_000
        _NON_LLM_REPAIR_SUFFIXES = (
            ".lock",
            "uv.lock",
            "package-lock.json",
            "pnpm-lock.yaml",
            "poetry.lock",
            "Cargo.lock",
            "yarn.lock",
            "Gemfile.lock",
        )

        for instr in instructions:
            if not instr.is_repairable:
                continue

            if instr.file_path.endswith(_NON_LLM_REPAIR_SUFFIXES):
                self.logger.info(
                    "Skipping LLM repair for generated/lock file %s "
                    "(regenerate via toolchain instead)",
                    instr.file_path,
                )
                continue

            current_content = self.git_tool.get_file_content(
                state.config.fork_ref, instr.file_path
            )
            target_content = self.git_tool.get_file_content(
                state.config.upstream_ref, instr.file_path
            )

            if current_content is None and target_content is None:
                continue

            cur_len = len(current_content or "")
            tgt_len = len(target_content or "")
            if (
                cur_len > _MAX_CONTENT_CHARS_PER_SIDE
                or tgt_len > _MAX_CONTENT_CHARS_PER_SIDE
            ):
                self.logger.warning(
                    "Skipping LLM repair for oversized file %s "
                    "(current=%d chars, upstream=%d chars, limit=%d). "
                    "Escalating: file needs manual attention.",
                    instr.file_path,
                    cur_len,
                    tgt_len,
                    _MAX_CONTENT_CHARS_PER_SIDE,
                )
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

    async def analyze_deletion(
        self,
        file_path: str,
        file_diff: FileDiff,
        state: MergeState,
    ) -> UserDecisionItem:
        prompt = build_deletion_analysis_prompt(
            file_path,
            file_diff.lines_deleted,
            state.config.project_context,
        )
        rationale = "File deleted in upstream branch."
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=EXECUTOR_SYSTEM
            )
            rationale = str(raw).strip()
        except Exception as exc:
            logger.warning("analyze_deletion LLM failed for %s: %s", file_path, exc)

        return UserDecisionItem(
            item_id=f"deleted_only_{file_path}",
            file_path=file_path,
            description=f"File '{file_path}' is deleted in upstream.",
            risk_context=rationale,
            current_classification=RiskLevel.DELETED_ONLY.value,
            options=[
                DecisionOption(
                    key="A",
                    label="confirm_delete",
                    description="Apply deletion (remove this file)",
                ),
                DecisionOption(
                    key="B",
                    label="keep",
                    description="Keep file (do not apply upstream deletion)",
                ),
            ],
        )

    async def build_rebuttal(
        self,
        issues: list[JudgeIssue],
        state: MergeState,
    ) -> ExecutorRebuttal:
        if not issues:
            return ExecutorRebuttal(accepts_all=True)

        issues_summary = "\n".join(
            f"- [{i.issue_id}] {i.issue_level.value}: {i.description}" for i in issues
        )
        file_paths = list({i.file_path for i in issues})
        prompt = build_rebuttal_prompt(
            issues_summary, file_paths, state.config.project_context
        )
        try:
            raw = await self._call_llm_with_retry(
                [{"role": "user", "content": prompt}], system=EXECUTOR_SYSTEM
            )
            raw_str = str(raw).strip()
            if raw_str.startswith("```"):
                lines = raw_str.splitlines()
                raw_str = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(raw_str)
        except Exception as exc:
            logger.warning("build_rebuttal LLM failed: %s", exc)
            return ExecutorRebuttal(
                accepts_all=True,
                repair_instructions=[
                    RepairInstruction(
                        file_path=i.file_path,
                        instruction=i.description,
                        severity=i.issue_level,
                        is_repairable=True,
                    )
                    for i in issues
                    if i.must_fix_before_merge
                ],
                overall_rationale="Rebuttal analysis failed; accepting all issues for repair.",
            )

        accepts_all: bool = bool(data.get("accepts_all", False))
        dispute_points: list[DisputePoint] = []
        repair_instructions: list[RepairInstruction] = []
        issue_map = {i.issue_id: i for i in issues}

        for decision in data.get("decisions", []):
            issue_id = decision.get("issue_id", "")
            action = decision.get("action", "accept")
            evidence = decision.get("counter_evidence", "")
            if action == "dispute":
                dispute_points.append(
                    DisputePoint(
                        issue_id=issue_id, counter_evidence=evidence, accepts=False
                    )
                )
            else:
                original = issue_map.get(issue_id)
                if original and original.must_fix_before_merge:
                    repair_instructions.append(
                        RepairInstruction(
                            file_path=original.file_path,
                            instruction=original.description,
                            severity=original.issue_level,
                            is_repairable=True,
                            source_issue_id=issue_id,
                        )
                    )

        return ExecutorRebuttal(
            accepts_all=accepts_all or not dispute_points,
            dispute_points=dispute_points,
            repair_instructions=repair_instructions,
            overall_rationale=data.get("overall_rationale", ""),
        )

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
