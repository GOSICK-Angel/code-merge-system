"""Cross-run consistency for evaluation (DET / CPC).

Compares ``N`` independent evaluation runs and reports the fraction of
files whose ``(decision, target_risk_level)`` tuple agrees across every
run (metrics.md §6.1 / §6.2).

DET — Determinism: same configuration repeated N times.
CPC — Cross-Provider Consistency: identical pipeline run under different
      reviewer / executor model pairings.

The two metrics share an identical comparison engine — the only
difference is intent (and the convention is reflected in the metric name
recorded in the output). Triggering the N runs is the caller's job
(``plan §决策 3``: shell loop over ``run.py`` invocations); this script
only reads ``runs/<sample_id>/merge_report_<run_id>.json`` produced by
Phase 3 and emits the consistency report.

Per-file decision dimension is sourced from
``MergeState.file_decision_records[f].(decision|strategy, target_risk_level|risk)``
— the same dual-name fallback used by ``diff_against_golden.py`` so a
real ``merge`` run and a fake-fixture run remain interoperable.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Literal

from scripts.eval._common import write_json

Metric = Literal["DET", "CPC"]


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Merge report ingestion (duplicates diff_against_golden's tolerant reader
# so consistency.py does not depend on diff layer internals).
# ---------------------------------------------------------------------------


def _locate_merge_report(sample_dir: Path) -> Path:
    """Pick the lexicographically last ``merge_report_*.json`` in the sample
    directory.

    Aligned with [code-phase-4] ``diff_against_golden._locate_merge_report``
    behaviour. Raises :class:`FileNotFoundError` when none exist so callers
    can attribute the gap to a specific sample.
    """
    matches = sorted(sample_dir.glob("merge_report_*.json"))
    if not matches:
        raise FileNotFoundError(
            f"sample {sample_dir.name}: no merge_report_<run_id>.json"
        )
    return matches[-1]


def _decision_tuple(record: dict[str, object]) -> tuple[str, str]:
    """Extract ``(decision, target_risk_level)`` honoring both naming
    conventions used across fixtures and the real merge CLI.

    Fixtures from Phase 3 use the JSON-mode names ``decision`` /
    (absent ``target_risk_level`` → ``"UNKNOWN"``); the real ``MergeState``
    dump exposes ``strategy`` / ``target_risk_level``.
    """
    decision = str(record.get("decision") or record.get("strategy") or "UNKNOWN")
    risk = str(record.get("target_risk_level") or record.get("risk") or "UNKNOWN")
    return decision, risk


def _load_records(sample_dir: Path) -> dict[str, tuple[str, str]]:
    """Return ``{file_path: (decision, risk)}`` for one sample."""
    payload = json.loads(_locate_merge_report(sample_dir).read_text(encoding="utf-8"))
    records = payload.get("file_decision_records", {})
    out: dict[str, tuple[str, str]] = {}
    if isinstance(records, dict):
        for file_path, rec in records.items():
            if isinstance(rec, dict):
                out[str(file_path)] = _decision_tuple(rec)
    return out


# ---------------------------------------------------------------------------
# Cross-run aggregation
# ---------------------------------------------------------------------------


def _enumerate_sample_ids(run_dir: Path) -> set[str]:
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    return {p.name for p in run_dir.iterdir() if p.is_dir()}


def _validate_sample_alignment(run_dirs: list[Path]) -> set[str]:
    """Confirm every run dir exposes the same set of sample ids.

    Mismatched sets defeat the consistency metric — callers should add the
    missing samples explicitly rather than silently averaging over a
    truncated dataset.
    """
    per_run = [_enumerate_sample_ids(d) for d in run_dirs]
    common = set.intersection(*per_run) if per_run else set()
    union = set.union(*per_run) if per_run else set()
    diff = union - common
    if diff:
        raise ValueError(
            "consistency: runs disagree on sample set; "
            f"missing-somewhere = {sorted(diff)}"
        )
    return common


def _compute_metric(
    run_dirs: list[Path], sample_ids: set[str]
) -> tuple[float, int, list[dict[str, object]]]:
    """Return ``(metric_value, total_files, inconsistent_samples)``.

    The metric is "fraction of (sample, file) pairs that agree across
    every run on ``(decision, risk)``", which is the literal definition
    of DET in metrics.md §6.1 and the equivalent for CPC.
    """
    total_files = 0
    agreeing_files = 0
    inconsistent: list[dict[str, object]] = []
    for sample_id in sorted(sample_ids):
        per_run_records = [_load_records(run_dir / sample_id) for run_dir in run_dirs]
        file_paths: set[str] = set()
        for rec_map in per_run_records:
            file_paths.update(rec_map)
        per_file_decisions: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for rec_map in per_run_records:
            for file_path in file_paths:
                # Missing files in a particular run are recorded as the
                # sentinel tuple so they show up as disagreements rather
                # than silently inflate the consistency score.
                per_file_decisions[file_path].append(
                    rec_map.get(file_path, ("ABSENT", "ABSENT"))
                )
        for file_path in sorted(file_paths):
            total_files += 1
            decisions = per_file_decisions[file_path]
            if all(d == decisions[0] for d in decisions[1:]):
                agreeing_files += 1
            else:
                inconsistent.append(
                    {
                        "sample_id": sample_id,
                        "file_path": file_path,
                        "decisions": [
                            {"decision": d[0], "risk": d[1]} for d in decisions
                        ],
                    }
                )
    value = (agreeing_files / total_files) if total_files else 1.0
    return value, total_files, inconsistent


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def cmd_consistency(
    *,
    run_dirs: list[Path],
    metric: Metric,
    output: Path,
) -> int:
    if len(run_dirs) < 2:
        _eprint(f"consistency: {metric} requires >= 2 runs (got {len(run_dirs)})")
        return 1
    for d in run_dirs:
        if not d.is_dir():
            _eprint(f"consistency: run directory not found: {d}")
            return 1

    try:
        sample_ids = _validate_sample_alignment(run_dirs)
    except ValueError as exc:
        _eprint(f"consistency: {exc}")
        return 1
    except FileNotFoundError as exc:
        _eprint(f"consistency: {exc}")
        return 1

    try:
        value, total_files, inconsistent = _compute_metric(run_dirs, sample_ids)
    except FileNotFoundError as exc:
        _eprint(f"consistency: {exc}")
        return 1

    payload: dict[str, object] = {
        "metric": metric,
        "value": value,
        "n_runs": len(run_dirs),
        "total_files": total_files,
        "inconsistent": inconsistent,
        "run_dirs": [str(d) for d in run_dirs],
    }
    write_json(output, payload)
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.consistency",
        description="Compute DET / CPC across multiple eval runs.",
    )
    parser.add_argument(
        "--runs",
        nargs="+",
        required=True,
        help="One or more runs/ directories (one per repeat).",
    )
    parser.add_argument(
        "--metric",
        choices=("DET", "CPC"),
        required=True,
        help="Which consistency metric to label the report with.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the consistency report (JSON).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return cmd_consistency(
        run_dirs=[Path(d).resolve() for d in args.runs],
        metric=args.metric,
        output=Path(args.output).resolve(),
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
