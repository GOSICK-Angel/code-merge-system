from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from src.models.config import GateCommandConfig
from src.models.state import MergeState, SystemStatus

if TYPE_CHECKING:
    from src.core.phases.base import PhaseContext
    from src.models.plan import MergeLayer

logger = logging.getLogger(__name__)


async def run_gates(
    state: MergeState,
    ctx: PhaseContext,
    phase_name: str,
    layer_gates: list[object] | None = None,
) -> bool:
    gates: list[GateCommandConfig] = []
    if layer_gates:
        for gate in layer_gates:
            if isinstance(gate, GateCommandConfig):
                gates.append(gate)
            elif isinstance(gate, dict):
                gates.append(GateCommandConfig(**gate))

    if not gates:
        gates = list(ctx.config.gate.commands)

    if not ctx.config.gate.enabled or not gates:
        return True

    report = await ctx.gate_runner.run_all_gates(
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
    append_gate_record(state, phase_name, gate_entry)

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
        ctx.config.gate.max_consecutive_failures,
    )
    return False


async def handle_gate_failure(
    state: MergeState,
    ctx: PhaseContext,
) -> bool:
    if state.consecutive_gate_failures >= ctx.config.gate.max_consecutive_failures:
        logger.error(
            "Gate consecutive failures (%d) reached limit (%d), escalating to human",
            state.consecutive_gate_failures,
            ctx.config.gate.max_consecutive_failures,
        )
        ctx.state_machine.transition(
            state,
            SystemStatus.AWAITING_HUMAN,
            f"gate failures exceeded limit ({state.consecutive_gate_failures}/"
            f"{ctx.config.gate.max_consecutive_failures})",
        )
        return True
    return False


def verify_layer_deps(
    layer_id: int,
    completed_layers: set[int],
    state: MergeState,
) -> bool:
    if state.merge_plan is None or not state.merge_plan.layers:
        return True
    for layer in state.merge_plan.layers:
        if layer.layer_id == layer_id:
            missing = [dep for dep in layer.depends_on if dep not in completed_layers]
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


def build_layer_index(state: MergeState) -> dict[int, MergeLayer]:
    if state.merge_plan is None or not state.merge_plan.layers:
        return {}
    return {layer.layer_id: layer for layer in state.merge_plan.layers}


def vacuously_complete_layers(
    layer_index: dict[int, MergeLayer],
    layers_with_batches: set[int | None],
) -> set[int]:
    """Identify plan-declared layers that have no AUTO-class batches.

    Such layers (empty placeholders, or layers whose files all route to
    HUMAN_REQUIRED / cherry-pick replay) cannot block downstream
    ``depends_on`` checks — there is nothing to merge in them, so the
    dep is vacuously satisfied. Without this, downstream layers
    false-cascade every file into ``layer_dep_gate`` escalate records.
    """
    return {lid for lid in layer_index if lid not in layers_with_batches}


def get_layer_gates(
    layer_id: int,
    layer_index: dict[int, MergeLayer],
) -> list[object] | None:
    layer = layer_index.get(layer_id)
    if layer is None or not layer.gate_commands:
        return None
    return list(layer.gate_commands)


def append_gate_record(
    state: MergeState, phase_id: str, gate_history_entry: dict[str, object]
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


def append_execution_record(
    state: MergeState,
    phase_id: str,
    phase_result: object,
    files_processed: int,
    commit_sha: str | None = None,
) -> None:
    from src.models.plan import MergePlanLive, PhaseExecutionRecord
    from src.models.state import PhaseResult as PR

    if not isinstance(state.merge_plan, MergePlanLive):
        return
    if not isinstance(phase_result, PR):
        return

    state.merge_plan.execution_records.append(
        PhaseExecutionRecord(
            phase_id=phase_id,
            started_at=phase_result.started_at or datetime.now(),
            completed_at=phase_result.completed_at,
            files_processed=files_processed,
            commit_hash=commit_sha,
        )
    )


def append_judge_record(state: MergeState, round_number: int) -> None:
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
                {"file": i.file_path, "type": i.issue_type} for i in verdict.issues[:20]
            ],
            veto_triggered=verdict.veto_triggered,
            repair_instructions=[
                r.instruction for r in verdict.repair_instructions[:10]
            ],
        )
    )
