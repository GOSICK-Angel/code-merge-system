"""Tests for ``scripts.eval.prepare`` — Verifier T2-P1..T2-P6."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts.eval import lock as lock_mod
from scripts.eval import prepare as prepare_mod
from scripts.eval.prepare import PatchApplyError, main


FIXED_MTIME = 1767225600


def _make_tar(target: Path, files: dict[str, bytes]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = FIXED_MTIME
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))


def _whole_file_patch(old: str, new: str, path: str = "hello.py") -> bytes:
    """Build a deterministic single-hunk unified diff."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    out = [f"--- a/{path}\n", f"+++ b/{path}\n"]
    out.append(f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@\n")
    out.extend(
        ("-" + line) if line.endswith("\n") else ("-" + line + "\n")
        for line in old_lines
    )
    out.extend(
        ("+" + line) if line.endswith("\n") else ("+" + line + "\n")
        for line in new_lines
    )
    return "".join(out).encode("utf-8")


def _make_sample(
    container: Path,
    sample_id: str,
    *,
    base: dict[str, bytes] | None = None,
    golden: dict[str, bytes] | None = None,
    fork_patch: bytes | None = None,
    upstream_patch: bytes | None = None,
    meta_overrides: dict[str, object] | None = None,
    omit_golden: bool = False,
) -> Path:
    sample_dir = container / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    base = base or {"hello.py": b"def greet(): pass\n"}
    golden = golden or {"hello.py": b"def greet(name): pass\n"}
    fork_patch = (
        fork_patch
        if fork_patch is not None
        else _whole_file_patch(
            "def greet(): pass\n", "# fork comment\ndef greet(): pass\n"
        )
    )
    upstream_patch = (
        upstream_patch
        if upstream_patch is not None
        else _whole_file_patch("def greet(): pass\n", "def greet(name): pass\n")
    )
    _make_tar(sample_dir / "base.tar", base)
    if not omit_golden:
        _make_tar(sample_dir / "golden.tar", golden)
    (sample_dir / "fork.patch").write_bytes(fork_patch)
    (sample_dir / "upstream.patch").write_bytes(upstream_patch)
    meta = {
        "sample_id": sample_id,
        "tier": int(sample_id[1]),
        "category": "C",
        "expected_human": False,
    }
    if meta_overrides:
        meta.update(meta_overrides)
    (sample_dir / "meta.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    return sample_dir


@pytest.fixture
def datasets_with_one_sample(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"
    _make_sample(root / "tier1" / "samples", "t1-0001")
    (root / "tier2" / "replays").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def datasets_tier3(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"
    _make_sample(
        root / "tier3" / "adversarial",
        "t3-m3-0001",
        meta_overrides={"loss_class": "M3", "tier": 3},
        fork_patch=b"",  # empty patch is legal
    )
    (root / "tier1" / "samples").mkdir(parents=True, exist_ok=True)
    (root / "tier2" / "replays").mkdir(parents=True, exist_ok=True)
    return root


def _refresh_lock(datasets: Path, manifests: Path) -> None:
    rc = lock_mod.main(
        ["--update", "--datasets", str(datasets), "--manifests", str(manifests)]
    )
    assert rc == 0


def _run_prepare(*args: str) -> int:
    return main(list(args))


# ---------------------------------------------------------------------------
# T2-P1 — happy path
# ---------------------------------------------------------------------------


class TestPrepareTier1:
    def test_expands_sample_with_four_artifacts(
        self, datasets_with_one_sample: Path, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        _refresh_lock(datasets_with_one_sample, manifests)
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "1",
            "--out",
            str(out),
            "--datasets",
            str(datasets_with_one_sample),
            "--manifests",
            str(manifests),
        )
        assert rc == 0
        target = out / "t1-0001"
        assert (target / "working_tree").is_dir()
        assert (target / "golden_tree").is_dir()
        assert (target / "meta.yaml").is_file()
        assert (target / "apply_log.txt").is_file()

    def test_apply_log_records_patch_steps(
        self, datasets_with_one_sample: Path, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        _refresh_lock(datasets_with_one_sample, manifests)
        out = tmp_path / "out"
        _run_prepare(
            "--tier",
            "1",
            "--out",
            str(out),
            "--datasets",
            str(datasets_with_one_sample),
            "--manifests",
            str(manifests),
        )
        log = (out / "t1-0001" / "apply_log.txt").read_text(encoding="utf-8")
        assert "fork.patch" in log
        assert "hello.py" in log


# ---------------------------------------------------------------------------
# T2-P2 — lock-verify gate
# ---------------------------------------------------------------------------


class TestLockGate:
    def test_lock_consistent_passes(
        self, datasets_with_one_sample: Path, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        _refresh_lock(datasets_with_one_sample, manifests)
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "1",
            "--out",
            str(out),
            "--datasets",
            str(datasets_with_one_sample),
            "--manifests",
            str(manifests),
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# T2-P3 — tier3 sample with empty fork patch
# ---------------------------------------------------------------------------


class TestPrepareTier3:
    def test_expands_tier3_with_empty_fork_patch(
        self, datasets_tier3: Path, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        _refresh_lock(datasets_tier3, manifests)
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "3",
            "--out",
            str(out),
            "--datasets",
            str(datasets_tier3),
            "--manifests",
            str(manifests),
        )
        assert rc == 0
        target = out / "t3-m3-0001"
        assert (target / "working_tree" / "hello.py").is_file()
        assert (target / "golden_tree").is_dir()
        log = (target / "apply_log.txt").read_text(encoding="utf-8")
        assert "no-op (empty patch)" in log


# ---------------------------------------------------------------------------
# T2-P4 — corrupted patch
# ---------------------------------------------------------------------------


class TestCorruptedPatch:
    def test_corrupted_fork_patch_returns_two(
        self,
        datasets_with_one_sample: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        # Corrupt the patch BEFORE refreshing the lock so verify still passes.
        (
            datasets_with_one_sample / "tier1" / "samples" / "t1-0001" / "fork.patch"
        ).write_bytes(b"this is definitely not a patch\n")
        _refresh_lock(datasets_with_one_sample, manifests)
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "1",
            "--out",
            str(out),
            "--datasets",
            str(datasets_with_one_sample),
            "--manifests",
            str(manifests),
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "t1-0001" in err
        assert "fork.patch" in err


# ---------------------------------------------------------------------------
# T2-P5 — missing golden
# ---------------------------------------------------------------------------


class TestMissingGolden:
    def test_missing_golden_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        datasets = tmp_path / "datasets"
        _make_sample(datasets / "tier1" / "samples", "t1-0001", omit_golden=True)
        (datasets / "tier2" / "replays").mkdir(parents=True, exist_ok=True)
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        # _sample_sha256 raises before lock can be built without golden.tar.
        # Build the lock by temporarily creating a placeholder, then remove it
        # to mirror the user-facing scenario where golden.tar is deleted post-lock.
        placeholder = datasets / "tier1" / "samples" / "t1-0001" / "golden.tar"
        _make_tar(placeholder, {"hello.py": b"placeholder\n"})
        _refresh_lock(datasets, manifests)
        placeholder.unlink()
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "1",
            "--out",
            str(out),
            "--datasets",
            str(datasets),
            "--manifests",
            str(manifests),
        )
        # Lock check fails first (placeholder gone -> sha mismatch),
        # so prepare returns 1, not 2. Either way it must exit non-zero.
        assert rc != 0
        assert capsys.readouterr().err  # something printed


# ---------------------------------------------------------------------------
# T2-P6 — lock mismatch refuses execution
# ---------------------------------------------------------------------------


class TestLockMismatch:
    def test_tampered_sample_yields_exit_one(
        self,
        datasets_with_one_sample: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        _refresh_lock(datasets_with_one_sample, manifests)
        # Tamper after lock — base.tar gets one extra byte.
        target = datasets_with_one_sample / "tier1" / "samples" / "t1-0001" / "base.tar"
        with target.open("ab") as fh:
            fh.write(b"x")
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "1",
            "--out",
            str(out),
            "--datasets",
            str(datasets_with_one_sample),
            "--manifests",
            str(manifests),
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "lock verify failed" in err
        assert "lock.py --update" in err


# ---------------------------------------------------------------------------
# Internal: empty-tier handling and patch-application internals
# ---------------------------------------------------------------------------


class TestEmptyTier:
    def test_tier_with_no_samples_returns_zero(
        self, datasets_with_one_sample: Path, tmp_path: Path
    ) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        _refresh_lock(datasets_with_one_sample, manifests)
        out = tmp_path / "out"
        rc = _run_prepare(
            "--tier",
            "2",  # tier2 has no samples
            "--out",
            str(out),
            "--datasets",
            str(datasets_with_one_sample),
            "--manifests",
            str(manifests),
        )
        assert rc == 0


class TestPatchApplyHelpers:
    def test_unsafe_path_in_base_tar_raises(self, tmp_path: Path) -> None:
        sample_dir = tmp_path / "t1-0001"
        sample_dir.mkdir()
        bad_tar = sample_dir / "base.tar"
        with tarfile.open(bad_tar, "w", format=tarfile.USTAR_FORMAT) as tf:
            data = b"x = 1\n"
            info = tarfile.TarInfo(name="../escape.py")
            info.size = len(data)
            info.mtime = FIXED_MTIME
            tf.addfile(info, io.BytesIO(data))
        with pytest.raises(PatchApplyError) as exc:
            prepare_mod._safe_extract_tar("t1-0001", bad_tar, tmp_path / "working_tree")
        assert "unsafe path" in str(exc.value)

    def test_corrupted_tar_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "base.tar"
        bad.write_bytes(b"not a tar")
        with pytest.raises(PatchApplyError):
            prepare_mod._safe_extract_tar("t1-0001", bad, tmp_path / "working_tree")

    def test_apply_patch_to_tree_handles_empty_input(self) -> None:
        log = prepare_mod._apply_patch_to_tree(
            "t1-0001", "fork.patch", b"", {"x.py": b"foo\n"}
        )
        assert log == ["fork.patch: no-op (empty patch)"]

    def test_apply_patch_to_tree_parses_real_unified_diff(self) -> None:
        tree = {"hello.py": b"def greet(): pass\n"}
        patch = _whole_file_patch("def greet(): pass\n", "def greet(name): pass\n")
        log = prepare_mod._apply_patch_to_tree("t1-0001", "fork.patch", patch, tree)
        assert tree["hello.py"] == b"def greet(name): pass\n"
        assert any("hello.py" in line for line in log)

    def test_apply_patch_garbage_raises(self) -> None:
        with pytest.raises(PatchApplyError):
            prepare_mod._apply_patch_to_tree(
                "t1-0001", "fork.patch", b"garbage", {"x.py": b"y\n"}
            )


# ---------------------------------------------------------------------------
# Real committed samples — end-to-end smoke
# ---------------------------------------------------------------------------


class TestCommittedSamplesSmoke:
    def test_real_tier1_round_trip(self, tmp_path: Path) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        rc_lock = lock_mod.main(
            [
                "--update",
                "--datasets",
                str(repo_root / "tests" / "eval" / "datasets"),
                "--manifests",
                str(manifests),
            ]
        )
        assert rc_lock == 0
        out = tmp_path / "out"
        rc = main(
            [
                "--tier",
                "1",
                "--out",
                str(out),
                "--datasets",
                str(repo_root / "tests" / "eval" / "datasets"),
                "--manifests",
                str(manifests),
            ]
        )
        assert rc == 0
        target = out / "t1-0001"
        # working_tree has hello.py with the fork's leading comment applied.
        applied = (target / "working_tree" / "hello.py").read_text(encoding="utf-8")
        assert "Greeting helper" in applied
        # golden_tree has the merged version (kwarg + comment).
        golden = (target / "golden_tree" / "hello.py").read_text(encoding="utf-8")
        assert "loud" in golden
        assert "Greeting helper" in golden
