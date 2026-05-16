"""R2 dataset sha256 lock helper (Phase B §B.4).

`scripts/eval/lock.py` TIER_LAYOUT only supports tiers 1/2/3; R2 lives in a
separate `tests/eval/datasets/r2/samples/` tree that is not tier-indexed.
Rather than extend the production lock helper (which would invalidate the
tier1/2/3 lock semantics), R2 uses a parallel manual sha256 helper that
reuses `scripts.eval.lock._sample_sha256` for hash compatibility.

CLI:
    python -m tests.eval.manifests._r2_sha verify
        Recompute sha256 for every R2 sample and compare to r2.lock.json.
        Exit 0 on match, 1 on mismatch (prints diff to stderr).

    python -m tests.eval.manifests._r2_sha update
        Rebuild r2.lock.json from the current on-disk samples.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.eval._common import read_json, write_json
from scripts.eval.lock import EVAL_VERSION, _sample_sha256

REPO_ROOT = Path(__file__).resolve().parents[3]
R2_DATASETS_DIR = REPO_ROOT / "tests" / "eval" / "datasets" / "r2" / "samples"
R2_LOCK_PATH = REPO_ROOT / "tests" / "eval" / "manifests" / "r2.lock.json"


def compute_sample_sha256(sample_dir: Path) -> str:
    """Return the canonical sha256 of one R2 sample directory.

    Thin wrapper around `lock._sample_sha256` — same algorithm so the
    artifact bytes hash to the same digest as the tier1/2/3 path.
    """
    return _sample_sha256(sample_dir)


def list_r2_samples(datasets_dir: Path = R2_DATASETS_DIR) -> list[Path]:
    if not datasets_dir.is_dir():
        return []
    return sorted(p for p in datasets_dir.iterdir() if p.is_dir())


def build_lock_entries(
    datasets_dir: Path = R2_DATASETS_DIR,
) -> list[dict[str, str | int]]:
    entries: list[dict[str, str | int]] = []
    for sample_dir in list_r2_samples(datasets_dir):
        rel = sample_dir.relative_to(datasets_dir.parent).as_posix()
        entry: dict[str, str | int] = {
            "sample_id": sample_dir.name,
            "tier": 2,
            "relative_path": rel,
            "content_sha256": compute_sample_sha256(sample_dir),
        }
        entries.append(entry)
    return entries


def update_lock(
    datasets_dir: Path = R2_DATASETS_DIR, lock_path: Path = R2_LOCK_PATH
) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "eval_version": EVAL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "samples": build_lock_entries(datasets_dir),
    }
    write_json(lock_path, payload)
    return 0


def verify_lock(
    datasets_dir: Path = R2_DATASETS_DIR, lock_path: Path = R2_LOCK_PATH
) -> int:
    if not lock_path.exists():
        print(
            f"r2_sha: missing lock file {lock_path}; run "
            "`python -m tests.eval.manifests._r2_sha update`",
            file=sys.stderr,
        )
        return 1
    locked: dict[str, dict[str, str | int]] = {
        entry["sample_id"]: entry for entry in read_json(lock_path)["samples"]
    }
    on_disk: dict[str, dict[str, str | int]] = {
        str(entry["sample_id"]): entry for entry in build_lock_entries(datasets_dir)
    }
    mismatches: list[str] = []
    for sid, current in on_disk.items():
        if sid not in locked:
            mismatches.append(f"  {sid}: untracked sample (not in lock)")
            continue
        lock_sha = str(locked[sid]["content_sha256"])
        disk_sha = str(current["content_sha256"])
        if lock_sha != disk_sha:
            mismatches.append(
                f"  {sid}: sha mismatch (lock={lock_sha[:12]}…, disk={disk_sha[:12]}…)"
            )
    for sid in locked:
        if sid not in on_disk:
            mismatches.append(f"  {sid}: locked but missing on disk")
    if mismatches:
        print("r2_sha: lock verify FAILED:", file=sys.stderr)
        for line in mismatches:
            print(line, file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests.eval.manifests._r2_sha",
        description="Manual sha256 lock for R2 dataset samples.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="Recompute sha256 and diff against lock file.")
    sub.add_parser("update", help="Rebuild r2.lock.json from on-disk samples.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "update":
        return update_lock()
    if args.cmd == "verify":
        return verify_lock()
    raise AssertionError(f"unhandled cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
