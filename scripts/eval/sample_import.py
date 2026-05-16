"""Sample-import helper for Tier-1 / Tier-3 dataset construction.

Captures one merge from a real git repository into the canonical
``base.tar / upstream.patch / fork.patch / golden.tar / meta.yaml``
five-file format consumed by ``scripts.eval.prepare``. The goal is to
cut the per-sample human cost from ~30 minutes to ~10 minutes by
automating the mechanical capture work; only ``meta.yaml`` classification
fields (``category`` / ``expected_risk`` / ``golden_strategy`` / etc.)
remain for the annotator.

Resolution order when ``--from-merge <sha>`` is given::

    base_ref     = git merge-base <merge_sha>^1 <merge_sha>^2
    upstream_ref = <merge_sha>^2   (second parent — merged-in side)
    fork_ref     = <merge_sha>^1   (first parent — receiving branch)
    golden_ref   = <merge_sha>     (actual merged tree)

Any of these can be overridden individually with
``--base-ref / --upstream-ref / --fork-ref / --golden-ref`` for
non-merge-commit workflows (rebase / squash PRs whose ground truth is
a tree not directly reachable as a merge commit).

The captured file set is the union of paths touched by any of the three
diffs (base→upstream, base→fork, base→golden). Pass ``--all-files`` to
snapshot the entire working tree at each ref — useful for adversarial
Tier-3 samples whose context matters beyond the touched files.

The emitted ``meta.yaml`` is a *skeleton*: classification fields are
left blank (e.g. ``category: TBD``) and **must be filled in by the
annotator** before the sample is committed. ``scripts.eval._schemas``
rejects ``TBD`` placeholders so a half-finished sample cannot silently
land in a lock file.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

from scripts.eval._common import atomic_write_text

# Same fixed mtime as the existing reference samples / test fixtures
# (``tests/eval/unit/test_diff_against_golden.py:FIXED_MTIME``) so a tar
# produced by this script byte-matches a tar produced by the test
# helpers — keeps ``lock --update`` sha256 stable across hosts.
FIXED_MTIME = 1767225600


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """Run ``git -C <repo> <args>`` capturing stdout (text)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _git_bytes(repo: Path, *args: str) -> bytes:
    """Run ``git -C <repo> <args>`` capturing raw stdout bytes."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _resolve_ref(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


@dataclass(frozen=True)
class _Refs:
    base: str
    upstream: str
    fork: str
    golden: str


def _derive_refs_from_merge(repo: Path, merge_sha: str) -> _Refs:
    fork = _resolve_ref(repo, f"{merge_sha}^1")
    upstream = _resolve_ref(repo, f"{merge_sha}^2")
    base = _git(repo, "merge-base", fork, upstream).strip()
    golden = _resolve_ref(repo, merge_sha)
    return _Refs(base=base, upstream=upstream, fork=fork, golden=golden)


# ---------------------------------------------------------------------------
# Diff + file-set helpers
# ---------------------------------------------------------------------------


def _diff_paths(repo: Path, base_ref: str, head_ref: str) -> list[str]:
    """Files touched by ``git diff <base>..<head>`` (added/modified/deleted)."""
    out = _git(repo, "diff", "--name-only", base_ref, head_ref)
    return [line for line in out.splitlines() if line]


def _capture_patch(repo: Path, base_ref: str, head_ref: str) -> str:
    """Capture a binary-safe unified diff for the patch file.

    ``--no-color`` and ``--no-ext-diff`` keep the output reproducible
    across user git configs.
    """
    return _git(
        repo,
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--binary",
        base_ref,
        head_ref,
    )


def _resolve_file_set(repo: Path, refs: _Refs, all_files: bool) -> list[str]:
    """Return the sorted file paths to capture in the tars."""
    if all_files:
        out = _git(repo, "ls-tree", "-r", "--name-only", refs.golden)
        return sorted(line for line in out.splitlines() if line)
    union: set[str] = set()
    for head in (refs.upstream, refs.fork, refs.golden):
        union.update(_diff_paths(repo, refs.base, head))
    return sorted(union)


# ---------------------------------------------------------------------------
# Tar writer (byte-stable)
# ---------------------------------------------------------------------------


def _tar_tree_at_ref(repo: Path, ref: str, file_set: list[str], target: Path) -> None:
    """Write a USTAR tarball capturing ``file_set`` at ``ref``.

    Settings (mtime / mode / format / sorted order) match the test
    fixture helpers so the file is byte-identical regardless of host
    umask, locale, or git user config.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w", format=tarfile.USTAR_FORMAT) as tf:
        for path in sorted(file_set):
            try:
                blob = _git_bytes(repo, "show", f"{ref}:{path}")
            except subprocess.CalledProcessError:
                # File absent at this ref — skip (tar omits missing
                # entries; consumers infer add/delete from patches).
                continue
            info = tarfile.TarInfo(name=path)
            info.size = len(blob)
            info.mtime = FIXED_MTIME
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(blob))


# ---------------------------------------------------------------------------
# Meta.yaml skeleton
# ---------------------------------------------------------------------------


_META_TEMPLATE = """sample_id: {sample_id}
tier: {tier}
category: TBD            # ABCDE (see doc/evaluation/dataset.md §2.2)
expected_risk: TBD       # AUTO_SAFE / AUTO_RISKY / HUMAN_REQUIRED
loss_class: null         # null or M1-M6 for Tier-3 / M-injected samples
expected_human: false    # bool — should the system escalate?
golden_strategy: TBD     # SEMANTIC_MERGE / FORK_KEEP / UPSTREAM_TAKE / ESCALATE_HUMAN
description: |
  TBD — one-line summary of base/upstream/fork divergence and the
  golden resolution rationale.
notes_provenance:
  repo: {repo}
  base_ref: {base}
  upstream_ref: {upstream}
  fork_ref: {fork}
  golden_ref: {golden}
"""


def _write_meta_skeleton(
    target: Path, *, sample_id: str, tier: int, repo: Path, refs: _Refs
) -> None:
    body = _META_TEMPLATE.format(
        sample_id=sample_id,
        tier=tier,
        repo=str(repo),
        base=refs.base,
        upstream=refs.upstream,
        fork=refs.fork,
        golden=refs.golden,
    )
    atomic_write_text(target, body)


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def cmd_import(
    *,
    repo: Path,
    sample_id: str,
    tier: int,
    out_root: Path,
    refs: _Refs,
    all_files: bool,
) -> int:
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        _eprint(f"sample_import: {repo} is not a git repository")
        return 1
    sample_dir = out_root / sample_id
    if sample_dir.exists() and any(sample_dir.iterdir()):
        _eprint(f"sample_import: refuse to overwrite non-empty {sample_dir}")
        return 1
    sample_dir.mkdir(parents=True, exist_ok=True)

    file_set = _resolve_file_set(repo, refs, all_files=all_files)
    if not file_set:
        _eprint(
            "sample_import: no files touched by base→{upstream,fork,golden} "
            "— refusing to write an empty sample"
        )
        return 1

    _tar_tree_at_ref(repo, refs.base, file_set, sample_dir / "base.tar")
    _tar_tree_at_ref(repo, refs.golden, file_set, sample_dir / "golden.tar")
    atomic_write_text(
        sample_dir / "upstream.patch",
        _capture_patch(repo, refs.base, refs.upstream),
    )
    atomic_write_text(
        sample_dir / "fork.patch",
        _capture_patch(repo, refs.base, refs.fork),
    )
    _write_meta_skeleton(
        sample_dir / "meta.yaml",
        sample_id=sample_id,
        tier=tier,
        repo=repo,
        refs=refs,
    )
    print(
        f"sample_import: wrote {sample_dir} "
        f"({len(file_set)} files, base={refs.base[:8]}, golden={refs.golden[:8]})"
    )
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.sample_import",
        description=(
            "Capture one merge from a real git repository into the "
            "base.tar/upstream.patch/fork.patch/golden.tar/meta.yaml "
            "sample format."
        ),
    )
    parser.add_argument("--repo", required=True, help="Path to the git repository.")
    parser.add_argument(
        "--sample-id",
        required=True,
        help="Sample id (e.g. t1-0002).",
    )
    parser.add_argument(
        "--tier", type=int, choices=(1, 2, 3), required=True, help="Dataset tier."
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output root (e.g. tests/eval/datasets/tier1/samples).",
    )
    parser.add_argument(
        "--from-merge",
        help="Merge commit sha — derives base/upstream/fork/golden from ^1/^2.",
    )
    parser.add_argument("--base-ref", help="Override base ref.")
    parser.add_argument("--upstream-ref", help="Override upstream-side ref.")
    parser.add_argument("--fork-ref", help="Override fork-side ref.")
    parser.add_argument("--golden-ref", help="Override golden (final tree) ref.")
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Capture the whole tree, not just the diff-touched union.",
    )
    return parser


def _resolve_refs(repo: Path, args: argparse.Namespace) -> _Refs:
    overrides = (args.base_ref, args.upstream_ref, args.fork_ref, args.golden_ref)
    if args.from_merge and any(overrides):
        derived = _derive_refs_from_merge(repo, args.from_merge)
        return _Refs(
            base=_resolve_ref(repo, args.base_ref) if args.base_ref else derived.base,
            upstream=(
                _resolve_ref(repo, args.upstream_ref)
                if args.upstream_ref
                else derived.upstream
            ),
            fork=_resolve_ref(repo, args.fork_ref) if args.fork_ref else derived.fork,
            golden=(
                _resolve_ref(repo, args.golden_ref)
                if args.golden_ref
                else derived.golden
            ),
        )
    if args.from_merge:
        return _derive_refs_from_merge(repo, args.from_merge)
    if not all(overrides):
        raise ValueError(
            "must pass either --from-merge or all four of "
            "--base-ref/--upstream-ref/--fork-ref/--golden-ref"
        )
    return _Refs(
        base=_resolve_ref(repo, args.base_ref),
        upstream=_resolve_ref(repo, args.upstream_ref),
        fork=_resolve_ref(repo, args.fork_ref),
        golden=_resolve_ref(repo, args.golden_ref),
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        refs = _resolve_refs(repo, args)
    except (ValueError, subprocess.CalledProcessError) as exc:
        _eprint(f"sample_import: {exc}")
        return 1
    return cmd_import(
        repo=repo,
        sample_id=args.sample_id,
        tier=args.tier,
        out_root=Path(args.out).resolve(),
        refs=refs,
        all_files=args.all_files,
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
