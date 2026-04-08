from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePhase
from src.models.diff import FileDiff
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.state import MergeState
from src.llm.prompts.analyst_prompts import (
    ANALYST_SYSTEM,
    build_conflict_analysis_prompt,
)
from src.llm.response_parser import parse_conflict_analysis
from src.tools.git_tool import GitTool


class ConflictAnalystAgent(BaseAgent):
    agent_type = AgentType.CONFLICT_ANALYST

    def __init__(self, llm_config: AgentLLMConfig, git_tool: GitTool | None = None):
        super().__init__(llm_config)
        self.git_tool = git_tool

    async def run(self, state: MergeState) -> AgentMessage:
        results: dict[str, ConflictAnalysis] = {}

        if state.merge_plan is None:
            return AgentMessage(
                sender=AgentType.CONFLICT_ANALYST,
                receiver=AgentType.ORCHESTRATOR,
                phase=MergePhase.CONFLICT_ANALYSIS,
                message_type=MessageType.PHASE_COMPLETED,
                subject="Conflict analysis skipped: no plan",
                payload={},
            )

        high_risk_files: list[str] = []
        for batch in state.merge_plan.phases:
            from src.models.diff import RiskLevel

            if batch.risk_level in (RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY):
                high_risk_files.extend(batch.file_paths)

        file_diffs_map: dict[str, FileDiff] = {}
        if hasattr(state, "_file_diffs"):
            for fd in state._file_diffs or []:
                file_diffs_map[fd.file_path] = fd

        for file_path in high_risk_files:
            fd = file_diffs_map.get(file_path)
            if fd is None:
                continue

            base_content = target_content = current_content = None
            if self.git_tool and hasattr(state, "_merge_base"):
                base_content, current_content, target_content = (
                    self.git_tool.get_three_way_diff(
                        state._merge_base or "",
                        state.config.fork_ref,
                        state.config.upstream_ref,
                        file_path,
                    )
                )

            analysis = await self.analyze_file(
                fd,
                base_content=base_content,
                current_content=current_content,
                target_content=target_content,
                project_context=state.config.project_context,
            )
            results[file_path] = analysis

        state.conflict_analyses.update(results)

        return AgentMessage(
            sender=AgentType.CONFLICT_ANALYST,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.CONFLICT_ANALYSIS,
            message_type=MessageType.PHASE_COMPLETED,
            subject=f"Analyzed {len(results)} files",
            payload={"analyzed_count": len(results)},
        )

    async def analyze_file(
        self,
        file_diff: FileDiff,
        base_content: str | None,
        current_content: str | None,
        target_content: str | None,
        project_context: str = "",
    ) -> ConflictAnalysis:
        enriched_context = project_context
        builder = None
        if self._memory_store:
            from src.llm.prompt_builders import AgentPromptBuilder

            builder = AgentPromptBuilder(self.llm_config, self._memory_store)
            memory_text = builder.build_memory_context_text([file_diff.file_path])
            if memory_text:
                enriched_context = (
                    f"{project_context}\n\n{memory_text}"
                    if project_context
                    else memory_text
                )

        if builder is not None:
            diff_ranges = _extract_diff_ranges(file_diff)
            content_budget = builder.compute_content_budget(
                ANALYST_SYSTEM + enriched_context
            )
            content_budget_tokens = content_budget // 4  # chars -> rough tokens
            if current_content:
                current_content = builder.build_staged_content(
                    current_content,
                    file_diff.file_path,
                    diff_ranges,
                    content_budget_tokens // 2,
                )
            if target_content:
                target_content = builder.build_staged_content(
                    target_content,
                    file_diff.file_path,
                    diff_ranges,
                    content_budget_tokens // 2,
                )
            if base_content:
                base_content = builder.build_staged_content(
                    base_content,
                    file_diff.file_path,
                    diff_ranges,
                    content_budget_tokens // 4,
                )

        prompt = build_conflict_analysis_prompt(
            file_diff,
            base_content,
            current_content,
            target_content,
            enriched_context,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            raw = await self._call_llm_with_retry(messages, system=ANALYST_SYSTEM)
            return parse_conflict_analysis(
                str(raw), file_diff.file_path, self.llm_config.model
            )
        except Exception as e:
            self.logger.error(
                f"Conflict analysis failed for {file_diff.file_path}: {e}"
            )
            return ConflictAnalysis(
                file_path=file_diff.file_path,
                conflict_points=[],
                overall_confidence=0.3,
                recommended_strategy=MergeDecision.ESCALATE_HUMAN,
                conflict_type=ConflictType.UNKNOWN,
                rationale=f"Analysis failed: {e}",
                confidence=0.3,
            )

    async def analyze_conflict_point(
        self,
        file_diff: FileDiff,
        hunk_content: str,
        project_context: str = "",
    ) -> ConflictAnalysis:
        return await self.analyze_file(
            file_diff,
            base_content=None,
            current_content=hunk_content,
            target_content=None,
            project_context=project_context,
        )

    def compute_confidence(
        self, analysis: ConflictAnalysis, has_base_version: bool
    ) -> float:
        base_confidence = analysis.confidence

        type_adjustment = {
            ConflictType.SEMANTIC_EQUIVALENT: 0.20,
            ConflictType.DEPENDENCY_UPDATE: 0.15,
            ConflictType.CONFIGURATION: 0.10,
            ConflictType.CONCURRENT_MODIFICATION: 0.0,
            ConflictType.REFACTOR_VS_FEATURE: -0.10,
            ConflictType.DELETION_VS_MODIFICATION: -0.15,
            ConflictType.INTERFACE_CHANGE: -0.20,
            ConflictType.LOGIC_CONTRADICTION: -0.30,
            ConflictType.UNKNOWN: -0.25,
        }

        base_bonus = 0.10 if has_base_version else 0.0
        calibrated = base_confidence * 0.85
        adjustment = type_adjustment.get(analysis.conflict_type, 0.0)
        final = calibrated + adjustment + base_bonus
        return max(0.10, min(0.95, round(final, 3)))

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status == SystemStatus.ANALYZING_CONFLICTS


def _extract_diff_ranges(file_diff: FileDiff) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if file_diff.hunks:
        for hunk in file_diff.hunks:
            ranges.append((hunk.start_line_current, hunk.end_line_current))
    elif file_diff.lines_added > 0 or file_diff.lines_deleted > 0:
        ranges.append((1, file_diff.lines_added + file_diff.lines_deleted + 100))
    return ranges
