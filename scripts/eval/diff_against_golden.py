"""Compute the per-sample diff between system output and golden truth.

Inputs (per sample):
    runs/<sample_id>/working_tree/                — D_sys (system output)
    runs/<sample_id>/merge_report_<run_id>.json   — per-file decisions
                                                    (single source of truth,
                                                    plan §Phase 4 GO §2 +
                                                    [plan] approved-fact)
    datasets-out/<sample_id>/golden_tree/         — D_gold (ground truth,
                                                    produced by prepare.py)
    datasets-out/<sample_id>/meta.yaml            — sample metadata

Output:
    --output diff.json — :class:`scripts.eval._schemas.DiffReport` shape.

Per-sample MISMATCH labels (procedure.md §3.2 + plan decision 1):
    MISS_UPSTREAM — D_gold contains a path / line absent from D_sys
                    (system did not bring upstream's change forward).
    MISS_FORK     — D_sys discarded a path that D_gold preserves
                    (system over-rolled-back a fork-only change).
    WRONG_MERGE   — same path in both, but the contents differ even after
                    the suffix-aware semantic equivalence check.
    EXTRA_NOISE   — D_sys contains a path absent from D_gold
                    (system invented a file).

The output includes :class:`scripts.eval._schemas.DiffReportMeta` whose
``semantic_engine`` is ``"tree-sitter"`` when every per-file comparison
that *could* have used tree-sitter actually had it available, otherwise
``"fallback-bytes"`` (plan decision 4 / P1-5 — never claim AST equality
when the fallback decided the call).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from scripts.eval._ast_equiv import (
    BINARY_SUFFIXES,
    SemanticEngine,
    UnsupportedFileType,
    is_equivalent,
)
from scripts.eval._common import write_json
from scripts.eval._ground_truth import (
    GroundTruthError,
    load_golden_tree,
    load_meta,
)
from scripts.eval._schemas import (
    DiffEntry,
    DiffReport,
    DiffReportMeta,
    MatchStatus,
    MismatchLabel,
    SystemDecision,
)


class RunArtifactMissing(Exception):
    """Raised when a required per-sample artifact is absent from runs/<id>/."""

    def __init__(self, sample_id: str, missing: str) -> None:
        self.sample_id = sample_id
        self.missing = missing
        super().__init__(f"[{sample_id}] required run artifact missing: {missing}")


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Per-file scanning
# ---------------------------------------------------------------------------


def _walk_tree(root: Path) -> dict[str, bytes]:
    """Read every regular file under ``root`` into ``{rel_posix: bytes}``."""
    out: dict[str, bytes] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = path.read_bytes()
    return out


def _classify_pair(
    rel_path: str,
    sys_bytes: bytes | None,
    gold_bytes: bytes | None,
) -> tuple[MatchStatus, MismatchLabel | None, SemanticEngine | None]:
    """Classify one (sys, gold) byte pair into (match, label, engine).

    Both args may be ``None`` to encode "absent on that side". The
    returned ``engine`` is ``None`` when the comparison was not driven
    by :func:`scripts.eval._ast_equiv.is_equivalent` (e.g. either side
    is missing).
    """
    if sys_bytes is None and gold_bytes is None:
        # Phantom call — should not happen because the union is iterated.
        return MatchStatus.EXACT, None, None
    if sys_bytes is None:
        return MatchStatus.MISMATCH, MismatchLabel.MISS_UPSTREAM, None
    if gold_bytes is None:
        return MatchStatus.MISMATCH, MismatchLabel.EXTRA_NOISE, None
    suffix = Path(rel_path).suffix or ".bin"
    try:
        equal, engine = is_equivalent(sys_bytes, gold_bytes, suffix=suffix)
    except UnsupportedFileType:
        equal = sys_bytes == gold_bytes
        engine = "exact-bytes"
    if equal:
        match = MatchStatus.EXACT if engine == "exact-bytes" else MatchStatus.SEMANTIC
        return match, None, engine
    return MatchStatus.MISMATCH, MismatchLabel.WRONG_MERGE, engine


def _line_counts(sys_bytes: bytes | None, gold_bytes: bytes | None) -> tuple[int, int]:
    """Return ``(missed_lines, extra_lines)`` based on simple line diff."""
    sys_lines = (sys_bytes or b"").splitlines()
    gold_lines = (gold_bytes or b"").splitlines()
    sys_set = set(sys_lines)
    gold_set = set(gold_lines)
    missed = sum(1 for line in gold_lines if line not in sys_set)
    extra = sum(1 for line in sys_lines if line not in gold_set)
    return missed, extra


# ---------------------------------------------------------------------------
# merge_report ingestion
# ---------------------------------------------------------------------------


def _locate_merge_report(run_dir: Path) -> Path:
    matches = sorted(run_dir.glob("merge_report_*.json"))
    if not matches:
        raise RunArtifactMissing(run_dir.name, "merge_report_<run_id>.json")
    if len(matches) > 1:
        # Multiple runs in one directory should not happen in practice.
        # Pick the lexicographically last to be deterministic.
        return matches[-1]
    return matches[0]


def _load_decision_records(run_dir: Path) -> dict[str, dict[str, Any]]:
    report_path = _locate_merge_report(run_dir)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    records = payload.get("file_decision_records", {})
    if not isinstance(records, dict):
        return {}
    return {str(k): dict(v) for k, v in records.items() if isinstance(v, dict)}


def _decision_to_system_decision(record: dict[str, Any]) -> SystemDecision:
    strategy = str(record.get("decision") or record.get("strategy") or "UNKNOWN")
    risk = str(record.get("target_risk_level") or record.get("risk") or "UNKNOWN")
    decision_source = str(record.get("decision_source") or "")
    human = decision_source == "human" or decision_source == "batch_human"
    return SystemDecision(strategy=strategy, risk=risk, human=human)


# ---------------------------------------------------------------------------
# Per-sample diff
# ---------------------------------------------------------------------------


def _diff_one_sample(
    sample_id: str,
    run_dir: Path,
    dataset_sample_dir: Path,
) -> tuple[DiffEntry, list[SemanticEngine]]:
    """Build one DiffEntry and return the engines used along the way."""
    working_tree = run_dir / "working_tree"
    if not working_tree.is_dir():
        raise RunArtifactMissing(sample_id, "working_tree/")
    decisions = _load_decision_records(run_dir)

    sys_files = _walk_tree(working_tree)
    gold_files = load_golden_tree(dataset_sample_dir)
    meta = load_meta(dataset_sample_dir)

    # Aggregate per-file label / counts. The DiffEntry is per-sample so
    # we collapse the per-file labels into the most severe mismatch
    # (MISS_UPSTREAM/MISS_FORK/WRONG_MERGE/EXTRA_NOISE).
    label: MismatchLabel | None = None
    match: MatchStatus = MatchStatus.EXACT
    engines: list[SemanticEngine] = []
    total_missed = 0
    total_extra = 0
    for rel_path in sorted(set(sys_files) | set(gold_files)):
        per_match, per_label, per_engine = _classify_pair(
            rel_path, sys_files.get(rel_path), gold_files.get(rel_path)
        )
        if per_engine is not None:
            engines.append(per_engine)
        missed, extra = _line_counts(sys_files.get(rel_path), gold_files.get(rel_path))
        total_missed += missed
        total_extra += extra
        if per_match is MatchStatus.MISMATCH:
            match = MatchStatus.MISMATCH
            label = _escalate_label(label, per_label)
        elif per_match is MatchStatus.SEMANTIC and match is MatchStatus.EXACT:
            match = MatchStatus.SEMANTIC

    # Pick the first decision record (one-sample / one-file fixtures) for
    # SystemDecision ergonomics. Real multi-file samples will be Phase 5.
    primary_record = next(iter(decisions.values()), {})
    system_decision = _decision_to_system_decision(primary_record)

    rationale_length = len(str(primary_record.get("rationale", "")))
    discarded_present = bool(primary_record.get("discarded_content"))
    is_security_sensitive = bool(primary_record.get("is_security_sensitive"))

    entry = DiffEntry(
        sample_id=sample_id,
        category=str(meta.category),
        loss_class=meta.loss_class,
        expected_human=meta.expected_human,
        system_decision=system_decision,
        match=match,
        label=label,
        missed_lines=total_missed,
        extra_lines=total_extra,
        rationale_length=rationale_length,
        discarded_content_present=discarded_present,
        is_security_sensitive=is_security_sensitive,
    )
    return entry, engines


def _escalate_label(
    current: MismatchLabel | None, new: MismatchLabel | None
) -> MismatchLabel | None:
    """Resolve label aggregation across multiple per-file mismatches.

    Priority (highest → lowest): WRONG_MERGE > MISS_UPSTREAM > MISS_FORK
    > EXTRA_NOISE. Reflects severity for the verdict — a real wrong
    merge dominates a missed upstream which dominates noise.
    """
    if new is None:
        return current
    if current is None:
        return new
    priority = {
        MismatchLabel.WRONG_MERGE: 4,
        MismatchLabel.MISS_UPSTREAM: 3,
        MismatchLabel.MISS_FORK: 2,
        MismatchLabel.EXTRA_NOISE: 1,
    }
    return new if priority[new] > priority[current] else current


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def _summarise_engine(
    engines: Iterable[SemanticEngine],
) -> Literal["tree-sitter", "fallback-bytes"]:
    """Pick the single ``meta.semantic_engine`` value for the report.

    Narrowed to the 2-element union accepted by
    :class:`scripts.eval._schemas.DiffReportMeta` — every other engine
    name (``json-canonical`` / ``yaml-canonical`` / ``exact-bytes``)
    collapses to ``fallback-bytes`` for the top-level summary because
    those paths do not exercise tree-sitter parsing.
    """
    engines_list = list(engines)
    if not engines_list:
        return "fallback-bytes"
    if all(e == "tree-sitter" for e in engines_list):
        return "tree-sitter"
    return "fallback-bytes"


def cmd_diff(
    *,
    runs_dir: Path,
    datasets_dir: Path,
    output: Path,
    tier: int,
) -> int:
    if not runs_dir.is_dir():
        _eprint(f"diff: runs directory not found: {runs_dir}")
        return 1
    if not datasets_dir.is_dir():
        _eprint(f"diff: datasets directory not found: {datasets_dir}")
        return 1

    sample_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    entries: list[DiffEntry] = []
    all_engines: list[SemanticEngine] = []
    failures = 0
    for run_subdir in sample_dirs:
        sample_id = run_subdir.name
        dataset_sample_dir = datasets_dir / sample_id
        if not dataset_sample_dir.is_dir():
            _eprint(
                f"diff: dataset directory missing for sample {sample_id}: "
                f"{dataset_sample_dir}"
            )
            failures += 1
            continue
        try:
            entry, engines = _diff_one_sample(sample_id, run_subdir, dataset_sample_dir)
        except (RunArtifactMissing, GroundTruthError) as exc:
            _eprint(f"diff: {exc}")
            failures += 1
            continue
        entries.append(entry)
        all_engines.extend(engines)

    report = DiffReport(
        tier=tier,
        samples=tuple(entries),
        meta=DiffReportMeta(semantic_engine=_summarise_engine(all_engines)),
    )
    write_json(output, report.model_dump(mode="json"))
    return 0 if failures == 0 else 2


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.diff_against_golden",
        description="Compute per-sample diff between merge run and golden tree.",
    )
    parser.add_argument(
        "--runs",
        required=True,
        help="Directory holding runs/<sample_id>/ subdirectories.",
    )
    parser.add_argument(
        "--datasets",
        required=True,
        help="Datasets-out root (prepare.py output) holding sample golden_tree/.",
    )
    parser.add_argument(
        "--output", required=True, help="Path of the diff.json file to write."
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=(1, 2, 3),
        required=True,
        help="Tier label written into the report header.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return cmd_diff(
        runs_dir=Path(args.runs).resolve(),
        datasets_dir=Path(args.datasets).resolve(),
        output=Path(args.output).resolve(),
        tier=args.tier,
    )


# Suppress unused-import warning for BINARY_SUFFIXES (used only in tests).
_ = BINARY_SUFFIXES


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
