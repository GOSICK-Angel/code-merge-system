from __future__ import annotations

from datetime import datetime

from src.agents.guardrails import (
    AllHumanRequiredGuardrail,
    EmptyPlanGuardrail,
    run_guardrails,
)
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus


class PlanningPhase(Phase):
    name = "planning"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.ANALYSIS
        phase_result = PhaseResult(
            phase=MergePhase.ANALYSIS,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.ANALYSIS.value] = phase_result

        try:
            planner = ctx.agents["planner"]
            await planner.run(state)
            phase_result = phase_result.model_copy(
                update={"status": "completed", "completed_at": datetime.now()}
            )
            state.phase_results[MergePhase.ANALYSIS.value] = phase_result
            ctx.state_machine.transition(
                state, SystemStatus.PLAN_REVIEWING, "phase 1 complete"
            )
        except Exception as e:
            phase_result = phase_result.model_copy(
                update={"status": "failed", "error": str(e)}
            )
            state.phase_results[MergePhase.ANALYSIS.value] = phase_result
            raise

        return PhaseOutcome(
            target_status=SystemStatus.PLAN_REVIEWING,
            reason="phase 1 complete",
            checkpoint_tag="after_phase1",
            memory_phase="planning",
        )

    async def after(
        self, state: MergeState, outcome: PhaseOutcome, ctx: PhaseContext
    ) -> None:
        if state.merge_plan is not None:
            await run_guardrails(
                [EmptyPlanGuardrail(), AllHumanRequiredGuardrail()],
                state.merge_plan,
                state,
            )
