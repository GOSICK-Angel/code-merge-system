from src.agents.base_agent import BaseAgent
from src.models.config import AgentLLMConfig
from src.models.message import AgentType, AgentMessage, MessageType
from src.models.plan import MergePlan, MergePhase
from src.models.diff import FileDiff
from src.models.plan_judge import PlanJudgeVerdict
from src.models.state import MergeState
from src.llm.prompts.planner_judge_prompts import (
    get_planner_judge_system,
    build_plan_review_prompt,
)
from src.llm.response_parser import parse_plan_judge_verdict


class PlannerJudgeAgent(BaseAgent):
    agent_type = AgentType.PLANNER_JUDGE

    def __init__(self, llm_config: AgentLLMConfig):
        super().__init__(llm_config)

    async def run(self, state: MergeState) -> AgentMessage:
        if state.merge_plan is None:
            raise ValueError("No merge plan to review")

        file_diffs: list[FileDiff] = []
        if hasattr(state, "_file_diffs"):
            file_diffs = state._file_diffs or []

        lang = state.config.output.language
        verdict = await self.review_plan(state.merge_plan, file_diffs, 0, lang=lang)

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
    ) -> PlanJudgeVerdict:
        prompt = build_plan_review_prompt(plan, file_diffs, lang=lang)

        if revision_round > 0:
            prompt = f"[Revision round {revision_round}]\n\n" + prompt

        messages = [{"role": "user", "content": prompt}]

        system = get_planner_judge_system(lang)
        try:
            raw = await self._call_llm_with_retry(messages, system=system)
            return parse_plan_judge_verdict(
                str(raw), self.llm_config.model, revision_round
            )
        except Exception as e:
            self.logger.error("Plan review failed: %s", e)
            from src.models.plan_judge import PlanJudgeResult
            from datetime import datetime

            return PlanJudgeVerdict(
                result=PlanJudgeResult.REVISION_NEEDED,
                revision_round=revision_round,
                issues=[],
                approved_files_count=0,
                flagged_files_count=0,
                summary=f"Review parse failed (raw response could not be parsed as JSON): {e}",
                judge_model=self.llm_config.model,
                timestamp=datetime.now(),
            )

    def can_handle(self, state: MergeState) -> bool:
        from src.models.state import SystemStatus

        return state.status == SystemStatus.PLAN_REVIEWING


from src.agents.registry import AgentRegistry  # noqa: E402

AgentRegistry.register("planner_judge", PlannerJudgeAgent)
