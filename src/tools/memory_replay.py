"""P0: offline memory ablation harness (read-only).

Consumes the ``memory_effectiveness.json`` artifacts that a run persists at
report time (one from a ``memory=on`` run, one from a ``memory=off`` run on
the same dataset — see ``MemoryExtractionConfig.inject_enabled``) and produces
the ablation comparison that answers "did injected memory actually improve
merge decisions?". This is the first real caller of
``compare_memory_effectiveness``.

Pure and offline: it reads already-persisted JSON, makes no LLM calls, and
never touches a decision path. The acceptance gate (lift > 0 AND harmful rate
not rising) is defined in ``doc/evaluation/acceptance.md``; this module only
loads, compares, and renders.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.models.memory_effectiveness import (
    MemoryAblationComparison,
    MemoryEffectivenessReport,
)
from src.tools.memory_eval import compare_memory_effectiveness

REPORT_FILENAME = "memory_effectiveness.json"


def _resolve_report_path(path: str | Path) -> Path:
    """Resolve a user-supplied path to the effectiveness JSON file.

    Accepts either the JSON file directly or a run directory containing
    ``memory_effectiveness.json``. Raises ``FileNotFoundError`` with an
    actionable message when neither resolves.
    """
    p = Path(path)
    if p.is_dir():
        candidate = p / REPORT_FILENAME
        if not candidate.is_file():
            raise FileNotFoundError(
                f"no {REPORT_FILENAME} in run directory {p} — was the run "
                f"completed with memory effectiveness reporting enabled?"
            )
        return candidate
    if not p.is_file():
        raise FileNotFoundError(
            f"effectiveness report not found: {p} (expected a "
            f"{REPORT_FILENAME} file or a run directory containing it)"
        )
    return p


def load_effectiveness_report(path: str | Path) -> MemoryEffectivenessReport:
    """Load a persisted ``MemoryEffectivenessReport`` from a JSON file or run dir."""
    report_path = _resolve_report_path(path)
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    return MemoryEffectivenessReport.model_validate(raw)


def build_ablation_comparison(
    memory_on: MemoryEffectivenessReport,
    memory_off: MemoryEffectivenessReport,
) -> MemoryAblationComparison:
    """Compare the on/off effectiveness reports (wraps the eval analyzer)."""
    return compare_memory_effectiveness(memory_on, memory_off)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_ablation_table(cmp: MemoryAblationComparison) -> str:
    """Render the ablation comparison as a plain markdown table.

    The verdict line restates the convenience ``memory_beneficial`` flag
    (lift > 0); the full acceptance gate also requires the harmful-influence
    rate not to rise over time (see ``doc/evaluation/acceptance.md``).
    """
    lift = cmp.memory_decision_lift
    sign = "+" if lift > 0 else ""
    verdict = "BENEFICIAL (lift > 0)" if cmp.memory_beneficial else "NOT beneficial"
    return "\n".join(
        [
            "| Metric | memory=on | memory=off |",
            "|---|---|---|",
            f"| run_id | `{cmp.on_run_id}` | `{cmp.off_run_id}` |",
            f"| overall_correct_rate | {_pct(cmp.overall_correct_rate_on)} "
            f"| {_pct(cmp.overall_correct_rate_off)} |",
            "",
            f"**memory_decision_lift**: {sign}{lift:.4f} "
            f"({_pct(lift) if lift >= 0 else '-' + _pct(-lift)})",
            "",
            f"**harmful_influence_rate (on)**: {_pct(cmp.harmful_influence_rate_on)}",
            "",
            f"**Verdict**: {verdict}",
        ]
    )
