"""Acceptance gate evaluator (procedure.md §2.5 + acceptance.md §1-§3).

Inputs:
    --report      eval_report_<version>.md   (Phase 5 output)
    --acceptance  acceptance_thresholds.yaml (Phase 6 fixture)
    --baseline    optional eval_report from the previous release
    --output      eval_acceptance_<version>.json

Logic (per [plan-amend] / [test-amend] decision C):

    hard gates  always absolute → ``value`` vs ``threshold``.
    soft gates
        kind=absolute → ``value`` vs ``threshold``.
        kind=relative → ``value`` vs ``baseline_value * multiplier``;
                         SKIPped (pass=null, skipped_reason="no baseline")
                         when ``--baseline`` is omitted or the metric is
                         missing from the baseline report.

Exit codes:
    0   every gate passed (or relative gates were vacuously skipped).
    1   at least one **hard** gate failed (takes priority over soft).
    2   no hard fail, but at least one **soft** gate failed.

Verdict mapping in the emitted ``AcceptanceReport``:
    PASS           — every gate passed.
    FAIL           — any hard gate failed.
    NEEDS_REVIEW   — only soft gates failed.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

from scripts.eval._common import write_json
from scripts.eval._schemas import (
    AcceptanceReport,
    AcceptanceThresholdEntry,
    AcceptanceThresholds,
    GateKind,
    GateResult,
    GateVerdict,
)


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------


_METRIC_ROW = re.compile(r"^\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*([^|]+?)\s*\|")


def parse_metric_table(markdown: str) -> dict[str, float | str]:
    """Extract ``| MetricName | value |`` rows into a ``{name: value}`` map.

    Numeric values are coerced to float; non-numeric values (``"N/A"``,
    ``"N/A (follow-up)"``) round-trip as strings so gate.py can SKIP
    them with an explicit ``skipped_reason``.
    """
    out: dict[str, float | str] = {}
    for line in markdown.splitlines():
        match = _METRIC_ROW.match(line)
        if not match:
            continue
        key = match.group(1)
        raw_value = match.group(2).strip()
        try:
            out[key] = float(raw_value)
        except ValueError:
            out[key] = raw_value
    return out


def load_thresholds(yaml_path: Path) -> AcceptanceThresholds:
    if not yaml_path.is_file():
        raise FileNotFoundError(f"thresholds yaml not found at {yaml_path}")
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return AcceptanceThresholds.model_validate(payload)


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def _operator_passes(operator_value: str, value: float, threshold: float) -> bool:
    """Apply the comparison operator from the threshold entry."""
    if operator_value == "==":
        return value == threshold
    if operator_value == ">=":
        return value >= threshold
    if operator_value == "<=":
        return value <= threshold
    if operator_value == "<":
        return value < threshold
    if operator_value == ">":
        return value > threshold
    raise ValueError(f"unsupported operator: {operator_value!r}")


def _evaluate_absolute_gate(
    entry: AcceptanceThresholdEntry, metrics: dict[str, float | str]
) -> GateResult:
    raw = metrics.get(entry.id)
    if not isinstance(raw, float):
        # Metric absent or non-numeric → can't evaluate; mark SKIPped.
        return GateResult(
            id=entry.id,
            kind=GateKind.ABSOLUTE,
            value=None,
            threshold=entry.threshold,
            operator=entry.operator,
            skipped_reason=f"metric {entry.id!r} not numeric in report",
        )
    if entry.threshold is None:
        # The model_validator already enforces this for kind=absolute, but
        # guard explicitly so the invariant survives ``python -O`` (which
        # strips asserts) and surfaces as a clear error if a future schema
        # tweak breaks the constraint.
        raise ValueError(
            f"gate {entry.id!r}: kind=absolute entry must declare a threshold"
        )
    operator = entry.operator or "<="
    passes = _operator_passes(
        operator.value if hasattr(operator, "value") else str(operator),
        raw,
        entry.threshold,
    )
    return GateResult(
        id=entry.id,
        kind=GateKind.ABSOLUTE,
        value=raw,
        threshold=entry.threshold,
        operator=entry.operator,
        **{"pass": passes},  # type: ignore[arg-type]
    )


def _evaluate_relative_gate(
    entry: AcceptanceThresholdEntry,
    metrics: dict[str, float | str],
    baseline_metrics: dict[str, float | str] | None,
) -> GateResult:
    raw = metrics.get(entry.id)
    if not isinstance(raw, float):
        return GateResult(
            id=entry.id,
            kind=GateKind.RELATIVE,
            value=None,
            multiplier=entry.multiplier,
            skipped_reason=f"metric {entry.id!r} not numeric in report",
        )
    if entry.multiplier is None:
        # Same defensive raise as the absolute path — guards against ``-O``.
        raise ValueError(
            f"gate {entry.id!r}: kind=relative entry must declare a multiplier"
        )
    if baseline_metrics is None:
        return GateResult(
            id=entry.id,
            kind=GateKind.RELATIVE,
            value=raw,
            multiplier=entry.multiplier,
            skipped_reason="no baseline",
        )
    baseline_value = baseline_metrics.get(entry.id)
    if not isinstance(baseline_value, float):
        return GateResult(
            id=entry.id,
            kind=GateKind.RELATIVE,
            value=raw,
            multiplier=entry.multiplier,
            skipped_reason="metric missing from baseline",
        )
    computed = baseline_value * entry.multiplier
    return GateResult(
        id=entry.id,
        kind=GateKind.RELATIVE,
        value=raw,
        multiplier=entry.multiplier,
        baseline_value=baseline_value,
        computed_threshold=computed,
        **{"pass": raw <= computed},  # type: ignore[arg-type]
    )


def _evaluate_entry(
    entry: AcceptanceThresholdEntry,
    metrics: dict[str, float | str],
    baseline_metrics: dict[str, float | str] | None,
) -> GateResult:
    if entry.kind == "relative":
        return _evaluate_relative_gate(entry, metrics, baseline_metrics)
    return _evaluate_absolute_gate(entry, metrics)


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def _derive_verdict(
    hard_results: tuple[GateResult, ...],
    soft_results: tuple[GateResult, ...],
) -> tuple[GateVerdict, int]:
    """Return ``(verdict, exit_code)`` from the gate outcomes.

    Hard failures take strict priority — exit 1 / FAIL even when soft
    gates also failed (Verifier T6-G11).
    """
    hard_failed = any(r.passed is False for r in hard_results)
    soft_failed = any(r.passed is False for r in soft_results)
    if hard_failed:
        return GateVerdict.FAIL, 1
    if soft_failed:
        return GateVerdict.NEEDS_REVIEW, 2
    return GateVerdict.PASS, 0


def cmd_gate(
    *,
    report_path: Path,
    acceptance_yaml: Path,
    baseline_path: Path | None,
    output: Path,
    version: str,
) -> int:
    if not report_path.is_file():
        _eprint(f"gate: report not found: {report_path}")
        return 1
    try:
        thresholds = load_thresholds(acceptance_yaml)
    except FileNotFoundError as exc:
        _eprint(f"gate: {exc}")
        return 1

    metrics = parse_metric_table(report_path.read_text(encoding="utf-8"))
    baseline_metrics: dict[str, float | str] | None = None
    if baseline_path is not None and baseline_path.is_file():
        baseline_metrics = parse_metric_table(baseline_path.read_text(encoding="utf-8"))

    hard_results = tuple(
        _evaluate_entry(entry, metrics, baseline_metrics)
        for entry in thresholds.hard_gates
    )
    soft_results = tuple(
        _evaluate_entry(entry, metrics, baseline_metrics)
        for entry in thresholds.soft_gates
    )
    skipped_relative = sum(
        1
        for r in soft_results
        if r.kind == GateKind.RELATIVE and r.skipped_reason == "no baseline"
    )
    if skipped_relative:
        _eprint(f"gate: skipped {skipped_relative} relative gate(s) due to no baseline")

    verdict, exit_code = _derive_verdict(hard_results, soft_results)

    report = AcceptanceReport(
        version=version,
        baseline=str(baseline_path) if baseline_path else None,
        evaluated_at=datetime.now(timezone.utc),
        datasets={"acceptance_thresholds_sha": thresholds.synced_with_sha},
        model_matrix={},
        hard_gates=hard_results,
        soft_gates=soft_results,
        verdict=verdict,
    )
    write_json(output, report.model_dump(mode="json", by_alias=True))
    return exit_code


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.gate",
        description="Evaluate acceptance gates against an eval report.",
    )
    parser.add_argument("--report", required=True, help="eval_report markdown path.")
    parser.add_argument(
        "--acceptance",
        required=True,
        help="acceptance_thresholds.yaml path.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Optional previous-release eval_report.md for relative gates.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Where to write eval_acceptance_<version>.json.",
    )
    parser.add_argument(
        "--version",
        default="<unknown>",
        help="Free-form version tag inserted into the AcceptanceReport.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return cmd_gate(
        report_path=Path(args.report).resolve(),
        acceptance_yaml=Path(args.acceptance).resolve(),
        baseline_path=Path(args.baseline).resolve() if args.baseline else None,
        output=Path(args.output).resolve(),
        version=args.version,
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
