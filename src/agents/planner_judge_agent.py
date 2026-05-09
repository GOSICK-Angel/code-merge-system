from __future__ import annotations

import math
from datetime import datetime

from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePlan, MergePhase
from src.models.diff import FileDiff
from src.models.plan_judge import PlanIssue, PlanJudgeResult, PlanJudgeVerdict
from src.models.state import MergeState
from src.llm.prompts.planner_judge_prompts import (
    get_planner_judge_system,
    build_plan_review_prompt,
    build_segment_plan_review_prompt,
    REVIEW_SEGMENT_SIZE,
)
from src.models.plan_review import PlannerIssueResponse
from src.llm.response_parser import parse_plan_judge_verdict


def _aggregate_segment_verdicts(
    verdicts: list[PlanJudgeVerdict],
    total_files: int,
    judge_model: str,
    revision_round: int,
) -> PlanJudgeVerdict:
    if not verdicts:
        return PlanJudgeVerdict(
            result=PlanJudgeResult.LLM_UNAVAILABLE,
            revision_round=revision_round,
            issues=[],
            approved_files_count=0,
            flagged_files_count=0,
            summary="No segment verdicts produced",
            judge_model=judge_model,
            timestamp=datetime.now(),
        )

    all_issues: list[PlanIssue] = []
    seen_files: set[str] = set()
    for v in verdicts:
        for issue in v.issues:
            if issue.file_path not in seen_files:
                all_issues.append(issue)
                seen_files.add(issue.file_path)

    if any(v.result == PlanJudgeResult.CRITICAL_REPLAN for v in verdicts):
        result = PlanJudgeResult.CRITICAL_REPLAN
    elif any(v.result == PlanJudgeResult.LLM_UNAVAILABLE for v in verdicts):
        result = PlanJudgeResult.LLM_UNAVAILABLE
    elif any(v.result == PlanJudgeResult.REVISION_NEEDED for v in verdicts):
        result = PlanJudgeResult.REVISION_NEEDED
    else:
        result = PlanJudgeResult.APPROVED

    flagged = len(all_issues)
    approved = max(0, total_files - flagged)
    completed = len(verdicts)
    total_segments = max(1, len(verdicts))
    segment_summaries = "; ".join(
        f"seg{i + 1}: {v.result.value}({len(v.issues)} issues)"
        for i, v in enumerate(verdicts)
    )
    summary = (
        f"Reviewed {completed} segment(s) covering {total_files} files. "
        f"{flagged} flagged, {approved} approved. [{segment_summaries}]"
    )

    return PlanJudgeVerdict(
        result=result,
        revision_round=revision_round,
        issues=all_issues,
        approved_files_count=approved,
        flagged_files_count=flagged,
        summary=summary,
        judge_model=judge_model,
        timestamp=datetime.now(),
    )


class PlannerJudgeAgent(BaseAgent):
    agent_type = AgentType.PLANNER_JUDGE
    contract_name = "planner_judge"

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(self, state: MergeState) -> AgentMessage:
        view = self.restricted_view(state)
        if view.merge_plan is None:
            raise ValueError("No merge plan to review")

        file_diffs: list[FileDiff] = view.file_diffs

        lang = view.config.output.language
        verdict = await self.review_plan(view.merge_plan, file_diffs, 0, lang=lang)

        return AgentMessage(
            sender=AgentType.PLANNER_JUDGE,
            receiver=AgentType.ORCHESTRATOR,
            phase=MergePhase.PLAN_REVIEW,
            message_type=MessageType.PHASE_COMPLETED,
            subject="Plan review completed",
            payload={"verdict": verdict.model_dump(mode="json")},
        )

    async def review_plan(
        self,
        plan: MergePlan,
        file_diffs: list[FileDiff],
        revision_round: int,
        lang: str = "en",
        *,
        prior_resolved: list[PlanIssue] | None = None,
        prior_still_open: list[PlanIssue] | None = None,
        planner_responses: list[PlannerIssueResponse] | None = None,
    ) -> PlanJudgeVerdict:
        system = get_planner_judge_system(lang)

        total_files = len(file_diffs)
        total_segments = max(1, math.ceil(total_files / REVIEW_SEGMENT_SIZE))

        if total_segments == 1:
            return await self._review_single(
                plan,
                file_diffs,
                revision_round,
                lang,
                system,
                prior_resolved=prior_resolved,
                prior_still_open=prior_still_open,
                planner_responses=planner_responses,
            )

        self.logger.info(
            "Plan review: splitting %d files into %d segments of up to %d each",
            total_files,
            total_segments,
            REVIEW_SEGMENT_SIZE,
        )
        segment_verdicts: list[PlanJudgeVerdict] = []
        for idx in range(total_segments):
            start = idx * REVIEW_SEGMENT_SIZE
            segment = file_diffs[start : start + REVIEW_SEGMENT_SIZE]

            seg_fps = {f.file_path for f in segment}
            seg_prior_resolved = [
                i for i in (prior_resolved or []) if i.file_path in seg_fps
            ]
            seg_prior_open = [
                i for i in (prior_still_open or []) if i.file_path in seg_fps
            ]

            verdict = await self._review_segment(
                plan,
                segment,
                idx,
                total_segments,
                total_files,
                revision_round,
                lang,
                system,
                prior_resolved=seg_prior_resolved,
                prior_still_open=seg_prior_open,
                planner_responses=planner_responses,
            )
            segment_verdicts.append(verdict)

            if verdict.result == PlanJudgeResult.LLM_UNAVAILABLE:
                self.logger.warning(
                    "Segment %d/%d returned LLM_UNAVAILABLE — aborting remaining segments",
                    idx + 1,
                    total_segments,
                )
                break

        return _aggregate_segment_verdicts(
            segment_verdicts, total_files, self.llm_config.model, revision_round
        )

    async def _review_single(
        self,
        plan: MergePlan,
        file_diffs: list[FileDiff],
        revision_round: int,
        lang: str,
        system: str,
        *,
        prior_resolved: list[PlanIssue] | None,
        prior_still_open: list[PlanIssue] | None,
        planner_responses: list[PlannerIssueResponse] | None,
    ) -> PlanJudgeVerdict:
        prompt = build_plan_review_prompt(
            plan,
            file_diffs,
            lang=lang,
            revision_round=revision_round,
            prior_resolved=prior_resolved,
            prior_still_open=prior_still_open,
            planner_responses=planner_responses,
        )
        return await self._call_judge_llm(prompt, system, revision_round)

    async def _review_segment(
        self,
        plan: MergePlan,
        file_segment: list[FileDiff],
        segment_idx: int,
        total_segments: int,
        total_files: int,
        revision_round: int,
        lang: str,
        system: str,
        *,
        prior_resolved: list[PlanIssue],
        prior_still_open: list[PlanIssue],
        planner_responses: list[PlannerIssueResponse] | None,
    ) -> PlanJudgeVerdict:
        prompt = build_segment_plan_review_prompt(
            plan,
            file_segment,
            segment_idx,
            total_segments,
            total_files,
            lang=lang,
            revision_round=revision_round,
            prior_resolved=prior_resolved,
            prior_still_open=prior_still_open,
            planner_responses=planner_responses,
        )
        return await self._call_judge_llm(prompt, system, revision_round)

    async def _call_judge_llm(
        self, prompt: str, system: str, revision_round: int
    ) -> PlanJudgeVerdict:
        messages = [{"role": "user", "content": prompt}]
        try:
            raw = await self._call_llm_with_retry(
                messages, system=system, json_mode=True
            )
            return parse_plan_judge_verdict(
                str(raw), self.llm_config.model, revision_round
            )
        except Exception as e:
            self.logger.error("Plan review failed: %s", e)
            error_type = type(e).__name__
            is_llm_unavailable = any(
                marker in error_type
                for marker in ("AgentExhaustedError", "APIError", "RateLimitError")
            ) or any(
                marker in str(e)
                for marker in ("LLM call failed", "502", "503", "No available accounts")
            )
            result = (
                PlanJudgeResult.LLM_UNAVAILABLE
                if is_llm_unavailable
                else PlanJudgeResult.REVISION_NEEDED
            )
            summary_prefix = (
                "Plan Judge LLM unavailable"
                if is_llm_unavailable
                else f"Review parse failed ({error_type})"
            )
            return PlanJudgeVerdict(
                result=result,
                revision_round=revision_round,
                issues=[],
                approved_files_count=0,
                flagged_files_count=0,
                summary=f"{summary_prefix}: {str(e)[:200]}",
                judge_model=self.llm_config.model,
                timestamp=datetime.now(),
            )

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status == SystemStatus.PLAN_REVIEWING


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("planner_judge", PlannerJudgeAgent)
