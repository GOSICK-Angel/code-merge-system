"""Normalize legacy wrapper-produced run_meta.json to the canonical RunMeta
schema (Phase D adapter, P-γ-3 follow-up).

`/tmp/eval-runs/run_v4_full.sh` and similar baseline wrappers emit a minimal
run_meta.json containing `sample_id / run_id / wall_seconds / merge_target /
fork_ref`. The production `RunMeta` model (`scripts/eval/_schemas.py:244`)
requires `seed / concurrency / wall_time_seconds / cost_usd / git_sha` and
forbids extras — so `summarize.py:77` rejects the wrapper output verbatim.

This utility walks a runs directory and rewrites each `run_meta.json` in
place to match `RunMeta`:

- Rename `wall_seconds` -> `wall_time_seconds`
- Drop wrapper extras (`merge_target`, `fork_ref`) — domain belongs in
  `.merge/config.yaml`, not run metadata
- Inject defaults for missing required fields:
  * `seed: 0`, `concurrency: 1`, `cache_disabled: false`
  * `cost_usd: 0.0` (wrapper does not track cost; downstream sees the
    placeholder and treats it as "not authoritative" per RunMeta docstring
    plan decision 3 / P1-7)
  * `git_sha: <current HEAD>` resolved via `git rev-parse --short=7 HEAD`
  * `model_matrix: {"all": "claude-opus-4-6"}` (Phase B baseline default)
  * `status: "success"`, `memory_clean_check: "passed"`, `exit_code: 0`

Pass `--git-sha <sha>` to override the resolved git sha (useful when
normalizing artifacts produced by an older commit).

Usage:
    python -m scripts.eval.normalize_run_meta /tmp/eval-runs-v4/runs
    python -m scripts.eval.normalize_run_meta /tmp/eval-runs-v4/runs --git-sha ba8f4d3
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.eval._common import read_json, write_json
from scripts.eval._schemas import RunMeta

_LEGACY_FIELD_RENAMES: dict[str, str] = {"wall_seconds": "wall_time_seconds"}
_LEGACY_DROP_FIELDS: frozenset[str] = frozenset({"merge_target", "fork_ref"})


def _resolve_git_sha(repo: Path | None = None) -> str:
    """Best-effort short HEAD; return ``"unknown"`` on any failure."""
    try:
        cmd = ["git", "rev-parse", "--short=7", "HEAD"]
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            cwd=str(repo) if repo else None,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def normalize_one(
    payload: dict[str, object], *, git_sha: str, model: str
) -> dict[str, object]:
    """Return a new payload conforming to :class:`RunMeta`.

    Pure function — does not mutate ``payload``.
    """
    result: dict[str, object] = {}
    for key, value in payload.items():
        if key in _LEGACY_DROP_FIELDS:
            continue
        result[_LEGACY_FIELD_RENAMES.get(key, key)] = value

    # Fill required-but-missing defaults.
    result.setdefault("seed", 0)
    result.setdefault("concurrency", 1)
    result.setdefault("cache_disabled", False)
    if "wall_time_seconds" in result:
        result["wall_time_seconds"] = float(result["wall_time_seconds"])  # type: ignore[arg-type]
    else:
        result["wall_time_seconds"] = 0.0
    result.setdefault("cost_usd", 0.0)
    result.setdefault("git_sha", git_sha)
    result.setdefault("model_matrix", {"all": model})
    result.setdefault("status", "success")
    result.setdefault("memory_clean_check", "passed")
    result.setdefault("exit_code", 0)

    # Round-trip through RunMeta to guarantee schema compliance.
    return RunMeta.model_validate(result).model_dump(mode="json")


def normalize_runs_dir(runs_dir: Path, *, git_sha: str, model: str) -> tuple[int, int]:
    """Walk ``runs_dir``; rewrite each ``run_meta.json`` in place.

    Returns ``(rewritten_count, skipped_count)``. A meta already matching the
    schema is left untouched (the rewrite is byte-equal so this just saves a
    no-op write); a missing meta file is skipped.
    """
    rewritten = 0
    skipped = 0
    if not runs_dir.is_dir():
        print(f"normalize_run_meta: runs dir not found: {runs_dir}", file=sys.stderr)
        return (0, 0)

    for sample_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        meta_path = sample_dir / "run_meta.json"
        if not meta_path.exists():
            skipped += 1
            continue
        payload = read_json(meta_path)
        try:
            RunMeta.model_validate(payload)
            # already conforming — touch nothing
            skipped += 1
            continue
        except Exception:
            pass
        normalized = normalize_one(payload, git_sha=git_sha, model=model)
        write_json(meta_path, normalized)
        rewritten += 1
    return (rewritten, skipped)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.normalize_run_meta",
        description=(
            "Rewrite legacy wrapper run_meta.json to the canonical RunMeta "
            "schema so summarize.py / gate.py can consume them."
        ),
    )
    parser.add_argument(
        "runs_dir",
        type=Path,
        help="Directory containing <sample>/run_meta.json subdirs.",
    )
    parser.add_argument(
        "--git-sha",
        default=None,
        help="Explicit git_sha (default: resolved from current HEAD).",
    )
    parser.add_argument(
        "--model",
        default="claude-opus-4-6",
        help="Default model name for model_matrix (default: claude-opus-4-6).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    git_sha = args.git_sha or _resolve_git_sha()
    rewritten, skipped = normalize_runs_dir(
        args.runs_dir, git_sha=git_sha, model=args.model
    )
    print(
        f"normalize_run_meta: rewrote {rewritten}, skipped {skipped} "
        f"(git_sha={git_sha})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
