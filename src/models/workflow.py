"""Workflow preset definitions (O-B).

A *workflow* is a named bundle of MergeConfig overrides.  It collapses
scattered CLI flags (``--dry-run`` etc.) and review-intensity choices into a
single ``--workflow <name>`` entry.  Implementation stays config-layer only —
no state-machine changes — so every workflow effect is expressible as a
delta on an existing :class:`~src.models.config.MergeConfig` field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReviewMode = Literal["high", "medium", "low"]


REVIEW_MODE_THRESHOLDS: dict[ReviewMode, tuple[float, float]] = {
    "high": (0.95, 0.50),
    "medium": (0.85, 0.30),
    "low": (0.70, 0.15),
}
"""Mapping from review_mode to (auto_merge_confidence, human_escalation).

``high`` pushes more files through Judge LLM / human review; ``low`` lets
more auto-merge through.  Judge and PlannerJudge still execute their
deterministic segments in every mode — invariant P4 (``不确定即升级``) is
preserved because review_mode only adjusts *thresholds*, never skips agents.
"""


class WorkflowDefinition(BaseModel):
    """Single workflow preset loaded from ``config/workflows.yaml``."""

    name: str = Field(..., description="Unique workflow name used via --workflow.")
    description: str = Field(default="", description="One-line human summary.")
    review_mode: ReviewMode = Field(default="medium")
    dry_run: bool = Field(
        default=False,
        description="When true, run full analysis pipeline but skip file writes.",
    )
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Arbitrary MergeConfig field overrides in dotted-path form, e.g. "
            "{'migration.sync_detection_threshold': 0.2, "
            "'history.commit_after_phase': false}. Applied after review_mode."
        ),
    )


class WorkflowCatalog(BaseModel):
    """Top-level container for ``config/workflows.yaml``."""

    workflows: dict[str, WorkflowDefinition] = Field(default_factory=dict)
