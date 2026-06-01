"""Emit per-gate LLM-judgment golden JSON from dataset meta.yaml.

For every ``*-SYSTEM`` gate that any judgment-intensive sample labels, writes
``<out-dir>/<gate_id>.golden.json`` in the ``[{case_id, expected_decision}]``
shape consumed by ``merge optimize-prompts --golden``. meta.yaml is the single
source of truth (see ``doc/evaluation/golden.md``); re-run this whenever a
sample's ``judgment_intensive`` / ``golden_decisions`` fields change.

Usage:
    python -m scripts.eval.build_golden                 # defaults below
    python -m scripts.eval.build_golden --tier 1 --tier 3
    python -m scripts.eval.build_golden \
        --datasets tests/eval/datasets --out-dir tests/eval/golden
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.eval._common import write_json
from scripts.eval._golden import build_golden_sets

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASETS_DIR = REPO_ROOT / "tests" / "eval" / "datasets"
DEFAULT_OUT_DIR = REPO_ROOT / "tests" / "eval" / "golden"


def _eprint(message: str) -> None:
    print(message)


def cmd_build(datasets_dir: Path, out_dir: Path, tiers: tuple[int, ...]) -> int:
    golden = build_golden_sets(datasets_dir, tiers=tiers)
    if not golden:
        _eprint(
            "No judgment-intensive golden cases found "
            f"(datasets={datasets_dir}, tiers={list(tiers)})."
        )
        return 0
    for gate_id, cases in golden.items():
        out_path = out_dir / f"{gate_id}.golden.json"
        payload = [case.model_dump(mode="json") for case in cases]
        write_json(out_path, payload, sort_keys=False)
        _eprint(f"{gate_id}: {len(cases)} case(s) -> {out_path}")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.build_golden",
        description="Emit per-gate LLM-judgment golden JSON from dataset meta.yaml.",
    )
    parser.add_argument(
        "--datasets",
        default=str(DEFAULT_DATASETS_DIR),
        help=f"Datasets root (default: {DEFAULT_DATASETS_DIR}).",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"Directory to write <gate>.golden.json into (default: {DEFAULT_OUT_DIR}).",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=(1, 2, 3),
        action="append",
        dest="tiers",
        help="Tier to scan (repeatable). Default: all of 1, 2, 3.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    tiers = tuple(args.tiers) if args.tiers else (1, 2, 3)
    return cmd_build(
        datasets_dir=Path(args.datasets).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        tiers=tiers,
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
