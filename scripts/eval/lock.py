"""Dataset & acceptance-threshold lock manager.

Sub-commands:
    --update                       Rebuild ``tests/eval/manifests/tier{1,2,3}.lock.json``
                                    from the current contents of the dataset tree.
    --verify                       Recompute sha256 hashes and exit non-zero on
                                    mismatch. Also cross-checks
                                    ``acceptance_thresholds.yaml.synced_with_sha``
                                    against the live ``acceptance.md`` (warn
                                    locally, fail when ``CI=true`` is set).
    --update-acceptance-sync       Refresh only the ``synced_with_sha`` /
                                    ``synced_at`` fields of
                                    ``acceptance_thresholds.yaml`` — never
                                    touches the threshold table.

Single-source sha256 algorithm: see :func:`_sample_sha256` — every other
caller (tests, future Phase) MUST go through this function so the hash
stays stable across refactors.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.eval._common import read_json, write_json
from scripts.eval._schemas import ManifestEntry, TierManifest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASETS_DIR = REPO_ROOT / "tests" / "eval" / "datasets"
DEFAULT_MANIFESTS_DIR = REPO_ROOT / "tests" / "eval" / "manifests"
DEFAULT_ACCEPTANCE_MD = REPO_ROOT / "doc" / "evaluation" / "acceptance.md"
DEFAULT_ACCEPTANCE_YAML = DEFAULT_MANIFESTS_DIR / "acceptance_thresholds.yaml"
EVAL_VERSION = "0.1.0"

ARTIFACT_FILES: tuple[str, ...] = (
    "base.tar",
    "fork.patch",
    "golden.tar",
    "meta.yaml",
    "upstream.patch",
)
"""Canonical (sorted) artifact filenames per sample. The sha256 mixes the
five files in this exact order — changing the tuple invalidates every
existing lock.json."""

TIER_LAYOUT: dict[int, str] = {
    1: "tier1/samples",
    2: "tier2/replays",
    3: "tier3/adversarial",
}
"""Per-tier sample container directory inside ``DEFAULT_DATASETS_DIR``."""


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return the sha256 hex digest of ``path``'s raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sample_sha256(sample_dir: Path) -> str:
    """Canonical sha256 of one sample directory.

    Mixes :data:`ARTIFACT_FILES` in fixed order with their lengths to make
    the hash insensitive to filesystem ordering and immune to splitting /
    joining adjacent files. ``meta.yaml`` is the only required file; any
    of the patch / tar files may be empty (legitimate for a "no fork
    changes" scenario like ``t3-m3-0001``) but the entry MUST exist.
    """
    h = hashlib.sha256()
    for name in ARTIFACT_FILES:
        target = sample_dir / name
        if not target.exists():
            raise FileNotFoundError(
                f"sample {sample_dir.name} missing required artifact: {name}"
            )
        data = target.read_bytes()
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Manifest scanning
# ---------------------------------------------------------------------------


def _iter_samples(datasets_dir: Path, tier: int) -> list[Path]:
    """Return sorted sample directories for ``tier`` under ``datasets_dir``."""
    container = datasets_dir / TIER_LAYOUT[tier]
    if not container.is_dir():
        return []
    return sorted(p for p in container.iterdir() if p.is_dir())


def _build_manifest(datasets_dir: Path, tier: int) -> TierManifest:
    """Walk one tier and produce a fully-populated :class:`TierManifest`."""
    entries: list[ManifestEntry] = []
    for sample_dir in _iter_samples(datasets_dir, tier):
        rel = sample_dir.relative_to(datasets_dir).as_posix()
        entries.append(
            ManifestEntry(
                sample_id=sample_dir.name,
                tier=tier,
                relative_path=rel,
                content_sha256=_sample_sha256(sample_dir),
            )
        )
    return TierManifest(tier=tier, eval_version=EVAL_VERSION, samples=tuple(entries))


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def cmd_update(datasets_dir: Path, manifests_dir: Path) -> int:
    manifests_dir.mkdir(parents=True, exist_ok=True)
    for tier in (1, 2, 3):
        manifest = _build_manifest(datasets_dir, tier)
        write_json(
            manifests_dir / f"tier{tier}.lock.json",
            manifest.model_dump(mode="json"),
        )
    return 0


def _eprint(message: str) -> None:
    """Emit ``message`` to the *current* :data:`sys.stderr`.

    Re-resolved on every call so test fixtures that swap
    ``sys.stderr`` (e.g. pytest ``capsys``) capture the output.
    """
    print(message, file=sys.stderr)


def cmd_verify(
    datasets_dir: Path,
    manifests_dir: Path,
    acceptance_md: Path,
    acceptance_yaml: Path | None,
    *,
    is_ci: bool,
) -> int:
    """Verify on-disk datasets against their lock.json + acceptance sync check.

    ``acceptance_yaml=None`` explicitly skips the sync check; this is the
    clean Phase 6 replacement for the Phase 2 sentinel-path workaround
    (``manifests_dir / "__no_such_acceptance_yaml__.yaml"``).
    """
    rc = 0
    for tier in (1, 2, 3):
        manifest_path = manifests_dir / f"tier{tier}.lock.json"
        if not manifest_path.exists():
            _eprint(
                f"verify: missing manifest {manifest_path.name}; run lock.py --update"
            )
            rc = 1
            continue
        recorded = TierManifest.model_validate(read_json(manifest_path))
        try:
            recomputed = _build_manifest(datasets_dir, tier)
        except FileNotFoundError as exc:
            _eprint(f"verify: tier{tier} sample missing artifact: {exc}")
            rc = 1
            continue
        recorded_map = {e.sample_id: e.content_sha256 for e in recorded.samples}
        recomputed_map = {e.sample_id: e.content_sha256 for e in recomputed.samples}
        for sample_id in sorted(set(recorded_map) | set(recomputed_map)):
            old = recorded_map.get(sample_id)
            new = recomputed_map.get(sample_id)
            if old is None:
                _eprint(
                    f"verify: tier{tier} {sample_id} present on disk but not in lock"
                )
                rc = 1
            elif new is None:
                _eprint(
                    f"verify: tier{tier} {sample_id} present in lock but missing on disk"
                )
                rc = 1
            elif old != new:
                _eprint(
                    f"verify: tier{tier} {sample_id} sha256 mismatch "
                    f"(lock={old[:12]}.. disk={new[:12]}..)"
                )
                rc = 1

    if acceptance_yaml is not None:
        sync_rc = _check_acceptance_sync(
            acceptance_md=acceptance_md,
            acceptance_yaml=acceptance_yaml,
            is_ci=is_ci,
        )
        if sync_rc != 0:
            rc = sync_rc
    return rc


def _check_acceptance_sync(
    *,
    acceptance_md: Path,
    acceptance_yaml: Path,
    is_ci: bool,
) -> int:
    if not acceptance_yaml.exists():
        _eprint(
            f"verify: acceptance_thresholds.yaml not found at {acceptance_yaml}, "
            "skipping sync check (Phase 6 will create it)"
        )
        return 0
    if not acceptance_md.exists():
        _eprint(
            f"verify: acceptance.md not found at {acceptance_md}, skipping sync check"
        )
        return 0
    live_sha = _sha256_file(acceptance_md)
    yaml_payload = yaml.safe_load(acceptance_yaml.read_text(encoding="utf-8")) or {}
    recorded_sha = yaml_payload.get("synced_with_sha")
    if recorded_sha == live_sha:
        return 0
    _eprint(
        f"verify: synced_with_sha mismatch "
        f"(yaml={str(recorded_sha)[:12]}.. acceptance.md={live_sha[:12]}..); "
        "run lock.py --update-acceptance-sync"
    )
    return 1 if is_ci else 0


def cmd_update_acceptance_sync(
    acceptance_md: Path,
    acceptance_yaml: Path,
) -> int:
    if not acceptance_md.exists():
        _eprint(f"update-acceptance-sync: acceptance.md not found at {acceptance_md}")
        return 1
    if not acceptance_yaml.exists():
        _eprint(
            f"update-acceptance-sync: {acceptance_yaml} does not exist; "
            "Phase 6 must create it before this command can run"
        )
        return 1
    payload = yaml.safe_load(acceptance_yaml.read_text(encoding="utf-8")) or {}
    payload["synced_with_sha"] = _sha256_file(acceptance_md)
    payload["synced_at"] = datetime.now(timezone.utc).isoformat()
    acceptance_yaml.write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.eval.lock",
        description="Dataset and acceptance-threshold lock manager.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--update",
        action="store_true",
        help="Rebuild tier{1,2,3}.lock.json from datasets on disk.",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="Recompute hashes and exit non-zero on mismatch.",
    )
    mode.add_argument(
        "--update-acceptance-sync",
        action="store_true",
        help="Refresh acceptance_thresholds.yaml.synced_with_sha + synced_at only.",
    )
    parser.add_argument(
        "--datasets",
        default=str(DEFAULT_DATASETS_DIR),
        help=f"Datasets root (default: {DEFAULT_DATASETS_DIR}).",
    )
    parser.add_argument(
        "--manifests",
        default=str(DEFAULT_MANIFESTS_DIR),
        help=f"Manifests directory (default: {DEFAULT_MANIFESTS_DIR}).",
    )
    parser.add_argument(
        "--acceptance",
        default=str(DEFAULT_ACCEPTANCE_MD),
        help=f"acceptance.md path (default: {DEFAULT_ACCEPTANCE_MD}).",
    )
    parser.add_argument(
        "--acceptance-thresholds",
        default=str(DEFAULT_ACCEPTANCE_YAML),
        help=(f"acceptance_thresholds.yaml path (default: {DEFAULT_ACCEPTANCE_YAML})."),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    datasets_dir = Path(args.datasets).resolve()
    manifests_dir = Path(args.manifests).resolve()
    acceptance_md = Path(args.acceptance).resolve()
    acceptance_yaml = Path(args.acceptance_thresholds).resolve()
    if args.update:
        return cmd_update(datasets_dir, manifests_dir)
    if args.verify:
        return cmd_verify(
            datasets_dir,
            manifests_dir,
            acceptance_md,
            acceptance_yaml,
            is_ci=os.environ.get("CI") == "true",
        )
    if args.update_acceptance_sync:
        return cmd_update_acceptance_sync(acceptance_md, acceptance_yaml)
    return 2  # pragma: no cover - argparse enforces exclusivity


if __name__ == "__main__":  # pragma: no cover - direct CLI entry
    raise SystemExit(main())
