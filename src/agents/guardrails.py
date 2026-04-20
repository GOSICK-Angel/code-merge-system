from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.models.plan import MergePlan
    from src.models.state import MergeState

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    triggered: bool = False
    reason: str = ""
    severity: Literal["warn", "block"] = "warn"


class GuardrailTripwire(RuntimeError):
    def __init__(self, guardrail_name: str, reason: str) -> None:
        super().__init__(f"Guardrail '{guardrail_name}' triggered: {reason}")
        self.guardrail_name = guardrail_name
        self.reason = reason


class OutputGuardrail(ABC, Generic[T]):
    name: str = ""

    @abstractmethod
    async def run(self, output: T, state: MergeState) -> GuardrailResult: ...


async def run_guardrails(
    guardrails: list[OutputGuardrail],  # type: ignore[type-arg]
    output: BaseModel,
    state: MergeState,
) -> None:
    results = await asyncio.gather(*(g.run(output, state) for g in guardrails))
    for guardrail, result in zip(guardrails, results):
        if result.triggered:
            if result.severity == "block":
                raise GuardrailTripwire(guardrail.name, result.reason)
            logger.warning("Guardrail '%s' warned: %s", guardrail.name, result.reason)


class EmptyPlanGuardrail(OutputGuardrail):  # type: ignore[type-arg]
    name = "empty_plan"

    async def run(self, output: MergePlan, state: MergeState) -> GuardrailResult:
        if not output.phases:
            return GuardrailResult(
                passed=False,
                triggered=True,
                reason="Planner returned a plan with no phases",
                severity="block",
            )
        return GuardrailResult(passed=True)


class AllHumanRequiredGuardrail(OutputGuardrail):  # type: ignore[type-arg]
    name = "all_human_required"

    async def run(self, output: MergePlan, state: MergeState) -> GuardrailResult:
        from src.models.diff import RiskLevel

        if output.phases and all(
            batch.risk_level == RiskLevel.HUMAN_REQUIRED for batch in output.phases
        ):
            return GuardrailResult(
                passed=False,
                triggered=True,
                reason="All plan phases are HUMAN_REQUIRED — planner may have failed",
                severity="warn",
            )
        return GuardrailResult(passed=True)
