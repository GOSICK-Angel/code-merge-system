"""P0: memory-effectiveness measurement models.

Read-only diagnostics that quantify whether injected memory actually
improved merge decisions. Computed at run-end from the ``MemoryHitTracker``
and the Judge's final verdict; this data never feeds back into any decision
path. Kept separate from ``config.py`` to avoid import cycles.

Influenced-decision metrics are run-local (the tracker's per-file injection
map is not persisted); per-entry effectiveness and observation counts
accumulate across runs via the tracker sidecar.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntryEffectivenessItem(BaseModel, frozen=True):
    """One memory entry's cross-run credit/blame tally.

    ``score`` is ``(pass - fail) / (pass + fail)`` in ``[-1, 1]``.
    """

    entry_id: str
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    score: float = Field(ge=-1.0, le=1.0)


class MemoryEffectivenessReport(BaseModel, frozen=True):
    """Per-run snapshot of memory's effect on merge decisions."""

    run_id: str
    total_judged_decisions: int = Field(ge=0)
    overall_correct_rate: float = Field(ge=0.0, le=1.0)
    memory_influenced_decisions: int = Field(ge=0)
    correct_after_influence: int = Field(ge=0)
    harmful_influence_count: int = Field(ge=0)
    correct_rate_after_influence: float = Field(ge=0.0, le=1.0)
    harmful_influence_rate: float = Field(ge=0.0, le=1.0)
    top_helpful: list[EntryEffectivenessItem] = Field(default_factory=list)
    top_harmful: list[EntryEffectivenessItem] = Field(default_factory=list)
    total_tracked_entries: int = Field(ge=0)
    effective_observations: int = Field(ge=0)


class MemoryAblationComparison(BaseModel, frozen=True):
    """Diff of two runs — memory on vs memory off — on the same dataset.

    ``memory_decision_lift`` is the overall judged-correctness delta
    (``on - off``); it may be negative. The acceptance gate that decides
    whether to activate a feedback loop (lift > 0 AND harmful rate not
    rising over time) is defined in ``doc/evaluation/acceptance.md`` — this
    model only carries the raw numbers.
    """

    on_run_id: str
    off_run_id: str
    overall_correct_rate_on: float = Field(ge=0.0, le=1.0)
    overall_correct_rate_off: float = Field(ge=0.0, le=1.0)
    memory_decision_lift: float
    harmful_influence_rate_on: float = Field(ge=0.0, le=1.0)
    memory_beneficial: bool
