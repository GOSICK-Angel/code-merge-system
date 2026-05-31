"""Tests for ``src.agents.guardrails`` — F6 EmptyPlanGuardrail change.

The guardrail's role is to catch *planner failures*. The F6 fix makes
it distinguish those from *legitimate no-op merges* where the upstream
side has zero actionable files in scope — in which case an empty plan is
the correct planner output.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.guardrails import (
    AllHumanRequiredGuardrail,
    EmptyPlanGuardrail,
    GuardrailTripwire,
    run_guardrails,
)
from src.models.diff import FileChangeCategory, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState

from tests.unit.test_phases import _make_config  # type: ignore[import-not-found]


def _risk_summary(total: int, safe: int = 0) -> RiskSummary:
    return RiskSummary(
        total_files=total,
        auto_safe_count=safe,
        auto_risky_count=0,
        human_required_count=0,
        deleted_only_count=0,
        binary_count=0,
        excluded_count=0,
        estimated_auto_merge_rate=1.0 if total == 0 else safe / total,
    )


def _plan(*, phases: list[PhaseFileBatch], total: int) -> MergePlan:
    return MergePlan(
        created_at=datetime(2026, 5, 16, 0, 0, 0),
        upstream_ref="upstream",
        fork_ref="main",
        merge_base_commit="b" * 40,
        phases=phases,
        risk_summary=_risk_summary(total, safe=len(phases)),
        project_context_summary="t",
    )


def _empty_plan() -> MergePlan:
    return _plan(phases=[], total=0)


def _populated_plan() -> MergePlan:
    return _plan(
        phases=[
            PhaseFileBatch(
                batch_id="b1",
                phase=MergePhase.AUTO_MERGE,
                file_paths=["x.py"],
                risk_level=RiskLevel.AUTO_SAFE,
            )
        ],
        total=1,
    )


def _state(**overrides) -> MergeState:
    state = MergeState(config=_make_config())
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


class TestEmptyPlanGuardrail:
    async def test_populated_plan_passes(self) -> None:
        result = await EmptyPlanGuardrail().run(_populated_plan(), _state())
        assert result.passed is True
        assert result.triggered is False

    async def test_empty_plan_with_actionable_files_blocks(self) -> None:
        # B/C/D_MISSING are actionable. State with one upstream_only file
        # but no plan phases = planner failure.
        state = _state(
            file_categories={
                "upstream_only_file.py": FileChangeCategory.B,
                "ignored.py": FileChangeCategory.A,
            }
        )
        result = await EmptyPlanGuardrail().run(_empty_plan(), state)
        assert result.passed is False
        assert result.triggered is True
        assert result.severity == "block"
        assert "1 actionable files" in result.reason

    async def test_empty_plan_no_actionable_files_passes(self) -> None:
        # F6: fork-only changes / unchanged / current_only — no actionable
        # work for the merge to do. Empty plan is correct, not a failure.
        state = _state(
            file_categories={
                "fork_only.py": FileChangeCategory.D_EXTRA,
                "fork_changed.py": FileChangeCategory.E,
                "unchanged.py": FileChangeCategory.A,
            }
        )
        result = await EmptyPlanGuardrail().run(_empty_plan(), state)
        assert result.passed is True
        assert result.triggered is False
        assert "no-op merge" in result.reason

    async def test_empty_plan_no_categories_at_all_passes(self) -> None:
        # Edge case: state was reset / never populated. We err on the
        # side of no-op rather than crash — orchestrator can still
        # short-circuit cleanly.
        result = await EmptyPlanGuardrail().run(_empty_plan(), _state())
        assert result.passed is True
        assert result.triggered is False


class TestRunGuardrailsIntegration:
    async def test_block_severity_raises_tripwire(self) -> None:
        state = _state(file_categories={"x.py": FileChangeCategory.B})
        with pytest.raises(GuardrailTripwire) as exc:
            await run_guardrails(
                [EmptyPlanGuardrail(), AllHumanRequiredGuardrail()],
                _empty_plan(),
                state,
            )
        assert exc.value.guardrail_name == "empty_plan"

    async def test_no_actionable_path_does_not_tripwire(self) -> None:
        # F6 end-to-end through run_guardrails — the legitimate no-op
        # case must NOT raise, so the orchestrator can proceed.
        state = _state(file_categories={"fork_only.py": FileChangeCategory.D_EXTRA})
        await run_guardrails(
            [EmptyPlanGuardrail(), AllHumanRequiredGuardrail()],
            _empty_plan(),
            state,
        )
