"""Phase B (P-γ-3) TB-U-05 / TB-U-08 — R2 sha256 lock helper.

Maps to test/FINAL.md §2.2:
- TB-U-05: sha helper computes per-sample sha256 and the lock entries
           round-trip equal to on-disk computation.
- TB-U-08: tampering a sample byte produces a mismatch in verify_lock.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.eval.manifests import _r2_sha


def test_TB_U_05_lock_round_trip_matches_disk(tmp_path) -> None:
    """Build lock entries, write them, then re-verify — must report match."""
    # use real on-disk samples; manifest may live in tmp_path so we don't
    # disturb the committed lock.
    repo_root = Path(__file__).resolve().parents[3]
    datasets = repo_root / "tests" / "eval" / "datasets" / "r2" / "samples"
    lock_file = tmp_path / "r2.lock.json"
    rc = _r2_sha.update_lock(datasets_dir=datasets, lock_path=lock_file)
    assert rc == 0
    payload = json.loads(lock_file.read_text())
    assert payload["eval_version"]
    assert len(payload["samples"]) >= 5
    rc_verify = _r2_sha.verify_lock(datasets_dir=datasets, lock_path=lock_file)
    assert rc_verify == 0


def test_TB_U_08_tampered_sample_reports_mismatch(tmp_path, capsys) -> None:
    """Copy r2-0001 to tmp, tamper meta.yaml, lock then alter — verify fails."""
    repo_root = Path(__file__).resolve().parents[3]
    src = repo_root / "tests" / "eval" / "datasets" / "r2" / "samples"
    dst = tmp_path / "samples"
    dst.mkdir()
    # only copy one sample so the test is fast
    sample_src = src / "r2-0001"
    import shutil

    shutil.copytree(sample_src, dst / "r2-0001")
    lock_file = tmp_path / "r2.lock.json"
    assert _r2_sha.update_lock(datasets_dir=dst, lock_path=lock_file) == 0
    # tamper
    target = dst / "r2-0001" / "meta.yaml"
    target.write_text(target.read_text() + "\n# tampered\n")
    rc = _r2_sha.verify_lock(datasets_dir=dst, lock_path=lock_file)
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    assert "r2-0001" in captured.err


def test_TB_U_05_committed_lock_matches_disk() -> None:
    """The committed r2.lock.json must match the committed sample bytes."""
    repo_root = Path(__file__).resolve().parents[3]
    datasets = repo_root / "tests" / "eval" / "datasets" / "r2" / "samples"
    lock = repo_root / "tests" / "eval" / "manifests" / "r2.lock.json"
    assert lock.exists(), "r2.lock.json must be committed alongside samples"
    rc = _r2_sha.verify_lock(datasets_dir=datasets, lock_path=lock)
    assert rc == 0
