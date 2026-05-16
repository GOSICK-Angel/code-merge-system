"""Tests for ``scripts.eval._ground_truth`` — Verifier T2-G1..T2-G3."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts.eval._ground_truth import (
    GroundTruthCorrupted,
    GroundTruthMissing,
    load_golden_tree,
    load_meta,
    load_sample,
)
from scripts.eval._schemas import GroundTruthBundle, SampleMeta


FIXED_MTIME = 1767225600


def _write_tar(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = FIXED_MTIME
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))


def _write_meta(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _build_sample(
    container: Path,
    sample_id: str,
    *,
    meta: dict[str, object] | None = None,
    golden: dict[str, bytes] | None = None,
    include_meta: bool = True,
    include_golden: bool = True,
) -> Path:
    sample_dir = container / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    if include_meta:
        _write_meta(
            sample_dir / "meta.yaml",
            meta
            or {
                "sample_id": sample_id,
                "tier": int(sample_id[1]),
                "category": "C",
                "loss_class": "M3",
                "expected_human": False,
            },
        )
    if include_golden:
        _write_tar(
            sample_dir / "golden.tar",
            golden or {"a.py": b"a = 1\n", "sub/b.py": b"b = 2\n"},
        )
    return sample_dir


# ---------------------------------------------------------------------------
# T2-G1
# ---------------------------------------------------------------------------


class TestLoadMeta:
    def test_parses_valid_meta_yaml(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(
            tmp_path,
            "t3-m3-0001",
            meta={
                "sample_id": "t3-m3-0001",
                "tier": 3,
                "category": "C",
                "loss_class": "M3",
                "expected_human": False,
            },
        )
        meta = load_meta(sample_dir)
        assert isinstance(meta, SampleMeta)
        assert meta.sample_id == "t3-m3-0001"
        assert meta.category == "C"
        assert meta.loss_class == "M3"
        assert meta.expected_human is False

    def test_missing_meta_raises_missing(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(tmp_path, "t1-0001", include_meta=False)
        with pytest.raises(GroundTruthMissing) as exc:
            load_meta(sample_dir)
        assert "meta.yaml" in str(exc.value)
        assert exc.value.sample_id == "t1-0001"

    def test_malformed_yaml_raises_corrupted(self, tmp_path: Path) -> None:
        sample_dir = tmp_path / "t1-0001"
        sample_dir.mkdir()
        (sample_dir / "meta.yaml").write_text(": : :", encoding="utf-8")
        with pytest.raises(GroundTruthCorrupted):
            load_meta(sample_dir)

    def test_meta_yaml_must_be_mapping(self, tmp_path: Path) -> None:
        sample_dir = tmp_path / "t1-0001"
        sample_dir.mkdir()
        (sample_dir / "meta.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
        with pytest.raises(GroundTruthCorrupted):
            load_meta(sample_dir)

    def test_schema_violation_raises_corrupted(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(
            tmp_path,
            "t1-0001",
            meta={"sample_id": "t1-0001", "tier": 99, "category": "C"},
        )
        with pytest.raises(GroundTruthCorrupted):
            load_meta(sample_dir)


# ---------------------------------------------------------------------------
# T2-G2
# ---------------------------------------------------------------------------


class TestLoadGoldenTree:
    def test_returns_relative_path_to_bytes_map(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(
            tmp_path,
            "t1-0001",
            golden={"a.py": b"x = 1\n", "sub/b.py": b"y = 2\n"},
        )
        tree = load_golden_tree(sample_dir)
        assert tree == {"a.py": b"x = 1\n", "sub/b.py": b"y = 2\n"}

    def test_missing_golden_raises(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(tmp_path, "t1-0001", include_golden=False)
        with pytest.raises(GroundTruthMissing) as exc:
            load_golden_tree(sample_dir)
        assert exc.value.missing == "golden.tar"

    def test_corrupted_tar_raises(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(tmp_path, "t1-0001", include_golden=False)
        (sample_dir / "golden.tar").write_bytes(b"this is not a tar")
        with pytest.raises(GroundTruthCorrupted):
            load_golden_tree(sample_dir)

    def test_unsafe_path_in_tar_raises(self, tmp_path: Path) -> None:
        sample_dir = tmp_path / "t1-0001"
        sample_dir.mkdir()
        # Forge a tar with a parent-traversal entry.
        with tarfile.open(
            sample_dir / "golden.tar", "w", format=tarfile.USTAR_FORMAT
        ) as tf:
            data = b"evil\n"
            info = tarfile.TarInfo(name="../escape.py")
            info.size = len(data)
            info.mtime = FIXED_MTIME
            tf.addfile(info, io.BytesIO(data))
        _write_meta(
            sample_dir / "meta.yaml",
            {"sample_id": "t1-0001", "tier": 1, "category": "C"},
        )
        with pytest.raises(GroundTruthCorrupted):
            load_golden_tree(sample_dir)


# ---------------------------------------------------------------------------
# T2-G3 — load_sample combined
# ---------------------------------------------------------------------------


class TestLoadSample:
    def test_returns_bundle_with_meta_and_files(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(
            tmp_path,
            "t3-m3-0001",
            golden={"lib.py": b"def add(a,b): pass\n"},
        )
        bundle = load_sample(sample_dir)
        assert isinstance(bundle, GroundTruthBundle)
        assert bundle.meta.sample_id == "t3-m3-0001"
        assert len(bundle.golden_files) == 1
        assert bundle.golden_files[0].relative_path == "lib.py"
        assert bundle.golden_files[0].content == b"def add(a,b): pass\n"

    def test_missing_meta_propagates(self, tmp_path: Path) -> None:
        sample_dir = _build_sample(tmp_path, "t1-0001", include_meta=False)
        with pytest.raises(GroundTruthMissing):
            load_sample(sample_dir)


# ---------------------------------------------------------------------------
# Real committed samples (sanity)
# ---------------------------------------------------------------------------


class TestCommittedSamples:
    def test_reference_tier1_fixture_loads(self) -> None:
        # The synthetic tier1 reference sample lives under fixtures/ to keep
        # it physically separate from the real evaluation dataset (dataset.md
        # §1 "评估集不进入训练" — same isolation principle for fixtures vs
        # real data so framework tests don't depend on real sample shape).
        repo_root = Path(__file__).resolve().parents[3]
        bundle = load_sample(
            repo_root / "tests/eval/fixtures/reference_samples/tier1/samples/t1-0001"
        )
        assert bundle.meta.sample_id == "t1-0001"
        assert any(f.relative_path == "hello.py" for f in bundle.golden_files)

    def test_real_tier3_loads_with_loss_class(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        bundle = load_sample(
            repo_root / "tests/eval/datasets/tier3/adversarial/t3-m3-0001"
        )
        assert bundle.meta.loss_class == "M3"
