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
from src.tools.merge_plan_report import write_merge_plan_report


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
            if ctx.coordinator is not None:
                size_hints = _build_file_size_hints(state)
                state.merge_plan = ctx.coordinator.enforce_batch_limits(
                    state.merge_plan, file_size_hints=size_hints
                )
            write_merge_plan_report(state)


def _build_file_size_hints(state: MergeState) -> dict[str, int]:
    """Estimate per-file token cost from FileDiff line counts.

    Heuristic: each changed line of diff costs ~12 tokens (~4 chars/token,
    ~50 chars/line including context). Files with no diff data are omitted;
    the splitter's ``dict.get(fp, 0)`` default treats them as cost-free,
    which is fine because B-class take_target paths skip the LLM anyway.
    """
    hints: dict[str, int] = {}
    for fd in state.file_diffs:
        lines = fd.lines_added + fd.lines_deleted + fd.lines_changed
        if lines <= 0:
            continue
        hints[fd.file_path] = lines * 12
    return hints
