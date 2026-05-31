"""Bootstrap a synthetic 3-branch git repository from an evaluation sample.

The merge CLI expects a live git repository with both ``upstream_ref`` and
``fork_ref`` resolvable. Evaluation samples are stored as
``base.tar / upstream.patch / fork.patch / golden.tar / meta.yaml`` — flat
trees, not git history. This helper bridges the gap so a real
``python -m src.cli.main merge upstream`` invocation can drive a sample
end-to-end and the eval framework can score the resulting merge_report
against ``golden.tar``.

Layout after :func:`bootstrap_synthetic_repo`::

    <target>/
    .git/
    .gitignore       # ignores .merge/ so the merge CLI's runtime
                     # artifacts don't pollute the synthetic history
    <files>          # post-fork-patch tree (HEAD of `main`)

Branches::

    main      = base ⊕ fork.patch   (the fork-side; default checkout)
    upstream  = base ⊕ upstream.patch

Either patch may be empty — when one side did not touch the captured
scope ``git commit --allow-empty`` keeps the branch present so the merge
CLI's ref resolution succeeds.

This module deliberately stays out of the locked Phase 3 ``run.py``
contract. It is a pre-step that ``run.py`` (or any future evaluation
driver) can call before spawning the merge subprocess.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


@dataclass(frozen=True)
class RefBundle:
    """The three commit shas produced by :func:`bootstrap_synthetic_repo`."""

    base: str
    upstream: str
    fork: str


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Eval",
    "GIT_AUTHOR_EMAIL": "eval@local",
    "GIT_COMMITTER_NAME": "Eval",
    "GIT_COMMITTER_EMAIL": "eval@local",
    # Fixed timestamps so the synthetic shas are reproducible across
    # hosts — matches the byte-stable mtime used by sample_import.py.
    "GIT_AUTHOR_DATE": "2025-12-31T16:00:00Z",
    "GIT_COMMITTER_DATE": "2025-12-31T16:00:00Z",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    return result.stdout


def _git_apply(repo: Path, patch_path: Path) -> None:
    """Apply ``patch_path`` to the working tree of ``repo``.

    Patches captured from real git history occasionally trip the
    whitespace heuristic (trailing spaces in yaml indentation, etc.);
    ``--whitespace=nowarn`` keeps capture-time byte fidelity without
    polluting stderr.
    """
    subprocess.run(
        ["git", "-C", str(repo), "apply", "--whitespace=nowarn", str(patch_path)],
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def _extract_tar(tar_path: Path, target: Path) -> None:
    """Safely extract a USTAR sample tar into ``target``.

    Rejects member names with absolute paths or ``..`` traversal — the
    same filter ``_ground_truth.load_golden_tree`` uses, kept duplicated
    so this module has no dependency on the loader layer.
    """
    with tarfile.open(tar_path, "r") as tf:
        for member in tf.getmembers():
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"git_bootstrap: tar contains unsafe path {name!r}")
        tf.extractall(target)  # noqa: S202  -- validated above


def bootstrap_synthetic_repo(sample_dir: Path, target_dir: Path) -> RefBundle:
    """Materialise a 3-branch git repo from one evaluation sample.

    Args:
        sample_dir: directory containing ``base.tar / upstream.patch /
            fork.patch / meta.yaml`` (golden.tar is intentionally not
            consumed — the eval scorer reads it separately).
        target_dir: an empty (or non-existent) directory to receive the
            synthetic repo.

    Returns:
        :class:`RefBundle` with the three commit shas.

    Raises:
        FileNotFoundError: a required sample artifact is missing.
        ValueError: ``target_dir`` is non-empty.
        subprocess.CalledProcessError: a ``git`` operation failed.
    """
    base_tar = sample_dir / "base.tar"
    upstream_patch = sample_dir / "upstream.patch"
    fork_patch = sample_dir / "fork.patch"
    for required in (base_tar, upstream_patch, fork_patch):
        if not required.is_file():
            raise FileNotFoundError(
                f"git_bootstrap: required artifact missing: {required}"
            )

    if target_dir.exists() and any(target_dir.iterdir()):
        raise ValueError(
            f"git_bootstrap: refuse to bootstrap into non-empty {target_dir}"
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    _extract_tar(base_tar, target_dir)
    _git(target_dir, "init", "-q", "-b", "main")
    # Keep the merge CLI's runtime artifacts (.merge/, outputs/) out of
    # git operations without touching ``.gitignore`` — overwriting the
    # base tree's .gitignore breaks samples where upstream.patch has
    # hunks on .gitignore (line numbers shift). ``.git/info/exclude`` is
    # local-only, not part of the working tree, and applied on top of
    # any existing .gitignore.
    exclude_path = target_dir / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    exclude_path.write_text(existing + ".merge/\noutputs/\n", encoding="utf-8")
    _git(target_dir, "add", "-A")
    _git(target_dir, "commit", "-q", "-m", "base")
    base_sha = _git(target_dir, "rev-parse", "HEAD").strip()

    _git(target_dir, "checkout", "-q", "-b", "upstream")
    if upstream_patch.stat().st_size > 0:
        _git_apply(target_dir, upstream_patch)
        _git(target_dir, "add", "-A")
        _git(target_dir, "commit", "-q", "-m", "upstream change")
    else:
        _git(target_dir, "commit", "-q", "--allow-empty", "-m", "upstream (no-op)")
    upstream_sha = _git(target_dir, "rev-parse", "HEAD").strip()

    _git(target_dir, "checkout", "-q", "main")
    if fork_patch.stat().st_size > 0:
        _git_apply(target_dir, fork_patch)
        _git(target_dir, "add", "-A")
        _git(target_dir, "commit", "-q", "-m", "fork change")
    else:
        _git(target_dir, "commit", "-q", "--allow-empty", "-m", "fork (no-op)")
    fork_sha = _git(target_dir, "rev-parse", "HEAD").strip()

    return RefBundle(base=base_sha, upstream=upstream_sha, fork=fork_sha)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.git_bootstrap",
        description=(
            "Materialise a 3-branch git repo from one evaluation sample. "
            "Pre-step for driving real `merge` CLI against eval datasets."
        ),
    )
    parser.add_argument(
        "--sample",
        required=True,
        help="Sample directory (containing base.tar / *.patch / meta.yaml).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory for the synthetic git repo (must be empty).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    sample_dir = Path(args.sample).resolve()
    target_dir = Path(args.out).resolve()
    try:
        refs = bootstrap_synthetic_repo(sample_dir, target_dir)
    except (FileNotFoundError, ValueError) as exc:
        _eprint(f"git_bootstrap: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        _eprint(
            "git_bootstrap: git failure — "
            f"{exc.stderr.decode('utf-8', 'replace') if exc.stderr else exc}"
        )
        return 1
    print(
        f"git_bootstrap: wrote {target_dir} "
        f"(base={refs.base[:8]}, upstream={refs.upstream[:8]}, fork={refs.fork[:8]})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
