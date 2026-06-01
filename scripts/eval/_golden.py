"""Build the LLM-judgment golden set consumed by ``merge optimize-prompts``.

A *golden case* is a ``(case_id, expected_decision)`` pair for one
``*-SYSTEM`` gate. ``optimize-prompts`` ranks prompt variants by how often
their rollout reproduces ``expected_decision``; that signal only discriminates
between variants on cases whose decision is genuinely driven by the LLM, not
short-circuited by a deterministic rule (security_sensitive force, deterministic
veto, heuristic cap). This module turns the ``judgment_intensive`` /
``golden_decisions`` fields authored in each sample's ``meta.yaml`` into the
per-gate ``[{case_id, expected_decision}]`` JSON the CLI consumes — meta.yaml is
the single source of truth, so the golden set never drifts from the dataset.

The gate decision vocabularies are derived from the production enums
(``VerdictType`` / ``RiskLevel`` / ``MergeDecision``) so a renamed decision
value fails the build instead of silently mislabelling a case. ``GoldenCase`` is
imported from the production harness so the emitted objects are exactly what
``optimize-prompts --golden`` validates.
"""

from __future__ import annotations

from pathlib import Path

from src.models.decision import MergeDecision
from src.models.diff import RiskLevel
from src.models.judge import VerdictType
from src.tools.prompt_optimizer import GoldenCase

from scripts.eval._ground_truth import GroundTruthMissing, load_meta
from scripts.eval._schemas import SampleMeta

# Tier -> sample container, mirroring scripts.eval.lock.TIER_LAYOUT. Only tiers
# whose entries carry a SampleMeta (tier-1 micro-bench, tier-3 adversarial) can
# contribute golden cases; tier-2 replays have no meta.yaml and are skipped.
_TIER_LAYOUT: dict[int, str] = {
    1: "tier1/samples",
    2: "tier2/replays",
    3: "tier3/adversarial",
}

# expected_decision must be one of the gate's real decision values. Keyed by the
# gate IDs registered in src/llm/prompts/gate_registry.py.
GATE_DECISION_VOCAB: dict[str, frozenset[str]] = {
    "J-SYSTEM": frozenset(v.value for v in VerdictType),
    "P-RISK-SCORE-SYSTEM": frozenset(v.value for v in RiskLevel),
    "CA-SYSTEM": frozenset(v.value for v in MergeDecision),
}


class GoldenBuildError(ValueError):
    """A sample declared a golden case the build rejects (typo / unknown gate)."""


def _validate_decision(sample_id: str, gate_id: str, decision: str) -> None:
    vocab = GATE_DECISION_VOCAB.get(gate_id)
    if vocab is None:
        raise GoldenBuildError(
            f"{sample_id}: unknown golden gate '{gate_id}' "
            f"(known: {sorted(GATE_DECISION_VOCAB)})"
        )
    if decision not in vocab:
        raise GoldenBuildError(
            f"{sample_id}: '{decision}' is not a valid {gate_id} decision "
            f"(allowed: {sorted(vocab)})"
        )


def _iter_sample_metas(datasets_root: Path, tiers: tuple[int, ...]) -> list[SampleMeta]:
    metas: list[SampleMeta] = []
    for tier in tiers:
        layout = _TIER_LAYOUT.get(tier)
        if layout is None:
            continue
        container = datasets_root / layout
        if not container.is_dir():
            continue
        for sample_dir in sorted(p for p in container.iterdir() if p.is_dir()):
            try:
                metas.append(load_meta(sample_dir))
            except GroundTruthMissing:
                # Not every dir is a meta-bearing sample (e.g. tier-2 replays).
                continue
    return metas


def build_golden_sets(
    datasets_root: Path,
    tiers: tuple[int, ...] = (1, 2, 3),
) -> dict[str, list[GoldenCase]]:
    """Collect judgment-intensive golden cases grouped by ``*-SYSTEM`` gate.

    Only samples with ``judgment_intensive: true`` contribute; each declared
    ``golden_decisions`` entry is validated against its gate's vocabulary
    (``GoldenBuildError`` on mismatch) and grouped under that gate. Cases within
    a gate are sorted by ``case_id`` for deterministic output. A sample marked
    judgment-intensive with no ``golden_decisions`` is a no-op (it contributes
    no case) rather than an error — staging a sample before labelling it is
    allowed.
    """
    grouped: dict[str, list[GoldenCase]] = {}
    for meta in _iter_sample_metas(datasets_root, tiers):
        if not meta.judgment_intensive:
            continue
        for entry in meta.golden_decisions:
            _validate_decision(meta.sample_id, entry.gate_id, entry.expected_decision)
            grouped.setdefault(entry.gate_id, []).append(
                GoldenCase(
                    case_id=meta.sample_id,
                    expected_decision=entry.expected_decision,
                )
            )
    return {
        gate_id: sorted(cases, key=lambda c: c.case_id)
        for gate_id, cases in sorted(grouped.items())
    }
