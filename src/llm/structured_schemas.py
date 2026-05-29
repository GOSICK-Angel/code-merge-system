"""Wire-shape schemas for P2-1 Structured Outputs.

These Pydantic models describe the *exact* JSON object each prompt's
``<output_format>`` block asks for — NOT the richer domain models the
agents ultimately work with. They exist only to hand a JSON Schema to the
provider's native Structured Outputs (OpenAI ``response_format`` /
Anthropic forced tool-use) so the model returns a well-formed object
instead of markdown-fenced prose.

The well-formed JSON still flows through the existing ``response_parser``
functions, which keep all business logic (grounding downgrades, hedging
sanitisation, deterministic verdicts, truncation gates). Structured
Outputs guarantees *shape*, not *semantics* — the parsers remain the
single source of truth for the latter.

``extra="forbid"`` makes ``model_json_schema()`` emit
``additionalProperties: false`` on every object, and every field is
required (no defaults), so the schema satisfies OpenAI strict-mode
constraints. Anthropic ``input_schema`` is lenient and accepts the same
schema unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_IntentType = Literal["bugfix", "refactor", "feature", "upgrade", "config", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChangeIntentWire(_Strict):
    description: str
    intent_type: _IntentType
    confidence: float = Field(ge=0.0, le=1.0)


class ConflictAnalysisWire(_Strict):
    """Mirror of ``build_conflict_analysis_prompt`` <output_format>."""

    conflict_type: Literal[
        "concurrent_modification",
        "logic_contradiction",
        "semantic_equivalent",
        "dependency_update",
        "interface_change",
        "deletion_vs_modification",
        "refactor_vs_feature",
        "configuration",
        "unknown",
    ]
    upstream_intent: ChangeIntentWire
    fork_intent: ChangeIntentWire
    can_coexist: bool
    semantic_compatibility: Literal["compatible", "incompatible", "orthogonal"]
    recommended_strategy: Literal[
        "take_current", "take_target", "semantic_merge", "escalate_human"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    is_security_sensitive: bool


class JudgeIssueWire(_Strict):
    file_path: str
    issue_level: Literal["critical", "high", "medium", "low", "info"]
    issue_type: Literal[
        "missing_logic", "wrong_merge", "unresolved_conflict", "syntax_error", "other"
    ]
    description: str
    affected_lines: list[int]
    evidence_excerpt: str
    suggested_fix: str
    must_fix_before_merge: bool
    resolvability: Literal["fixable", "system_limitation", "human_required"]


class JudgeFileReviewWire(_Strict):
    """Mirror of ``build_file_review_prompt`` <output_format>."""

    issues: list[JudgeIssueWire]
    overall_assessment: str
    confidence: float = Field(ge=0.0, le=1.0)


class PlanIssueWire(_Strict):
    file_path: str
    current_classification: Literal[
        "auto_safe",
        "auto_risky",
        "human_required",
        "deleted_only",
        "binary",
        "excluded",
    ]
    suggested_classification: Literal[
        "auto_safe",
        "auto_risky",
        "human_required",
        "deleted_only",
        "binary",
        "excluded",
    ]
    reason: str
    issue_type: Literal[
        "risk_underestimated", "wrong_batch", "missing_dependency", "security_missed"
    ]


class PlanJudgeVerdictWire(_Strict):
    """Mirror of ``_return_schema_block`` in planner_judge_prompts."""

    result: Literal["approved", "revision_needed", "critical_replan"]
    issues: list[PlanIssueWire]
    approved_files_count: int
    flagged_files_count: int
    summary: str


# Stable schema identifiers used as the Structured-Output tool / schema name.
CONFLICT_ANALYSIS = "conflict_analysis"
FILE_REVIEW = "file_review"
PLAN_JUDGE_VERDICT = "plan_judge_verdict"

_WIRE_MODELS: dict[str, type[BaseModel]] = {
    CONFLICT_ANALYSIS: ConflictAnalysisWire,
    FILE_REVIEW: JudgeFileReviewWire,
    PLAN_JUDGE_VERDICT: PlanJudgeVerdictWire,
}


def wire_schema(name: str) -> dict[str, Any]:
    """Return the JSON Schema dict for a registered wire model."""
    model = _WIRE_MODELS.get(name)
    if model is None:
        raise KeyError(f"Unknown structured schema: {name}")
    return model.model_json_schema()
