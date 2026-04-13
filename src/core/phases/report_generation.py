from __future__ import annotations

import logging
from datetime import datetime

from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.tools.report_writer import (
    write_json_report,
    write_living_plan_report,
    write_markdown_report,
)

logger = logging.getLogger(__name__)


class ReportGenerationPhase(Phase):
    name = "report_generation"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.REPORT
        phase_result = PhaseResult(
            phase=MergePhase.REPORT,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.REPORT.value] = phase_result

        output_dir = state.config.output.directory

        try:
            cost_summary = ctx.cost_tracker.summary() if ctx.cost_tracker else None

            if "json" in state.config.output.formats:
                write_json_report(state, output_dir)
            if "markdown" in state.config.output.formats:
                write_markdown_report(state, output_dir, cost_summary=cost_summary)

            write_living_plan_report(state, output_dir)

            phase_result = phase_result.model_copy(
                update={"status": "completed", "completed_at": datetime.now()}
            )
            state.phase_results[MergePhase.REPORT.value] = phase_result
            ctx.state_machine.transition(
                state, SystemStatus.COMPLETED, "reports generated"
            )
            return PhaseOutcome(
                target_status=SystemStatus.COMPLETED,
                reason="reports generated",
                checkpoint_tag="completed",
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
            ctx.state_machine.transition(
                state,
                SystemStatus.COMPLETED,
                "reports failed but marking complete",
            )
            return PhaseOutcome(
                target_status=SystemStatus.COMPLETED,
                reason="reports failed but marking complete",
                checkpoint_tag="completed",
            )
