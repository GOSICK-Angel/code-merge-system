"""Tests for ``scripts.eval.lock`` — Verifier matrix T1-L1..T1-L8."""

from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from scripts.eval import lock as lock_mod
from scripts.eval.lock import (
    _sample_sha256,
    cmd_update_acceptance_sync,
    main,
)


FIXED_MTIME = 1767225600


def _make_tar(target: Path, files: dict[str, bytes]) -> None:
    """Build a deterministic tar at ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = FIXED_MTIME
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            tf.addfile(info, io.BytesIO(data))


def _write_sample(
    container: Path,
    sample_id: str,
    *,
    base_files: dict[str, bytes] | None = None,
    golden_files: dict[str, bytes] | None = None,
    upstream_patch: bytes = b"--- /dev/null\n+++ /dev/null\n",
    fork_patch: bytes = b"",
    meta: dict[str, object] | None = None,
) -> Path:
    sample_dir = container / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    _make_tar(sample_dir / "base.tar", base_files or {"x.py": b"x = 1\n"})
    _make_tar(sample_dir / "golden.tar", golden_files or {"x.py": b"x = 2\n"})
    (sample_dir / "upstream.patch").write_bytes(upstream_patch)
    (sample_dir / "fork.patch").write_bytes(fork_patch)
    (sample_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            meta
            or {
                "sample_id": sample_id,
                "tier": int(sample_id[1]),
                "category": "C",
                "expected_human": False,
            }
        ),
        encoding="utf-8",
    )
    return sample_dir


@pytest.fixture
def datasets_tree(tmp_path: Path) -> Path:
    """Build a minimal datasets tree with one sample per tier."""
    root = tmp_path / "datasets"
    _write_sample(root / "tier1" / "samples", "t1-0001")
    _write_sample(
        root / "tier3" / "adversarial",
        "t3-m3-0001",
        meta={
            "sample_id": "t3-m3-0001",
            "tier": 3,
            "category": "C",
            "loss_class": "M3",
            "expected_human": False,
        },
    )
    (root / "tier2" / "replays").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def manifests_dir(tmp_path: Path) -> Path:
    target = tmp_path / "manifests"
    target.mkdir()
    return target


def _run_lock(*args: str) -> int:
    return main(list(args))


# ---------------------------------------------------------------------------
# T1-L1 — `--update` writes three lock.json files
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_writes_three_lock_files(
        self, datasets_tree: Path, manifests_dir: Path
    ) -> None:
        rc = _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        assert rc == 0
        for tier in (1, 2, 3):
            path = manifests_dir / f"tier{tier}.lock.json"
            assert path.exists()
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["tier"] == tier
            assert "samples" in payload

    def test_update_records_sample_sha(
        self, datasets_tree: Path, manifests_dir: Path
    ) -> None:
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        tier1 = json.loads(
            (manifests_dir / "tier1.lock.json").read_text(encoding="utf-8")
        )
        sample = tier1["samples"][0]
        assert sample["sample_id"] == "t1-0001"
        assert sample["content_sha256"] == _sample_sha256(
            datasets_tree / "tier1" / "samples" / "t1-0001"
        )

    def test_tier2_manifest_has_empty_samples(
        self, datasets_tree: Path, manifests_dir: Path
    ) -> None:
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        tier2 = json.loads(
            (manifests_dir / "tier2.lock.json").read_text(encoding="utf-8")
        )
        assert tier2["samples"] == []


# ---------------------------------------------------------------------------
# T1-L2 / T1-L4 — verify happy + tampered paths
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_after_update_is_zero(
        self, datasets_tree: Path, manifests_dir: Path
    ) -> None:
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        assert rc == 0

    def test_verify_detects_tampered_sample(
        self,
        datasets_tree: Path,
        manifests_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        # Append one byte to base.tar
        (datasets_tree / "tier1" / "samples" / "t1-0001" / "base.tar").open("ab").write(
            b"x"
        )
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        assert rc != 0
        captured = capsys.readouterr()
        assert "t1-0001" in captured.err
        assert "sha256 mismatch" in captured.err

    def test_verify_detects_missing_manifest(
        self,
        datasets_tree: Path,
        manifests_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        assert rc != 0
        assert "missing manifest" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T1-L3 — sha256 stability
# ---------------------------------------------------------------------------


class TestShaStability:
    def test_two_updates_produce_identical_hashes(
        self, datasets_tree: Path, manifests_dir: Path
    ) -> None:
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        first = (manifests_dir / "tier1.lock.json").read_text(encoding="utf-8")
        first_payload = json.loads(first)
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        second_payload = json.loads(
            (manifests_dir / "tier1.lock.json").read_text(encoding="utf-8")
        )
        # generated_at differs, but sample sha256 must be identical.
        first_hashes = [s["content_sha256"] for s in first_payload["samples"]]
        second_hashes = [s["content_sha256"] for s in second_payload["samples"]]
        assert first_hashes == second_hashes

    def test_sample_sha_uses_canonical_artifacts_only(self, tmp_path: Path) -> None:
        sample = _write_sample(tmp_path / "tier1" / "samples", "t1-0001")
        baseline = _sample_sha256(sample)
        # Adding an extraneous file must NOT change the sha (artifact whitelist).
        (sample / "scratch.txt").write_text("noise", encoding="utf-8")
        assert _sample_sha256(sample) == baseline


# ---------------------------------------------------------------------------
# T1-L5 / T1-L6 — acceptance_thresholds.yaml sync check
# ---------------------------------------------------------------------------


def _write_acceptance(tmp_path: Path, body: str = "x: 1\n") -> Path:
    target = tmp_path / "acceptance.md"
    target.write_text(body, encoding="utf-8")
    return target


def _write_thresholds_yaml(tmp_path: Path, *, sha: str) -> Path:
    target = tmp_path / "acceptance_thresholds.yaml"
    payload = {
        "synced_with_sha": sha,
        "synced_at": "2026-05-15T00:00:00+00:00",
        "hard_gates": [
            {
                "id": "WMR",
                "threshold": 0.0,
                "operator": "==",
                "source": "Tier-1 + Tier-2 + Tier-3",
            }
        ],
        "soft_gates": [],
    }
    target.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return target


class TestAcceptanceSyncCheck:
    def test_missing_yaml_is_warning_not_failure(
        self,
        datasets_tree: Path,
        manifests_dir: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        acceptance_md = _write_acceptance(tmp_path)
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
            "--acceptance",
            str(acceptance_md),
            "--acceptance-thresholds",
            str(tmp_path / "missing.yaml"),
        )
        assert rc == 0
        assert "acceptance_thresholds.yaml not found" in capsys.readouterr().err

    def test_ci_mode_sha_mismatch_fails(
        self,
        datasets_tree: Path,
        manifests_dir: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CI", "true")
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        acceptance_md = _write_acceptance(tmp_path)
        thresholds_yaml = _write_thresholds_yaml(tmp_path, sha="0" * 64)
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
            "--acceptance",
            str(acceptance_md),
            "--acceptance-thresholds",
            str(thresholds_yaml),
        )
        assert rc != 0
        err = capsys.readouterr().err
        assert "synced_with_sha mismatch" in err
        assert "lock.py --update-acceptance-sync" in err

    def test_local_mode_sha_mismatch_only_warns(
        self,
        datasets_tree: Path,
        manifests_dir: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CI", raising=False)
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        acceptance_md = _write_acceptance(tmp_path)
        thresholds_yaml = _write_thresholds_yaml(tmp_path, sha="0" * 64)
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
            "--acceptance",
            str(acceptance_md),
            "--acceptance-thresholds",
            str(thresholds_yaml),
        )
        assert rc == 0
        assert "synced_with_sha mismatch" in capsys.readouterr().err

    def test_sha_match_is_silent(
        self,
        datasets_tree: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CI", "true")
        _run_lock(
            "--update",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
        )
        acceptance_md = _write_acceptance(tmp_path)
        live_sha = lock_mod._sha256_file(acceptance_md)
        thresholds_yaml = _write_thresholds_yaml(tmp_path, sha=live_sha)
        rc = _run_lock(
            "--verify",
            "--datasets",
            str(datasets_tree),
            "--manifests",
            str(manifests_dir),
            "--acceptance",
            str(acceptance_md),
            "--acceptance-thresholds",
            str(thresholds_yaml),
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# T1-L7 — `--update-acceptance-sync` only refreshes sha + timestamp
# ---------------------------------------------------------------------------


class TestUpdateAcceptanceSync:
    def test_only_updates_sha_and_timestamp(self, tmp_path: Path) -> None:
        acceptance_md = _write_acceptance(tmp_path, body="version v9\n")
        thresholds_yaml = _write_thresholds_yaml(tmp_path, sha="0" * 64)
        # Manually corrupt a threshold to ensure the sync command leaves it alone.
        original_payload = yaml.safe_load(thresholds_yaml.read_text(encoding="utf-8"))
        original_payload["hard_gates"][0]["threshold"] = 999.99
        thresholds_yaml.write_text(
            yaml.safe_dump(original_payload, sort_keys=True), encoding="utf-8"
        )

        rc = cmd_update_acceptance_sync(acceptance_md, thresholds_yaml)
        assert rc == 0

        refreshed = yaml.safe_load(thresholds_yaml.read_text(encoding="utf-8"))
        assert refreshed["synced_with_sha"] == lock_mod._sha256_file(acceptance_md)
        # synced_at parses as ISO 8601 with timezone.
        ts = datetime.fromisoformat(refreshed["synced_at"])
        assert ts.tzinfo is not None
        # The sentinel threshold value must be untouched.
        assert refreshed["hard_gates"][0]["threshold"] == 999.99

    def test_missing_yaml_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        acceptance_md = _write_acceptance(tmp_path)
        rc = cmd_update_acceptance_sync(acceptance_md, tmp_path / "missing.yaml")
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err

    def test_missing_acceptance_md_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        thresholds_yaml = _write_thresholds_yaml(tmp_path, sha="0" * 64)
        rc = cmd_update_acceptance_sync(tmp_path / "missing.md", thresholds_yaml)
        assert rc == 1
        assert "acceptance.md not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T1-L8 — argparse mutual exclusion
# ---------------------------------------------------------------------------


class TestArgparseMutualExclusion:
    def test_update_and_update_acceptance_sync_mutually_exclusive(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--update", "--update-acceptance-sync"])
        assert exc.value.code != 0
        assert "not allowed with" in capsys.readouterr().err

    def test_verify_and_update_mutually_exclusive(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--verify", "--update"])
        assert exc.value.code != 0

    def test_no_mode_required(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# Sample shape sanity — committed reference samples must produce stable shas
# ---------------------------------------------------------------------------


class TestCommittedSamples:
    def test_real_repo_samples_have_stable_sha(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        t1 = repo_root / "tests/eval/datasets/tier1/samples/t1-0001"
        t3 = repo_root / "tests/eval/datasets/tier3/adversarial/t3-m3-0001"
        assert t1.is_dir()
        assert t3.is_dir()
        # Two consecutive computations on the on-disk samples are identical.
        assert _sample_sha256(t1) == _sample_sha256(t1)
        assert _sample_sha256(t3) == _sample_sha256(t3)

    def test_real_repo_update_then_verify(self, tmp_path: Path) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir()
        rc_update = main(
            [
                "--update",
                "--datasets",
                str(repo_root / "tests" / "eval" / "datasets"),
                "--manifests",
                str(manifests_dir),
            ]
        )
        assert rc_update == 0
        rc_verify = main(
            [
                "--verify",
                "--datasets",
                str(repo_root / "tests" / "eval" / "datasets"),
                "--manifests",
                str(manifests_dir),
                "--acceptance",
                str(repo_root / "doc" / "evaluation" / "acceptance.md"),
                "--acceptance-thresholds",
                str(tmp_path / "missing.yaml"),
            ]
        )
        assert rc_verify == 0
