"""Prompt gate registry: map stable gate IDs to existing prompt builders.

Gate IDs follow ``<AgentPrefix>-<Purpose>`` (e.g. ``PJ-PLAN-REVIEW``,
``J-DETERMINISTIC``, ``CA-THREE-WAY``).  This layer does **not** rewrite or
inline prompts — it only registers the existing prompt functions under stable
IDs so that:

* agent contracts can declare which gates an agent is permitted to use
  (enforced by contract loader + tests);
* future prompt A/B experiments and version rollbacks happen in a single
  place instead of touching every agent module.

Registration is eager at import time (this module imports existing prompt
builders).  Consumers obtain a :class:`Gate` via :func:`get_gate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.llm.prompts.analyst_prompts import (
    ANALYST_SYSTEM,
    build_commit_round_prompt,
    build_conflict_analysis_prompt,
)
from src.llm.prompts.executor_prompts import (
    EXECUTOR_SYSTEM,
    build_deletion_analysis_prompt,
    build_rebuttal_prompt,
    build_semantic_merge_prompt,
)
from src.llm.prompts.judge_prompts import (
    JUDGE_SYSTEM,
    build_batch_file_review_prompt,
    build_file_review_prompt,
    build_re_evaluate_prompt,
    build_verdict_prompt,
)
from src.llm.prompts.memory_extractor_prompts import (
    MEMORY_EXTRACTOR_SYSTEM,
    build_extraction_prompt,
)
from src.llm.prompts.planner_judge_prompts import (
    build_plan_review_prompt,
    get_planner_judge_system,
)
from src.llm.prompts.planner_prompts import (
    PLANNER_EVALUATION_SYSTEM,
    build_classification_prompt,
    build_evaluation_prompt,
    build_revision_prompt,
    get_planner_system,
)


@dataclass(frozen=True)
class Gate:
    """A registered prompt builder identified by a stable gate ID."""

    gate_id: str
    builder: Callable[..., str]
    description: str

    def render(self, *args: Any, **kwargs: Any) -> str:
        return self.builder(*args, **kwargs)


_REGISTRY: dict[str, Gate] = {}


def register_gate(gate_id: str, builder: Callable[..., str], description: str) -> Gate:
    if gate_id in _REGISTRY:
        raise ValueError(f"Gate already registered: {gate_id!r}")
    gate = Gate(gate_id=gate_id, builder=builder, description=description)
    _REGISTRY[gate_id] = gate
    return gate


def get_gate(gate_id: str) -> Gate:
    if gate_id not in _REGISTRY:
        raise KeyError(f"Unknown gate {gate_id!r}. Registered: {sorted(_REGISTRY)}")
    return _REGISTRY[gate_id]


def registered_gate_ids() -> list[str]:
    return sorted(_REGISTRY)


def _planner_system_constant(*_args: Any, **_kwargs: Any) -> str:
    return PLANNER_EVALUATION_SYSTEM


def _executor_system_constant(*_args: Any, **_kwargs: Any) -> str:
    return EXECUTOR_SYSTEM


def _analyst_system_constant(*_args: Any, **_kwargs: Any) -> str:
    return ANALYST_SYSTEM


def _judge_system_constant(*_args: Any, **_kwargs: Any) -> str:
    return JUDGE_SYSTEM


def _memory_extractor_system_constant(*_args: Any, **_kwargs: Any) -> str:
    return MEMORY_EXTRACTOR_SYSTEM


# Planner (P-)
register_gate(
    "P-SYSTEM",
    get_planner_system,
    "Planner system prompt (language-aware).",
)
register_gate(
    "P-CLASSIFICATION",
    build_classification_prompt,
    "Planner classification prompt: ABCDE-risk decision per file batch.",
)
register_gate(
    "P-REVISION",
    build_revision_prompt,
    "Planner revision prompt: applies PlannerJudge issues to regenerate plan.",
)
register_gate(
    "P-EVAL-SYSTEM",
    _planner_system_constant,
    "Planner evaluation system prompt (for judge feedback evaluation).",
)
register_gate(
    "P-EVALUATION",
    build_evaluation_prompt,
    "Planner evaluation prompt: accept/reject reviewer issues.",
)

# PlannerJudge (PJ-)
register_gate(
    "PJ-SYSTEM",
    get_planner_judge_system,
    "PlannerJudge system prompt (language-aware).",
)
register_gate(
    "PJ-PLAN-REVIEW",
    build_plan_review_prompt,
    "PlannerJudge plan review user prompt (handles prior rounds + planner responses).",
)

# ConflictAnalyst (CA-)
register_gate(
    "CA-SYSTEM",
    _analyst_system_constant,
    "ConflictAnalyst system prompt (semantic three-way diff reasoning).",
)
register_gate(
    "CA-THREE-WAY",
    build_conflict_analysis_prompt,
    "ConflictAnalyst three-way diff analysis prompt for a single high-risk file.",
)
register_gate(
    "CA-COMMIT-ROUND",
    build_commit_round_prompt,
    "ConflictAnalyst per-commit reasoning round prompt.",
)

# Executor (E-)
register_gate(
    "E-SYSTEM",
    _executor_system_constant,
    "Executor system prompt (semantic merge discipline).",
)
register_gate(
    "E-SEMANTIC-MERGE",
    build_semantic_merge_prompt,
    "Executor semantic merge prompt: synthesizes merged content for SEMANTIC_MERGE decisions.",
)
register_gate(
    "E-DELETION",
    build_deletion_analysis_prompt,
    "Executor deletion analysis prompt.",
)
register_gate(
    "E-REBUTTAL",
    build_rebuttal_prompt,
    "Executor rebuttal prompt for plan disputes.",
)

# Judge (J-)
register_gate(
    "J-SYSTEM",
    _judge_system_constant,
    "Judge system prompt (independent reviewer of merge results).",
)
register_gate(
    "J-FILE-REVIEW",
    build_file_review_prompt,
    "Judge single-file review prompt.",
)
register_gate(
    "J-VERDICT",
    build_verdict_prompt,
    "Judge verdict synthesis prompt across all file reviews.",
)
register_gate(
    "J-BATCH-REVIEW",
    build_batch_file_review_prompt,
    "Judge batched file review prompt (multi-file in one call).",
)
register_gate(
    "J-RE-EVALUATE",
    build_re_evaluate_prompt,
    "Judge re-evaluation prompt after Executor repair round.",
)

# MemoryExtractor (M-)
register_gate(
    "M-SYSTEM",
    _memory_extractor_system_constant,
    "MemoryExtractor system prompt (causal insight extraction).",
)
register_gate(
    "M-EXTRACT-INSIGHT",
    build_extraction_prompt,
    "MemoryExtractor user prompt: extract causal insights from phase events.",
)
