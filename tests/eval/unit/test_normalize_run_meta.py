"""Phase D unit tests for `scripts/eval/normalize_run_meta`.

Covers the legacy wrapper -> canonical RunMeta normalization path that
unblocks summarize.py / gate.py on wrapper-produced runs (Phase B P1-A
follow-up).
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.eval import normalize_run_meta as nrm
from scripts.eval._schemas import RunMeta


def _legacy_payload(sample_id: str = "t1-0001") -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "run_id": "1b66fea5-20f4-4eb0-a48e-18d0e4a47794",
        "wall_seconds": 11,
        "merge_target": "upstream",
        "fork_ref": "main",
    }


def test_normalize_one_renames_wall_seconds() -> None:
    out = nrm.normalize_one(
        _legacy_payload(), git_sha="abcdef0", model="claude-opus-4-6"
    )
    assert "wall_seconds" not in out
    assert out["wall_time_seconds"] == 11.0


def test_normalize_one_drops_wrapper_extras() -> None:
    out = nrm.normalize_one(
        _legacy_payload(), git_sha="abcdef0", model="claude-opus-4-6"
    )
    assert "merge_target" not in out
    assert "fork_ref" not in out


def test_normalize_one_fills_required_defaults() -> None:
    out = nrm.normalize_one(
        _legacy_payload(), git_sha="abcdef0", model="claude-opus-4-6"
    )
    assert out["seed"] == 0
    assert out["concurrency"] == 1
    assert out["cost_usd"] == 0.0
    assert out["git_sha"] == "abcdef0"
    assert out["model_matrix"] == {"all": "claude-opus-4-6"}
    assert out["status"] == "success"
    assert out["exit_code"] == 0


def test_normalize_one_output_validates_against_RunMeta() -> None:
    out = nrm.normalize_one(
        _legacy_payload(), git_sha="abcdef0", model="claude-opus-4-6"
    )
    rm = RunMeta.model_validate(out)
    assert rm.sample_id == "t1-0001"
    assert rm.wall_time_seconds == 11.0


def test_normalize_one_preserves_existing_canonical_fields() -> None:
    payload: dict[str, object] = {
        "sample_id": "t1-0002",
        "run_id": "r0",
        "seed": 7,
        "concurrency": 2,
        "wall_time_seconds": 42.0,
        "cost_usd": 0.5,
        "git_sha": "deadbee",
        "model_matrix": {"all": "claude-sonnet-4-6"},
    }
    out = nrm.normalize_one(payload, git_sha="abcdef0", model="claude-opus-4-6")
    rm = RunMeta.model_validate(out)
    assert rm.seed == 7
    assert rm.concurrency == 2
    assert rm.cost_usd == 0.5
    assert rm.git_sha == "deadbee"
    assert rm.model_matrix["all"] == "claude-sonnet-4-6"


def test_normalize_runs_dir_rewrites_legacy_and_skips_conforming(
    tmp_path: Path,
) -> None:
    # Sample A: legacy payload — should be rewritten
    sa = tmp_path / "t1-0001"
    sa.mkdir()
    (sa / "run_meta.json").write_text(json.dumps(_legacy_payload("t1-0001")))

    # Sample B: already canonical — should be skipped
    canonical = {
        "sample_id": "t1-0002",
        "run_id": "r0",
        "seed": 0,
        "concurrency": 1,
        "wall_time_seconds": 5.0,
        "cost_usd": 0.0,
        "git_sha": "abcdef0",
    }
    sb = tmp_path / "t1-0002"
    sb.mkdir()
    (sb / "run_meta.json").write_text(json.dumps(canonical))

    rewritten, skipped = nrm.normalize_runs_dir(
        tmp_path, git_sha="abcdef0", model="claude-opus-4-6"
    )
    assert rewritten == 1
    assert skipped == 1

    # Sample A is now schema-valid
    reloaded = json.loads((sa / "run_meta.json").read_text())
    rm = RunMeta.model_validate(reloaded)
    assert rm.sample_id == "t1-0001"


def test_normalize_runs_dir_missing_dir_returns_zeros(tmp_path: Path) -> None:
    rewritten, skipped = nrm.normalize_runs_dir(
        tmp_path / "does-not-exist", git_sha="abc", model="claude-opus-4-6"
    )
    assert rewritten == 0
    assert skipped == 0
