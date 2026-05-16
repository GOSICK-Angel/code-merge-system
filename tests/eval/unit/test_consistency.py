"""Tests for ``scripts.eval.consistency`` — Verifier T7-C1..T7-C5.

Covers DET / CPC computation, sample-set alignment, and CLI error paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.eval import consistency as consistency_mod
from scripts.eval.consistency import main


def _write_sample_report(
    sample_dir: Path,
    *,
    records: dict[str, dict[str, Any]],
    run_id: str = "FIXTURE",
) -> Path:
    """Write one ``merge_report_<run_id>.json`` under ``sample_dir``."""
    sample_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "file_decision_records": records}
    target = sample_dir / f"merge_report_{run_id}.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _decision(
    decision: str = "semantic_merge", risk: str = "AUTO_LOW"
) -> dict[str, Any]:
    return {
        "file_path": "hello.py",
        "decision": decision,
        "target_risk_level": risk,
        "decision_source": "auto_executor",
        "rationale": "x" * 40,
        "discarded_content": None,
        "is_security_sensitive": False,
    }


def _populate_run(
    runs_root: Path,
    run_name: str,
    *,
    samples: dict[str, dict[str, dict[str, Any]]],
) -> Path:
    """Build a runs/ directory containing per-sample merge_report files.

    ``samples`` maps ``sample_id -> {file_path: decision_record}``.
    """
    run_dir = runs_root / run_name
    for sample_id, records in samples.items():
        _write_sample_report(run_dir / sample_id, records=records, run_id=run_name)
    return run_dir


def _read_output(path: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


# ---------------------------------------------------------------------------
# T7-C1 — DET全一致 → 1.0
# ---------------------------------------------------------------------------


class TestDetAllAgree:
    def test_det_returns_one_when_all_runs_agree(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        for name in ("run-1", "run-2", "run-3"):
            _populate_run(
                runs_root,
                name,
                samples={
                    "t1-0001": {"hello.py": _decision()},
                    "t1-0002": {
                        "main.py": _decision("escalate_human", "HUMAN_REQUIRED")
                    },
                },
            )
        out = tmp_path / "consistency.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                str(runs_root / "run-3"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        assert payload["metric"] == "DET"
        assert payload["value"] == 1.0
        assert payload["n_runs"] == 3
        assert payload["total_files"] == 2
        assert payload["inconsistent"] == []


# ---------------------------------------------------------------------------
# T7-C2 — DET 部分不一致 → <1.0 + 列出不一致样本
# ---------------------------------------------------------------------------


class TestDetPartialDisagree:
    def test_det_lists_inconsistent_sample(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "run-1",
            samples={
                "t1-0001": {"hello.py": _decision()},
                "t1-0002": {"main.py": _decision()},
            },
        )
        _populate_run(
            runs_root,
            "run-2",
            samples={
                "t1-0001": {"hello.py": _decision()},
                "t1-0002": {"main.py": _decision()},
            },
        )
        # run-3 diverges only on t1-0001.
        _populate_run(
            runs_root,
            "run-3",
            samples={
                "t1-0001": {"hello.py": _decision("escalate_human", "HUMAN_REQUIRED")},
                "t1-0002": {"main.py": _decision()},
            },
        )
        out = tmp_path / "consistency.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                str(runs_root / "run-3"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        # 1 of 2 (sample,file) pairs agree.
        assert payload["value"] == pytest.approx(0.5)
        assert payload["total_files"] == 2
        inconsistent_ids = [row["sample_id"] for row in payload["inconsistent"]]
        assert inconsistent_ids == ["t1-0001"]
        decisions = payload["inconsistent"][0]["decisions"]
        # Three runs ⇒ three entries.
        assert len(decisions) == 3
        assert decisions[2]["decision"] == "escalate_human"


# ---------------------------------------------------------------------------
# T7-C3 — CPC 切 provider 走同一管道（用 2 run_dirs 即可）
# ---------------------------------------------------------------------------


class TestCpcSamePipeline:
    def test_cpc_uses_same_engine_with_two_runs(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "anthropic",
            samples={"t1-0001": {"hello.py": _decision()}},
        )
        _populate_run(
            runs_root,
            "openai",
            samples={"t1-0001": {"hello.py": _decision()}},
        )
        out = tmp_path / "cpc.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "anthropic"),
                str(runs_root / "openai"),
                "--metric",
                "CPC",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        assert payload["metric"] == "CPC"
        assert payload["value"] == 1.0
        assert payload["n_runs"] == 2

    def test_cpc_disagrees_marks_inconsistent(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "anthropic",
            samples={"t1-0001": {"hello.py": _decision("semantic_merge", "AUTO_LOW")}},
        )
        _populate_run(
            runs_root,
            "openai",
            samples={"t1-0001": {"hello.py": _decision("semantic_merge", "AUTO_HIGH")}},
        )
        out = tmp_path / "cpc.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "anthropic"),
                str(runs_root / "openai"),
                "--metric",
                "CPC",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        assert payload["value"] == 0.0
        assert payload["inconsistent"][0]["sample_id"] == "t1-0001"


# ---------------------------------------------------------------------------
# T7-C4 — runs < 2 → exit 1 + stderr 含 "requires"
# ---------------------------------------------------------------------------


class TestTooFewRuns:
    def test_single_run_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "run-1",
            samples={"t1-0001": {"hello.py": _decision()}},
        )
        out = tmp_path / "consistency.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 1
        captured = capsys.readouterr()
        assert "requires" in captured.err
        assert "DET" in captured.err
        assert not out.exists()


# ---------------------------------------------------------------------------
# T7-C5 — runs 间 sample_id 不一致 → exit 1 + stderr 列差集
# ---------------------------------------------------------------------------


class TestSampleSetMismatch:
    def test_disjoint_samples_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "run-1",
            samples={
                "t1-0001": {"hello.py": _decision()},
                "t1-0002": {"main.py": _decision()},
            },
        )
        _populate_run(
            runs_root,
            "run-2",
            samples={"t1-0001": {"hello.py": _decision()}},
        )
        out = tmp_path / "consistency.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 1
        captured = capsys.readouterr()
        assert "t1-0002" in captured.err
        assert "missing-somewhere" in captured.err
        assert not out.exists()


# ---------------------------------------------------------------------------
# Edge cases & internal helpers — keep cov ≥ 80% on consistency.py
# ---------------------------------------------------------------------------


class TestRunDirValidation:
    def test_nonexistent_run_dir_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "run-1",
            samples={"t1-0001": {"hello.py": _decision()}},
        )
        out = tmp_path / "out.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "ghost-run"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 1
        assert "run directory not found" in capsys.readouterr().err

    def test_sample_missing_merge_report_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "run-1",
            samples={"t1-0001": {"hello.py": _decision()}},
        )
        # run-2 has the sample directory but no merge_report file.
        (runs_root / "run-2" / "t1-0001").mkdir(parents=True)
        out = tmp_path / "out.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 1
        assert "no merge_report" in capsys.readouterr().err


class TestDualFieldNameFallback:
    """``strategy``/``risk`` should be treated as aliases for the JSON-mode
    ``decision``/``target_risk_level`` field names (matches diff_against_golden
    contract)."""

    def test_strategy_and_risk_aliases_normalise(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        # run-1 uses JSON-mode names.
        _write_sample_report(
            runs_root / "run-1" / "t1-0001",
            records={
                "hello.py": {
                    "file_path": "hello.py",
                    "decision": "semantic_merge",
                    "target_risk_level": "AUTO_LOW",
                }
            },
            run_id="run-1",
        )
        # run-2 uses MergeState alias names.
        _write_sample_report(
            runs_root / "run-2" / "t1-0001",
            records={
                "hello.py": {
                    "file_path": "hello.py",
                    "strategy": "semantic_merge",
                    "risk": "AUTO_LOW",
                }
            },
            run_id="run-2",
        )
        out = tmp_path / "out.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        assert payload["value"] == 1.0


class TestEmptySampleSet:
    """Two empty run dirs collapse to ``total_files=0 → value=1.0``."""

    def test_zero_samples_yields_vacuous_one(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        (runs_root / "run-1").mkdir(parents=True)
        (runs_root / "run-2").mkdir(parents=True)
        out = tmp_path / "out.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        assert payload["value"] == 1.0
        assert payload["total_files"] == 0
        assert payload["inconsistent"] == []


class TestAbsentFileSentinel:
    """A file decided in one run but missing from another should count as
    disagreement via the ``ABSENT`` sentinel."""

    def test_file_present_in_only_one_run(self, tmp_path: Path) -> None:
        runs_root = tmp_path / "runs"
        _populate_run(
            runs_root,
            "run-1",
            samples={
                "t1-0001": {
                    "a.py": _decision(),
                    "b.py": _decision(),
                }
            },
        )
        _populate_run(
            runs_root,
            "run-2",
            samples={"t1-0001": {"a.py": _decision()}},
        )
        out = tmp_path / "out.json"
        rc = main(
            [
                "--runs",
                str(runs_root / "run-1"),
                str(runs_root / "run-2"),
                "--metric",
                "DET",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        payload = _read_output(out)
        # 1 of 2 files agree.
        assert payload["value"] == pytest.approx(0.5)
        inconsistent_paths = [row["file_path"] for row in payload["inconsistent"]]
        assert inconsistent_paths == ["b.py"]
        decisions = payload["inconsistent"][0]["decisions"]
        assert decisions[1]["decision"] == "ABSENT"


class TestLocateMergeReportPicksLast:
    def test_picks_lex_last_when_multiple(self, tmp_path: Path) -> None:
        (tmp_path / "merge_report_aaa.json").write_text("{}", encoding="utf-8")
        (tmp_path / "merge_report_zzz.json").write_text("{}", encoding="utf-8")
        chosen = consistency_mod._locate_merge_report(tmp_path)
        assert chosen.name == "merge_report_zzz.json"
