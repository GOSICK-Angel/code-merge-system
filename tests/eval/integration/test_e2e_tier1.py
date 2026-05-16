"""End-to-end integration tests for the eval-impl pipeline.

Covers Verifier T8-E1 / T8-E2 / T8-E3 — chain ``prepare → run → diff →
summarize → gate`` through the fake ``merge`` binary fixture, plus the
3-run DET consistency loop.

The chain operates entirely on fixture data (no real LLM calls) so
these tests are safe to run in CI but live under ``tests/eval/integration/``
to flag them as crossing the unit-boundary (multiple scripts cooperate).
"""

from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path

import pytest

from scripts.eval import (
    consistency as consistency_mod,
    diff_against_golden as diff_mod,
    gate as gate_mod,
    run as run_mod,
    summarize as summarize_mod,
)
from scripts.eval.lock import DEFAULT_DATASETS_DIR, DEFAULT_MANIFESTS_DIR

REPO_ROOT = Path(__file__).resolve().parents[3]
FAKE_MERGE_BIN = REPO_ROOT / "tests/eval/fixtures/fake_merge_bin/fake_merge.sh"
DUMMY_RUN_FIXTURE = REPO_ROOT / "tests/eval/fixtures/dummy_run"
ACCEPTANCE_YAML = REPO_ROOT / "tests/eval/manifests/acceptance_thresholds.yaml"
TIER1_DATASETS = DEFAULT_DATASETS_DIR / "tier1" / "samples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_golden_tree(sample_dir: Path, out_dir: Path) -> Path:
    """Materialise ``golden.tar`` into a directory so the fake merge CLI can
    overlay it onto the eval cwd via ``FAKE_MERGED_TREE_DIR``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(sample_dir / "golden.tar", "r") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            data = tf.extractfile(member)
            if data is None:
                continue
            target = out_dir / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data.read())
    return out_dir


def _run_merge_pipeline(
    *,
    workdir: Path,
    seed: int,
    monkeypatch: pytest.MonkeyPatch,
    merged_tree_dir: Path | None = None,
) -> int:
    """Drive a single ``scripts.eval.run`` invocation through the fake CLI.

    ``FAKE_FIXTURE_DIR`` / ``FAKE_SAMPLE_ID`` are inherited by the merge
    subprocess via :func:`eval_subprocess_env` (which strips only
    ``MERGE_DEV`` and LLM keys — see [code-phase-0]).
    """
    monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
    monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
    if merged_tree_dir is not None:
        monkeypatch.setenv("FAKE_MERGED_TREE_DIR", str(merged_tree_dir))
    else:
        monkeypatch.delenv("FAKE_MERGED_TREE_DIR", raising=False)
    return run_mod.main(
        [
            "--tier",
            "1",
            "--workdir",
            str(workdir),
            "--merge-bin",
            str(FAKE_MERGE_BIN),
            "--seed",
            str(seed),
            "--datasets",
            str(DEFAULT_DATASETS_DIR),
            "--manifests",
            str(DEFAULT_MANIFESTS_DIR),
        ]
    )


def _run_diff(*, runs_dir: Path, output: Path) -> int:
    return diff_mod.main(
        [
            "--runs",
            str(runs_dir),
            "--datasets",
            str(TIER1_DATASETS),
            "--output",
            str(output),
            "--tier",
            "1",
        ]
    )


def _run_summarize(*, diff_path: Path, runs_dir: Path, output: Path) -> int:
    return summarize_mod.main(
        [
            "--diff",
            str(diff_path),
            "--runs",
            str(runs_dir),
            "--output",
            str(output),
        ]
    )


def _run_gate(*, report: Path, acceptance: Path, output: Path) -> int:
    return gate_mod.main(
        [
            "--report",
            str(report),
            "--acceptance",
            str(acceptance),
            "--output",
            str(output),
        ]
    )


def _read_json(path: Path) -> dict[str, object]:
    parsed: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


# ---------------------------------------------------------------------------
# T8-E1 — full chain to verdict=PASS
# ---------------------------------------------------------------------------


class TestE2eFullChain:
    def test_chain_lands_pass_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        merged_tree = _extract_golden_tree(
            TIER1_DATASETS / "t1-0001", tmp_path / "merged_tree"
        )
        workdir = tmp_path / "run-1"
        rc_run = _run_merge_pipeline(
            workdir=workdir,
            seed=1,
            monkeypatch=monkeypatch,
            merged_tree_dir=merged_tree,
        )
        assert rc_run == 0, "step 1: run.py failed"

        runs_dir = workdir / "runs"
        assert (runs_dir / "t1-0001" / "run_meta.json").is_file()
        # Phase 3 fixture writes the per-run merge_report under runs/<id>/.
        assert any((runs_dir / "t1-0001").glob("merge_report_*.json"))

        diff_path = tmp_path / "diff.json"
        rc_diff = _run_diff(runs_dir=runs_dir, output=diff_path)
        assert rc_diff == 0, "step 2: diff_against_golden failed"
        diff_payload = _read_json(diff_path)
        assert diff_payload["tier"] == 1
        samples = diff_payload["samples"]
        assert isinstance(samples, list) and len(samples) == 1

        report_md = tmp_path / "eval_report.md"
        rc_sum = _run_summarize(
            diff_path=diff_path, runs_dir=runs_dir, output=report_md
        )
        assert rc_sum == 0, "step 3: summarize failed"
        assert report_md.is_file()

        acceptance_json = tmp_path / "eval_acceptance.json"
        rc_gate = _run_gate(
            report=report_md, acceptance=ACCEPTANCE_YAML, output=acceptance_json
        )
        assert rc_gate == 0, "step 4: gate exit_code != 0"
        gate_payload = _read_json(acceptance_json)
        assert gate_payload["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# T8-E2 — chain refuses to proceed silently if an upstream step fails
# ---------------------------------------------------------------------------


class TestE2eFailurePropagation:
    def test_run_failure_short_circuits_chain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the fake merge binary to fail before producing artifacts.
        monkeypatch.setenv("FAKE_EXIT_CODE", "7")
        workdir = tmp_path / "run-1"
        rc_run = _run_merge_pipeline(workdir=workdir, seed=1, monkeypatch=monkeypatch)
        assert rc_run == 1, "run.py should propagate sample failure as rc=1"

        runs_dir = workdir / "runs"
        sample_dir = runs_dir / "t1-0001"
        # run_meta should still exist (status=failed) but no merge_report.
        meta = _read_json(sample_dir / "run_meta.json")
        assert meta["status"] == "failed"
        assert not any(sample_dir.glob("merge_report_*.json")), (
            "failed run must not surface artifacts that downstream steps trust"
        )

        # F5: diff emits a MISSING_REPORT stub so downstream RR / OA reflect
        # the failure instead of silently dropping the sample. rc=0 (no fatal
        # diff error) but the sample carries label=MISSING_REPORT.
        diff_path = tmp_path / "diff.json"
        rc_diff = _run_diff(runs_dir=runs_dir, output=diff_path)
        assert rc_diff == 0, "diff with run-but-no-artifacts should not be fatal"
        payload = _read_json(diff_path)
        sample_labels = {s["sample_id"]: s["label"] for s in payload["samples"]}
        assert sample_labels.get("t1-0001") == "MISSING_REPORT"


# ---------------------------------------------------------------------------
# T8-E3 — DET chain across 3 deterministic runs
# ---------------------------------------------------------------------------


class TestE2eDetChain:
    def test_three_runs_consistency_det_equals_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        merged_tree = _extract_golden_tree(
            TIER1_DATASETS / "t1-0001", tmp_path / "merged_tree"
        )
        run_dirs: list[Path] = []
        for i in (1, 2, 3):
            workdir = tmp_path / f"run-{i}"
            rc = _run_merge_pipeline(
                workdir=workdir,
                seed=i,
                monkeypatch=monkeypatch,
                merged_tree_dir=merged_tree,
            )
            assert rc == 0, f"run-{i} failed"
            run_dirs.append(workdir / "runs")

        consistency_out = tmp_path / "consistency.json"
        rc = consistency_mod.main(
            [
                "--runs",
                *[str(d) for d in run_dirs],
                "--metric",
                "DET",
                "--output",
                str(consistency_out),
            ]
        )
        assert rc == 0
        payload = _read_json(consistency_out)
        assert payload["metric"] == "DET"
        assert payload["value"] == 1.0
        assert payload["n_runs"] == 3
        assert payload["inconsistent"] == []


# ---------------------------------------------------------------------------
# Skip guard — fake_merge.sh requires a POSIX shell. Windows CI would hit
# this branch; the existing CI matrix only runs Linux/macOS so the marker
# is defensive rather than a real exclusion.
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="fake_merge.sh requires a POSIX shell"
)
