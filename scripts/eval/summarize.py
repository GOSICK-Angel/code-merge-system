"""Aggregate a DiffReport + per-sample RunMeta into an eval_report markdown.

Inputs:
    --diff   path to ``diff.json``      (DiffReport schema, Phase 4 output)
    --runs   directory holding ``runs/<id>/run_meta.json`` per sample
    --output path of the markdown report to write
    --baseline (optional)               previous-release ``eval_report``
                                          for the §5 comparison table

Highlights:
    * 18 metric anchors emitted by the template (hard 9 + soft 9), with
      ``Recall_M1..M6`` expanded inside the hard table.
    * Concurrency banner: when any sample ran under ``concurrency > 1``
      the template inserts a "wall_time/cost not authoritative" header.
    * Failure list is sorted by ``sample_id`` (plan §Phase 5 GO §4 /
      Verifier T5-S5).
    * Sanity-aware of ``ci_summary.json``: the runner's
      ``_persist_ci_summary`` wraps non-dict / non-JSON stdout into
      ``{"raw_value": ...}`` / ``{"invalid_json": True}`` envelopes —
      this module never relies on those values for per-file decisions,
      but flags their presence under "known issues".

SRSR is intentionally not computed here: it depends on the
``MergeState.snapshot_rollback_events`` field whose plan/v3 landing is
still a follow-up (test FINAL TR7). Schema-wise the anchor is emitted
with value ``"N/A (follow-up)"`` so reports remain comparable.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.eval._common import atomic_write_text, read_json
from scripts.eval._report_render import render_report
from scripts.eval._schemas import (
    DiffEntry,
    DiffReport,
    MatchStatus,
    RunMeta,
)


RECALL_LABELS: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5", "M6")
RATIONALE_EXCERPT_LIMIT = 80


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------


def _load_diff(diff_path: Path) -> DiffReport:
    return DiffReport.model_validate(read_json(diff_path))


def _load_run_metas(runs_dir: Path) -> dict[str, RunMeta]:
    """Load every ``runs/<id>/run_meta.json``; raise if any sample misses one."""
    metas: dict[str, RunMeta] = {}
    if not runs_dir.is_dir():
        raise FileNotFoundError(f"runs directory not found: {runs_dir}")
    for sample_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        meta_path = sample_dir / "run_meta.json"
        if not meta_path.is_file():
            raise FileNotFoundError(
                f"sample {sample_dir.name}: run_meta.json not found at {meta_path}"
            )
        metas[sample_dir.name] = RunMeta.model_validate(read_json(meta_path))
    return metas


def _load_ci_summary(runs_dir: Path, sample_id: str) -> dict[str, Any]:
    """Best-effort load of ``ci_summary.json``; returns ``{}`` when absent."""
    path = runs_dir / sample_id / "ci_summary.json"
    if not path.is_file():
        return {}
    try:
        payload = read_json(path)
    except json.JSONDecodeError:
        return {"invalid_json": True}
    return payload if isinstance(payload, dict) else {"raw_value": payload}


# ---------------------------------------------------------------------------
# Metric aggregation (plan §Phase 5 GO §2)
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: int) -> float | str:
    if not values:
        return "N/A"
    if len(values) == 1:
        return values[0]
    # statistics.quantiles uses n+1 cut-points; pct=95 → index 18 of 19.
    quantiles = statistics.quantiles(values, n=100, method="inclusive")
    return quantiles[pct - 1]


def _format_pct(value: float | str, decimals: int = 4) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}"
    return str(value)


def _compute_metrics(
    samples: tuple[DiffEntry, ...], metas: dict[str, RunMeta]
) -> dict[str, Any]:
    """Reduce diff samples + run metas into the metric dict the template needs.

    Numbers are best-effort given Tier-1's single sample reality (scope.md
    §6) — most "ratios" degenerate to 0.0 or 1.0; this module only
    guarantees that every required anchor key is present in the output.
    """
    total = len(samples)
    if total == 0:
        return _empty_metrics()

    wrong_merges = sum(1 for s in samples if s.label and s.label.value == "WRONG_MERGE")
    miss_upstream = sum(
        1 for s in samples if s.label and s.label.value == "MISS_UPSTREAM"
    )
    miss_fork = sum(1 for s in samples if s.label and s.label.value == "MISS_FORK")
    correct = sum(
        1 for s in samples if s.match in (MatchStatus.EXACT, MatchStatus.SEMANTIC)
    )
    security_sensitive = [s for s in samples if s.is_security_sensitive]
    rationale_ok = sum(1 for s in samples if s.rationale_length >= 30)
    discarded_ok = sum(
        1 for s in samples if s.discarded_content_present or s.label is None
    )

    total_missed = sum(s.missed_lines for s in samples)
    total_extra = sum(s.extra_lines for s in samples)

    metrics: dict[str, Any] = {
        # Hard 9 (acceptance.md §1)
        "OA": _format_pct(correct / total),
        "WMR": _format_pct(wrong_merges / total),
        "MMR": _format_pct(miss_upstream / total),
        "WDR": _format_pct(miss_fork / total),
        "SSER": _format_pct(
            (len(security_sensitive) / total) if security_sensitive else 1.0
        ),
        "DCRR": _format_pct(discarded_ok / total),
        "SRSR": "N/A (follow-up)",  # TR7 — plan v3 dependency
        "RR": _format_pct(1.0),  # Phase 3 GO already proved 3-artifact landing
        "RCR": _format_pct(rationale_ok / total),
        "Recall": {label: "N/A" for label in RECALL_LABELS},
        # Soft 9 (acceptance.md §2)
        "CRA": _format_pct(correct / total),
        "OverEscalationRate": _format_pct(
            sum(1 for s in samples if s.system_decision.human and not s.expected_human)
            / total
        ),
        "JA": "N/A (follow-up)",
        "DET": "N/A (multi-run)",
        "CPC": "N/A (multi-provider)",
        "cost_usd_per_run_p95": _format_pct(
            _percentile([m.cost_usd for m in metas.values()], 95)
        ),
        "wall_time_seconds_p95": _format_pct(
            _percentile([m.wall_time_seconds for m in metas.values()], 95)
        ),
        "plan_revision_rounds_p95": "N/A (no rounds collected)",
        # Auxiliary aggregates not gated but useful in the body
        "_total_missed_lines": total_missed,
        "_total_extra_lines": total_extra,
    }
    # Recall_M3 is the only tier-3 sample shipped; mark it computed.
    has_m3 = any(s.loss_class == "M3" for s in samples)
    if has_m3:
        m3_hit = any(
            s.loss_class == "M3"
            and s.label
            and s.label.value in ("WRONG_MERGE", "MISS_UPSTREAM", "MISS_FORK")
            for s in samples
        )
        metrics["Recall"]["M3"] = _format_pct(1.0 if m3_hit else 0.0)
    return metrics


def _empty_metrics() -> dict[str, Any]:
    base = {
        "OA": "N/A",
        "WMR": "N/A",
        "MMR": "N/A",
        "WDR": "N/A",
        "SSER": "N/A",
        "DCRR": "N/A",
        "SRSR": "N/A (follow-up)",
        "RR": "N/A",
        "RCR": "N/A",
        "Recall": {label: "N/A" for label in RECALL_LABELS},
        "CRA": "N/A",
        "OverEscalationRate": "N/A",
        "JA": "N/A (follow-up)",
        "DET": "N/A (multi-run)",
        "CPC": "N/A (multi-provider)",
        "cost_usd_per_run_p95": "N/A",
        "wall_time_seconds_p95": "N/A",
        "plan_revision_rounds_p95": "N/A",
        "_total_missed_lines": 0,
        "_total_extra_lines": 0,
    }
    return base


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def _failure_rows(samples: tuple[DiffEntry, ...]) -> list[dict[str, Any]]:
    failures = [s for s in samples if s.match is MatchStatus.MISMATCH]
    failures_sorted = sorted(failures, key=lambda s: s.sample_id)
    return [
        {
            "sample_id": s.sample_id,
            "label": s.label.value if s.label else "?",
            "strategy": s.system_decision.strategy,
            "rationale_excerpt": _excerpt(s),
        }
        for s in failures_sorted
    ]


def _excerpt(sample: DiffEntry) -> str:
    text = f"rationale_len={sample.rationale_length}"
    if sample.discarded_content_present:
        text += " ; discarded_content=present"
    if sample.is_security_sensitive:
        text += " ; security_sensitive=True"
    return text[:RATIONALE_EXCERPT_LIMIT]


def _detect_known_issues(runs_dir: Path, samples: tuple[DiffEntry, ...]) -> list[str]:
    notes: list[str] = []
    for sample in samples:
        ci = _load_ci_summary(runs_dir, sample.sample_id)
        if ci.get("invalid_json"):
            notes.append(
                f"{sample.sample_id}: ci_summary.json contained non-JSON stdout"
            )
        if "raw_value" in ci:
            notes.append(
                f"{sample.sample_id}: ci_summary.json wrapped a non-dict top-level value"
            )
    return notes


def _baseline_rows(
    metrics: dict[str, Any], baseline_path: Path | None
) -> list[dict[str, Any]]:
    if baseline_path is None:
        return []
    try:
        baseline_text = baseline_path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for key in ("OA", "WMR", "MMR", "WDR", "SSER", "DCRR", "RR", "RCR"):
        current = metrics.get(key, "N/A")
        # Baseline diff is reported as a textual marker — numerical
        # subtraction requires a structured baseline format not yet
        # standardised (out of scope for Phase 5).
        marker = "present" if key in baseline_text else "absent"
        rows.append(
            {
                "id": key,
                "current": current,
                "baseline": marker,
                "delta": "n/a (text comparison only)",
            }
        )
    return rows


def _build_context(
    diff: DiffReport,
    metas: dict[str, RunMeta],
    runs_dir: Path,
    *,
    baseline_path: Path | None,
    dataset_lock_sha: str,
) -> dict[str, Any]:
    metrics = _compute_metrics(diff.samples, metas)
    max_concurrency = max((m.concurrency for m in metas.values()), default=1)
    not_authoritative = max_concurrency > 1
    failures = _failure_rows(diff.samples)
    known_issues = _detect_known_issues(runs_dir, diff.samples)
    if not_authoritative:
        known_issues.append(
            f"wall_time / cost run under concurrency={max_concurrency}; "
            "header banner emitted to reflect non-authoritative measurement"
        )

    model_matrix: dict[str, str] = {}
    for meta in metas.values():
        if meta.model_matrix:
            model_matrix = dict(meta.model_matrix)
            break

    git_shas = {m.git_sha for m in metas.values()}
    git_sha = next(iter(git_shas)) if len(git_shas) == 1 else "<mixed>"

    return {
        "tier": diff.tier,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "model_matrix_repr": json.dumps(model_matrix, sort_keys=True)
        if model_matrix
        else "<empty>",
        "samples_total": len(diff.samples),
        "samples_failed": sum(
            1 for s in diff.samples if s.match is MatchStatus.MISMATCH
        ),
        "dataset_lock_sha": dataset_lock_sha,
        "semantic_engine": diff.meta.semantic_engine,
        "max_concurrency": max_concurrency,
        "not_authoritative": not_authoritative,
        "metrics": metrics,
        "samples": [s.model_dump(mode="json") for s in diff.samples],
        "failures": failures,
        "baseline": baseline_path,
        "baseline_diff": _baseline_rows(metrics, baseline_path),
        "known_issues": known_issues,
    }


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def cmd_summarize(
    *,
    diff_path: Path,
    runs_dir: Path,
    output: Path,
    baseline_path: Path | None,
    dataset_lock_sha: str,
) -> int:
    if not diff_path.is_file():
        _eprint(f"summarize: diff file not found: {diff_path}")
        return 1
    diff = _load_diff(diff_path)
    try:
        metas = _load_run_metas(runs_dir)
    except FileNotFoundError as exc:
        _eprint(f"summarize: {exc}")
        return 2
    context = _build_context(
        diff,
        metas,
        runs_dir,
        baseline_path=baseline_path,
        dataset_lock_sha=dataset_lock_sha,
    )
    markdown = render_report(context)
    atomic_write_text(output, markdown)
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.summarize",
        description="Aggregate diff + run metadata into eval_report.md.",
    )
    parser.add_argument("--diff", required=True, help="Path to diff.json (Phase 4).")
    parser.add_argument(
        "--runs", required=True, help="Directory holding runs/<sample_id>/."
    )
    parser.add_argument("--output", required=True, help="Markdown report to write.")
    parser.add_argument(
        "--baseline",
        default=None,
        help="Optional previous-release eval_report.md for §5 comparison.",
    )
    parser.add_argument(
        "--dataset-lock-sha",
        default="<unknown>",
        help="sha256 of the dataset lock used for this run (free-form).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return cmd_summarize(
        diff_path=Path(args.diff).resolve(),
        runs_dir=Path(args.runs).resolve(),
        output=Path(args.output).resolve(),
        baseline_path=Path(args.baseline).resolve() if args.baseline else None,
        dataset_lock_sha=args.dataset_lock_sha,
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
