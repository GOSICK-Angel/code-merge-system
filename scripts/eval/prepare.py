"""Expand evaluation samples into a workdir for the runner to consume.

For each sample under the requested tier:
    1. Validate dataset integrity by re-using ``lock.cmd_verify`` (cheap,
       single source of truth).
    2. Untar ``base.tar`` into ``<workdir>/<sample_id>/working_tree/``.
    3. Apply ``fork.patch`` (so ``working_tree/`` reflects the fork's
       pre-merge state — which is what the system-under-test will be
       asked to merge upstream into during Phase 3).
    4. Untar ``golden.tar`` into ``<workdir>/<sample_id>/golden_tree/``.
    5. Copy ``meta.yaml`` verbatim and write an ``apply_log.txt`` with
       the patch application trace (one line per file touched, plus the
       resulting per-file byte size).

Exit codes:
    0  every sample expanded successfully.
    1  lock verification failed — caller should run ``lock.py --update``.
    2  one or more samples failed to expand (corrupted patch, missing
       golden, etc); details on stderr.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path

from unidiff import PatchedFile, PatchSet  # type: ignore[import-untyped]
from unidiff.errors import UnidiffParseError  # type: ignore[import-untyped]

from scripts.eval import lock as lock_mod
from scripts.eval._common import atomic_write_text
from scripts.eval._ground_truth import (
    GroundTruthError,
    GroundTruthMissing,
    load_golden_tree,
    load_meta,
)
from scripts.eval._schemas import SampleMeta


class PatchApplyError(Exception):
    """Raised when ``fork.patch`` cannot be parsed or applied cleanly."""

    def __init__(self, sample_id: str, patch_name: str, message: str) -> None:
        self.sample_id = sample_id
        self.patch_name = patch_name
        super().__init__(f"[{sample_id}] {patch_name}: {message}")


def _eprint(message: str) -> None:
    """Re-resolved stderr write so pytest ``capsys`` captures the output."""
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Tar / patch primitives
# ---------------------------------------------------------------------------


def _safe_extract_tar(
    sample_id: str, tar_path: Path, target_dir: Path
) -> dict[str, bytes]:
    """Extract a tar archive into ``target_dir`` and return the materialised map.

    Refuses absolute paths and parent traversal (defense-in-depth against
    malicious fixtures). Returns ``{relative_path: bytes}`` so the caller
    can chain a patch application without re-reading from disk.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, bytes] = {}
    try:
        with tarfile.open(tar_path, "r") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise PatchApplyError(
                        sample_id,
                        tar_path.name,
                        f"unsafe path in archive: {member.name}",
                    )
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                data = fh.read()
                out[member.name] = data
                dest = target_dir / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
    except tarfile.TarError as exc:
        raise PatchApplyError(
            sample_id, tar_path.name, f"not a valid tar archive: {exc}"
        ) from exc
    return out


def _apply_patch_to_tree(
    sample_id: str,
    patch_name: str,
    patch_bytes: bytes,
    tree: dict[str, bytes],
) -> tuple[dict[str, bytes], list[str]]:
    """Apply a unified-diff patch to ``tree`` and return ``(new_tree, log)``.

    Pure function — the input ``tree`` is not mutated; a new dict is
    returned. Empty patches are valid (e.g. ``t3-m3-0001/fork.patch``)
    and produce a single ``no-op`` log entry plus a shallow copy of the
    input tree.

    Raises :class:`PatchApplyError` on parse failure or context mismatch.
    """
    new_tree: dict[str, bytes] = dict(tree)
    if not patch_bytes.strip():
        return new_tree, [f"{patch_name}: no-op (empty patch)"]
    try:
        patch_set = PatchSet(patch_bytes.decode("utf-8"))
    except (UnidiffParseError, UnicodeDecodeError, ValueError) as exc:
        raise PatchApplyError(sample_id, patch_name, f"parse failed: {exc}") from exc
    if len(patch_set) == 0:
        # PatchSet silently swallows non-empty input that lacks any
        # ``---/+++/@@`` markers. Refuse rather than producing an empty log,
        # which would let corrupted fixtures slip through prepare unnoticed.
        raise PatchApplyError(
            sample_id,
            patch_name,
            "non-empty input parsed to zero file hunks (likely malformed)",
        )
    log: list[str] = []
    for patched_file in patch_set:
        rel_path = _patched_file_target_path(patched_file)
        before = new_tree.get(rel_path, b"")
        after = _apply_patched_file(sample_id, patch_name, before, patched_file)
        new_tree[rel_path] = after
        log.append(f"{patch_name}: {rel_path} ({len(before)}B -> {len(after)}B)")
    return new_tree, log


def _patched_file_target_path(patched_file: PatchedFile) -> str:
    """Strip the ``a/`` / ``b/`` git-style prefix to get the on-disk path."""
    target: str = str(patched_file.target_file or patched_file.path)
    if target.startswith("b/") or target.startswith("a/"):
        return target[2:]
    return target


def _apply_patched_file(
    sample_id: str,
    patch_name: str,
    before: bytes,
    patched_file: PatchedFile,
) -> bytes:
    """Re-create the post-patch content of one file by walking its hunks."""
    text_before = before.decode("utf-8") if before else ""
    src_lines = text_before.splitlines(keepends=True)
    out_lines: list[str] = []
    cursor = 0
    for hunk in patched_file:
        # Copy untouched lines preceding this hunk.
        hunk_start = max(hunk.source_start - 1, 0)
        if hunk_start < cursor:
            raise PatchApplyError(
                sample_id,
                patch_name,
                f"hunks for {patched_file.path} overlap or are out of order",
            )
        out_lines.extend(src_lines[cursor:hunk_start])
        cursor = hunk_start
        for line in hunk:
            if line.is_context:
                if cursor >= len(src_lines):
                    raise PatchApplyError(
                        sample_id,
                        patch_name,
                        f"context past EOF in {patched_file.path}",
                    )
                out_lines.append(src_lines[cursor])
                cursor += 1
            elif line.is_removed:
                if cursor >= len(src_lines):
                    raise PatchApplyError(
                        sample_id,
                        patch_name,
                        f"removed line past EOF in {patched_file.path}",
                    )
                cursor += 1
            elif line.is_added:
                out_lines.append(line.value)
    out_lines.extend(src_lines[cursor:])
    return "".join(out_lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Per-sample expansion
# ---------------------------------------------------------------------------


def _expand_sample(sample_dir: Path, out_dir: Path) -> None:
    """Materialise one sample under ``<out_dir>/<sample_id>/``."""
    sample_id = sample_dir.name
    target = out_dir / sample_id
    working_tree = target / "working_tree"
    golden_tree = target / "golden_tree"
    target.mkdir(parents=True, exist_ok=True)

    base_tar = sample_dir / "base.tar"
    if not base_tar.is_file():
        raise GroundTruthMissing(sample_id, "base.tar")
    base_tree = _safe_extract_tar(sample_id, base_tar, working_tree)

    fork_patch = sample_dir / "fork.patch"
    if not fork_patch.is_file():
        raise GroundTruthMissing(sample_id, "fork.patch")
    forked_tree, log_lines = _apply_patch_to_tree(
        sample_id, "fork.patch", fork_patch.read_bytes(), base_tree
    )
    _write_tree_overlay(working_tree, forked_tree)

    # load_golden_tree raises GroundTruthMissing if golden.tar absent.
    golden = load_golden_tree(sample_dir)
    _write_tree_overlay(golden_tree, golden)

    meta = load_meta(sample_dir)
    atomic_write_text(target / "meta.yaml", _serialise_meta_yaml(meta))

    atomic_write_text(target / "apply_log.txt", "\n".join(log_lines) + "\n")


def _write_tree_overlay(target_dir: Path, files: dict[str, bytes]) -> None:
    """Write an in-memory file map to disk under ``target_dir``."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, data in files.items():
        dest = target_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _serialise_meta_yaml(meta: SampleMeta) -> str:
    """Render a :class:`SampleMeta` back to yaml for the workdir copy."""
    import yaml as _yaml

    return _yaml.safe_dump(
        meta.model_dump(exclude_none=True), sort_keys=True, allow_unicode=True
    )


# ---------------------------------------------------------------------------
# CLI / driver
# ---------------------------------------------------------------------------


def cmd_prepare(
    tier: int,
    out_dir: Path,
    datasets_dir: Path,
    manifests_dir: Path,
) -> int:
    # 1. Pre-flight: lock verify. acceptance_yaml=None explicitly skips
    # the acceptance.md / yaml sync check — that pairing is gate.py's
    # concern, not prepare's. (Replaces the Phase 2 sentinel-path hack.)
    lock_rc = lock_mod.cmd_verify(
        datasets_dir,
        manifests_dir,
        acceptance_md=lock_mod.DEFAULT_ACCEPTANCE_MD,
        acceptance_yaml=None,
        is_ci=False,
    )
    if lock_rc != 0:
        _eprint(
            "prepare: lock verify failed, run lock.py --update first "
            "(or fix the dataset)"
        )
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    samples = lock_mod._iter_samples(datasets_dir, tier)
    if not samples:
        _eprint(f"prepare: no samples found for tier {tier} under {datasets_dir}")
        # Empty tier (e.g. tier-2 placeholder) is not an error — still exit 0.
        return 0

    failures = 0
    for sample_dir in samples:
        try:
            _expand_sample(sample_dir, out_dir)
        except (GroundTruthError, PatchApplyError) as exc:
            _eprint(f"prepare: {exc}")
            failures += 1
    return 0 if failures == 0 else 2


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.prepare",
        description="Expand evaluation samples into a workdir.",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=(1, 2, 3),
        required=True,
        help="Tier to expand (1, 2, or 3).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output workdir; one sub-directory per sample is created.",
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
    return cmd_prepare(
        tier=args.tier,
        out_dir=Path(args.out).resolve(),
        datasets_dir=Path(args.datasets).resolve(),
        manifests_dir=Path(args.manifests).resolve(),
    )


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
