"""Drive ``merge`` (or a fake stand-in) over an evaluation tier.

Per sample, ``run.py``:
    1. Creates an isolated cwd at ``<workdir>/runs/<sample_id>/_cwd``.
    2. Asserts that ``<cwd>/.merge/memory.db`` does NOT exist
       (cross-sample memory-leak guard — see plan §Phase 3 GO §3 +
       approved-facts ``[plan]`` Memory form).
    3. Spawns the merge subprocess with :func:`scripts.eval._common.eval_subprocess_env`
       (strips ``MERGE_DEV``, injects dummy LLM keys) plus a per-sample
       ``HOME=<workdir>/home``.
    4. After the subprocess exits, copies five artifact families up to
       ``<workdir>/runs/<sample_id>/`` and writes ``run_meta.json``.

Concurrency is controlled by an :class:`asyncio.Semaphore`; per-sample
failures are isolated. The runner ALWAYS goes through
:func:`scripts.eval.prepare.cmd_prepare` first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from scripts.eval import lock as lock_mod
from scripts.eval import prepare as prepare_mod
from scripts.eval._common import eval_subprocess_env, write_json
from scripts.eval._schemas import RunMeta

MEMORY_DB_RELATIVE = Path(".merge/memory.db")
"""On-disk memory location (SQLite single file, see approved-facts ``[plan]``)."""


class MemoryLeakDetected(Exception):
    """Raised before spawning a sample's subprocess if its cwd already
    contains a stale ``.merge/memory.db`` from a prior run."""

    def __init__(self, sample_id: str, path: Path) -> None:
        self.sample_id = sample_id
        self.path = path
        super().__init__(
            f"memory leak detected for sample {sample_id}: {path} already exists"
        )


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _assert_clean_memory(cwd: Path, sample_id: str) -> None:
    memory_db = cwd / MEMORY_DB_RELATIVE
    if memory_db.exists():
        raise MemoryLeakDetected(sample_id, memory_db)


def _git_sha() -> str:
    """Best-effort current HEAD sha; never raises."""
    import subprocess as _sp

    try:
        out = _sp.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, _sp.SubprocessError):
        pass
    return "unknown"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_working_tree(src_cwd: Path, dest: Path) -> None:
    """Copy everything under ``src_cwd`` except the ``.merge/`` directory."""
    _ensure_dir(dest)
    for item in src_cwd.iterdir():
        if item.name == ".merge":
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _locate_merge_run_dir(cwd_merge: Path) -> Path | None:
    """Return the single ``<cwd>/.merge/runs/<run_id>/`` directory, if any."""
    runs_root = cwd_merge / "runs"
    if not runs_root.is_dir():
        return None
    children = [p for p in runs_root.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return None


def _copy_merge_artifacts(run_dir: Path, dest: Path) -> None:
    """Copy reports + checkpoint from the subprocess's run directory."""
    _ensure_dir(dest)
    for child in run_dir.iterdir():
        if child.is_file():
            shutil.copy2(child, dest / child.name)


def _persist_ci_summary(stdout_text: str, dest: Path) -> dict[str, Any]:
    """Materialise ``stdout`` as ``ci_summary.json``; tolerate non-JSON."""
    parsed: dict[str, Any]
    try:
        loaded = json.loads(stdout_text or "{}")
        parsed = loaded if isinstance(loaded, dict) else {"raw_value": loaded}
    except json.JSONDecodeError:
        parsed = {"invalid_json": True, "raw_stdout": stdout_text}
    write_json(dest, parsed)
    return parsed


def _build_run_meta(
    *,
    sample_id: str,
    run_id: str,
    seed: int,
    concurrency: int,
    wall_time_seconds: float,
    cost_usd: float,
    git_sha: str,
    status: str,
    memory_clean_check: str,
    exit_code: int,
    cache_disabled: bool,
) -> RunMeta:
    return RunMeta(
        sample_id=sample_id,
        run_id=run_id,
        seed=seed,
        concurrency=concurrency,
        cache_disabled=cache_disabled,
        wall_time_seconds=wall_time_seconds,
        cost_usd=cost_usd,
        git_sha=git_sha,
        status=status,  # type: ignore[arg-type]
        memory_clean_check=memory_clean_check,  # type: ignore[arg-type]
        exit_code=exit_code,
    )


async def _run_one_sample(
    *,
    sample_dir: Path,
    workdir: Path,
    merge_bin: str,
    merge_args: list[str],
    seed: int,
    concurrency: int,
    use_real_keys: bool,
    home_dir: Path,
    git_sha: str,
) -> int:
    """Run merge for a single prepared sample. Returns the merge exit code."""
    sample_id = sample_dir.name
    sample_out = _ensure_dir(workdir / "runs" / sample_id)
    cwd = _ensure_dir(sample_out / "_cwd")
    prepared_tree = sample_dir / "working_tree"
    if prepared_tree.is_dir():
        for item in prepared_tree.iterdir():
            target = cwd / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    _assert_clean_memory(cwd, sample_id)

    env = eval_subprocess_env(use_real_keys=use_real_keys)
    env["HOME"] = str(home_dir)
    env.setdefault("FAKE_SEED", str(seed))

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        merge_bin,
        *merge_args,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    elapsed = time.monotonic() - started
    exit_code = proc.returncode if proc.returncode is not None else -1

    stdout_text = stdout_b.decode("utf-8", errors="replace")
    stderr_text = stderr_b.decode("utf-8", errors="replace")

    ci_payload = _persist_ci_summary(stdout_text, sample_out / "ci_summary.json")
    if stderr_text:
        (sample_out / "stderr.log").write_text(stderr_text, encoding="utf-8")

    if exit_code == 0:
        cwd_merge = cwd / ".merge"
        run_subdir = _locate_merge_run_dir(cwd_merge)
        if run_subdir is not None:
            _copy_merge_artifacts(run_subdir, sample_out)
        _copy_working_tree(cwd, sample_out / "working_tree")

    run_id_value = ci_payload.get("run_id")
    run_id = (
        str(run_id_value)
        if isinstance(run_id_value, str)
        else f"unknown-{uuid.uuid4().hex[:8]}"
    )
    status = "success" if exit_code == 0 else "failed"

    meta = _build_run_meta(
        sample_id=sample_id,
        run_id=run_id,
        seed=seed,
        concurrency=concurrency,
        wall_time_seconds=elapsed,
        cost_usd=0.0,
        git_sha=git_sha,
        status=status,
        memory_clean_check="passed",
        exit_code=exit_code,
        cache_disabled=False,
    )
    write_json(sample_out / "run_meta.json", meta.model_dump(mode="json"))
    return exit_code


async def cmd_run(
    *,
    tier: int,
    workdir: Path,
    concurrency: int,
    merge_bin: str,
    merge_args: list[str],
    seed: int,
    use_real_keys: bool,
    datasets_dir: Path,
    manifests_dir: Path,
) -> int:
    if concurrency < 1:
        _eprint("run: concurrency must be >= 1")
        return 2

    workdir = _ensure_dir(workdir)
    home_dir = _ensure_dir(workdir / "home")
    prepared_root = workdir / "_prepare"

    prepare_rc = prepare_mod.cmd_prepare(
        tier=tier,
        out_dir=prepared_root,
        datasets_dir=datasets_dir,
        manifests_dir=manifests_dir,
    )
    if prepare_rc != 0:
        _eprint(f"run: prepare failed (rc={prepare_rc})")
        return prepare_rc

    sample_dirs = sorted(p for p in prepared_root.iterdir() if p.is_dir())
    if not sample_dirs:
        _eprint(f"run: no prepared samples for tier {tier}")
        return 0

    git_sha = _git_sha()
    semaphore = asyncio.Semaphore(concurrency)
    leak_failures = 0

    async def _bounded(sample_dir: Path) -> int:
        nonlocal leak_failures
        async with semaphore:
            try:
                return await _run_one_sample(
                    sample_dir=sample_dir,
                    workdir=workdir,
                    merge_bin=merge_bin,
                    merge_args=merge_args,
                    seed=seed,
                    concurrency=concurrency,
                    use_real_keys=use_real_keys,
                    home_dir=home_dir,
                    git_sha=git_sha,
                )
            except MemoryLeakDetected as exc:
                _eprint(f"run: {exc}")
                leak_failures += 1
                return 99

    results = await asyncio.gather(*(_bounded(sd) for sd in sample_dirs))
    failures = sum(1 for rc in results if rc != 0)
    if failures or leak_failures:
        return 1
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.run",
        description="Drive a prepared evaluation tier through the merge CLI.",
    )
    parser.add_argument("--tier", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--workdir", required=True, help="Output / scratch directory.")
    parser.add_argument(
        "--concurrency", type=int, default=1, help="Max parallel merge subprocesses."
    )
    parser.add_argument(
        "--merge-bin",
        required=True,
        help="Path to the merge binary (or fake stand-in).",
    )
    parser.add_argument(
        "--merge-args",
        default="",
        help="Space-separated args appended to merge-bin invocations.",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="Seed forwarded as FAKE_SEED env var."
    )
    parser.add_argument(
        "--use-real-keys",
        action="store_true",
        help="Forward real LLM keys instead of dummy values (default off).",
    )
    parser.add_argument(
        "--datasets",
        default=str(lock_mod.DEFAULT_DATASETS_DIR),
        help=f"Datasets root (default: {lock_mod.DEFAULT_DATASETS_DIR}).",
    )
    parser.add_argument(
        "--manifests",
        default=str(lock_mod.DEFAULT_MANIFESTS_DIR),
        help=f"Manifests directory (default: {lock_mod.DEFAULT_MANIFESTS_DIR}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    merge_args = args.merge_args.split() if args.merge_args else []
    if args.concurrency < 1:
        _eprint("run: concurrency must be >= 1")
        return 2
    return asyncio.run(
        cmd_run(
            tier=args.tier,
            workdir=Path(args.workdir).resolve(),
            concurrency=args.concurrency,
            merge_bin=str(Path(args.merge_bin).resolve()),
            merge_args=merge_args,
            seed=args.seed,
            use_real_keys=args.use_real_keys,
            datasets_dir=Path(args.datasets).resolve(),
            manifests_dir=Path(args.manifests).resolve(),
        )
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
