"""Ground-truth loader for evaluation samples.

Wraps the on-disk layout of one sample directory
(``{base,golden}.tar`` + ``{upstream,fork}.patch`` + ``meta.yaml``) into
a strongly-typed :class:`scripts.eval._schemas.GroundTruthBundle`.

Used by:
    - ``prepare.py`` — to validate a sample before expansion.
    - ``diff_against_golden.py`` (Phase 4) — to read the golden tree for
      per-file comparisons.

Exception hierarchy lets callers distinguish "your input is missing" from
"your input is malformed" without parsing error messages.
"""

from __future__ import annotations

import tarfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from scripts.eval._schemas import GoldenFileEntry, GroundTruthBundle, SampleMeta


class GroundTruthError(Exception):
    """Base class for ground-truth load failures.

    Carries the offending ``sample_id`` so callers can build aggregated
    reports without parsing the message.
    """

    def __init__(self, sample_id: str, message: str) -> None:
        self.sample_id = sample_id
        super().__init__(f"[{sample_id}] {message}")


class GroundTruthMissing(GroundTruthError):
    """Raised when a required artifact is absent from the sample directory."""

    def __init__(self, sample_id: str, missing: str) -> None:
        self.missing = missing
        super().__init__(sample_id, f"missing required artifact: {missing}")


class GroundTruthCorrupted(GroundTruthError):
    """Raised when an artifact exists but cannot be parsed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_meta_yaml(meta_path: Path) -> dict[str, Any]:
    try:
        text = meta_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GroundTruthCorrupted(
            meta_path.parent.name, f"could not read meta.yaml: {exc}"
        ) from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GroundTruthCorrupted(
            meta_path.parent.name, f"meta.yaml is not valid YAML: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise GroundTruthCorrupted(
            meta_path.parent.name, "meta.yaml must contain a top-level mapping"
        )
    return loaded


def load_meta(sample_dir: Path) -> SampleMeta:
    """Parse and validate ``<sample_dir>/meta.yaml`` into a :class:`SampleMeta`.

    Raises :class:`GroundTruthMissing` when ``meta.yaml`` is absent and
    :class:`GroundTruthCorrupted` for any parse / schema violation.
    """
    meta_path = sample_dir / "meta.yaml"
    if not meta_path.is_file():
        raise GroundTruthMissing(sample_dir.name, "meta.yaml")
    payload = _read_meta_yaml(meta_path)
    try:
        return SampleMeta.model_validate(payload)
    except ValidationError as exc:
        raise GroundTruthCorrupted(
            sample_dir.name, f"meta.yaml does not match schema: {exc}"
        ) from exc


def load_golden_tree(sample_dir: Path) -> dict[str, bytes]:
    """Extract the golden tarball into a ``{relative_path: bytes}`` mapping.

    Raises:
        GroundTruthMissing: ``golden.tar`` does not exist.
        GroundTruthCorrupted: ``golden.tar`` is not a valid tar archive
            or contains a path that escapes its prefix (defense-in-depth
            against malicious fixtures).
    """
    golden_tar = sample_dir / "golden.tar"
    if not golden_tar.is_file():
        raise GroundTruthMissing(sample_dir.name, "golden.tar")
    try:
        with tarfile.open(golden_tar, "r") as tf:
            return _materialise_tar_members(sample_dir.name, tf)
    except tarfile.TarError as exc:
        raise GroundTruthCorrupted(
            sample_dir.name, f"golden.tar is not a valid tar archive: {exc}"
        ) from exc


def _materialise_tar_members(sample_id: str, tf: tarfile.TarFile) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for member in tf.getmembers():
        if not member.isfile():
            continue
        # Reject absolute or parent-traversal paths; tarfile.extractall in
        # python >= 3.12 does this by default, but we use extractfile.
        if member.name.startswith("/") or ".." in Path(member.name).parts:
            raise GroundTruthCorrupted(
                sample_id, f"golden.tar contains unsafe path: {member.name}"
            )
        fh = tf.extractfile(member)
        if fh is None:
            continue
        out[member.name] = fh.read()
    return out


def load_sample(sample_dir: Path) -> GroundTruthBundle:
    """One-shot loader returning meta + golden contents as a frozen bundle.

    Convenience wrapper around :func:`load_meta` + :func:`load_golden_tree`.
    Both underlying calls may raise :class:`GroundTruthError`.
    """
    meta = load_meta(sample_dir)
    golden = load_golden_tree(sample_dir)
    files = tuple(
        GoldenFileEntry(relative_path=path, content=data)
        for path, data in sorted(golden.items())
    )
    return GroundTruthBundle(meta=meta, golden_files=files)


__all__ = [
    "GroundTruthCorrupted",
    "GroundTruthError",
    "GroundTruthMissing",
    "load_golden_tree",
    "load_meta",
    "load_sample",
]
