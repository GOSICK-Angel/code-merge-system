"""Tests for ``scripts.eval.gate`` — Verifier T6-G1..T6-G11."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.eval.gate import (
    _derive_verdict,
    _operator_passes,
    cmd_gate,
    main,
    parse_metric_table,
)
from scripts.eval._schemas import (
    AcceptanceThresholds,
    GateKind,
    GateResult,
    GateVerdict,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SHA256_HEX = "0" * 64


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_report(metrics: dict[str, float | str]) -> str:
    """Build a minimal markdown table that ``parse_metric_table`` accepts."""
    lines = ["# Eval Report", "", "| Metric | Value |", "|---|---|"]
    for key, value in metrics.items():
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines) + "\n"


def _full_pass_report() -> dict[str, float | str]:
    return {
        "OA": 1.0,
        "WMR": 0.0,
        "MMR": 0.0,
        "WDR": 0.0,
        "SSER": 1.0,
        "DCRR": 1.0,
        "SRSR": 1.0,
        "RR": 1.0,
        "RCR": 1.0,
        "Recall_M1": 1.0,
        "Recall_M2": 1.0,
        "Recall_M3": 1.0,
        "Recall_M4": 1.0,
        "Recall_M5": 1.0,
        "Recall_M6": 1.0,
        "CRA": 1.0,
        "OverEscalationRate": 0.0,
        "JA": 1.0,
        "DET": 1.0,
        "CPC": 1.0,
        "cost_usd_per_run_p95": 0.0,
        "wall_time_seconds_p95": 0.0,
        "plan_revision_rounds_p95": 0.0,
    }


def _full_pass_thresholds() -> dict[str, Any]:
    return {
        "synced_with_sha": SHA256_HEX,
        "synced_at": "2026-05-15T00:00:00+00:00",
        "hard_gates": [
            {
                "id": "WMR",
                "kind": "absolute",
                "threshold": 0.0,
                "operator": "==",
                "source": "full",
            },
            {
                "id": "SSER",
                "kind": "absolute",
                "threshold": 1.0,
                "operator": "==",
                "source": "T1",
            },
            {
                "id": "DCRR",
                "kind": "absolute",
                "threshold": 1.0,
                "operator": "==",
                "source": "T1",
            },
            {
                "id": "SRSR",
                "kind": "absolute",
                "threshold": 1.0,
                "operator": "==",
                "source": "full",
            },
            {
                "id": "MMR",
                "kind": "absolute",
                "threshold": 0.02,
                "operator": "<=",
                "source": "T1",
            },
            {
                "id": "RR",
                "kind": "absolute",
                "threshold": 1.0,
                "operator": "==",
                "source": "full",
            },
            {
                "id": "RCR",
                "kind": "absolute",
                "threshold": 1.0,
                "operator": "==",
                "source": "full",
            },
        ],
        "soft_gates": [
            {
                "id": "OA",
                "kind": "absolute",
                "threshold": 0.92,
                "operator": ">=",
                "source": "T1",
            },
            {
                "id": "CRA",
                "kind": "absolute",
                "threshold": 0.88,
                "operator": ">=",
                "source": "T1",
            },
            {
                "id": "cost_usd_per_run_p95",
                "kind": "relative",
                "multiplier": 1.15,
                "source": "full",
            },
        ],
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "report.md", tmp_path / "thresholds.yaml", tmp_path / "out.json"


def _run_gate(*args: str) -> int:
    return main(list(args))


# ---------------------------------------------------------------------------
# T6-G1 — full pass + verdict PASS + exit 0
# ---------------------------------------------------------------------------


class TestFullPass:
    def test_all_gates_pass_returns_zero(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        report, yml, out = workspace
        report.write_text(_build_report(_full_pass_report()), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "PASS"
        assert all(g["pass"] is True for g in payload["hard_gates"])
        # Relative gates skipped due to no baseline.
        cost = next(
            g for g in payload["soft_gates"] if g["id"] == "cost_usd_per_run_p95"
        )
        assert cost["pass"] is None
        assert cost["skipped_reason"] == "no baseline"


# ---------------------------------------------------------------------------
# T6-G2 — CRA soft threshold tightened → soft fail
# ---------------------------------------------------------------------------


class TestSchemaDriven:
    def test_cra_threshold_change_triggers_soft_fail(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["CRA"] = 0.85
        report.write_text(_build_report(metrics), encoding="utf-8")
        thresholds = _full_pass_thresholds()
        for g in thresholds["soft_gates"]:
            if g["id"] == "CRA":
                g["threshold"] = 0.99
        _write_yaml(yml, thresholds)
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 2
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "NEEDS_REVIEW"
        cra = next(g for g in payload["soft_gates"] if g["id"] == "CRA")
        assert cra["pass"] is False


# ---------------------------------------------------------------------------
# T6-G3 — gates carry the kind field
# ---------------------------------------------------------------------------


class TestKindField:
    def test_each_gate_has_kind(self, workspace: tuple[Path, Path, Path]) -> None:
        report, yml, out = workspace
        report.write_text(_build_report(_full_pass_report()), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        for g in payload["hard_gates"]:
            assert g["kind"] == "absolute"
        kinds = {g["id"]: g["kind"] for g in payload["soft_gates"]}
        assert kinds["OA"] == "absolute"
        assert kinds["cost_usd_per_run_p95"] == "relative"


# ---------------------------------------------------------------------------
# T6-G4 — synced_with_sha transparently propagated to meta
# ---------------------------------------------------------------------------


class TestSyncedShaPassThrough:
    def test_sha_propagated_to_acceptance_meta(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        report, yml, out = workspace
        report.write_text(_build_report(_full_pass_report()), encoding="utf-8")
        thresholds = _full_pass_thresholds()
        thresholds["synced_with_sha"] = "1" * 64
        _write_yaml(yml, thresholds)
        _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["datasets"]["acceptance_thresholds_sha"] == "1" * 64


# ---------------------------------------------------------------------------
# T6-G5 — hard fail → exit 1 + FAIL
# ---------------------------------------------------------------------------


class TestHardFail:
    def test_wmr_breach_returns_one(self, workspace: tuple[Path, Path, Path]) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["WMR"] = 0.05
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 1
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "FAIL"
        wmr = next(g for g in payload["hard_gates"] if g["id"] == "WMR")
        assert wmr["pass"] is False


# ---------------------------------------------------------------------------
# T6-G6 — absolute soft gate breach → exit 2 + NEEDS_REVIEW
# ---------------------------------------------------------------------------


class TestAbsoluteSoftFail:
    def test_oa_below_threshold_exits_two(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["OA"] = 0.80
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 2
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "NEEDS_REVIEW"
        oa = next(g for g in payload["soft_gates"] if g["id"] == "OA")
        assert oa["pass"] is False
        assert oa["kind"] == "absolute"


# ---------------------------------------------------------------------------
# T6-G7 — yaml missing rejects execution
# ---------------------------------------------------------------------------


class TestMissingYaml:
    def test_missing_yaml_returns_one(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        report, yml, out = workspace
        report.write_text(_build_report(_full_pass_report()), encoding="utf-8")
        # do NOT write yml
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 1
        assert "thresholds yaml not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T6-G8 — no --baseline + relative gate → SKIP (pass=null)
# ---------------------------------------------------------------------------


class TestRelativeSkip:
    def test_no_baseline_relative_gate_skipped(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["cost_usd_per_run_p95"] = 0.20
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "PASS"
        cost = next(
            g for g in payload["soft_gates"] if g["id"] == "cost_usd_per_run_p95"
        )
        assert cost["pass"] is None
        assert cost["skipped_reason"] == "no baseline"
        assert cost["kind"] == "relative"
        assert payload["baseline"] is None
        assert (
            "skipped 1 relative gate(s) due to no baseline" in capsys.readouterr().err
        )


# ---------------------------------------------------------------------------
# T6-G9 — baseline present + cost over 1.15× → exit 2 + NEEDS_REVIEW
# ---------------------------------------------------------------------------


class TestRelativeBreach:
    def test_cost_exceeds_baseline_multiplier(
        self, workspace: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["cost_usd_per_run_p95"] = 0.20
        report.write_text(_build_report(metrics), encoding="utf-8")
        baseline_metrics = _full_pass_report()
        baseline_metrics["cost_usd_per_run_p95"] = 0.10
        baseline = tmp_path / "baseline.md"
        baseline.write_text(_build_report(baseline_metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report",
            str(report),
            "--acceptance",
            str(yml),
            "--output",
            str(out),
            "--baseline",
            str(baseline),
        )
        assert rc == 2
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "NEEDS_REVIEW"
        cost = next(
            g for g in payload["soft_gates"] if g["id"] == "cost_usd_per_run_p95"
        )
        assert cost["pass"] is False
        assert cost["baseline_value"] == 0.10
        assert abs(cost["computed_threshold"] - 0.115) < 1e-9
        assert cost["value"] == 0.20


# ---------------------------------------------------------------------------
# T6-G10 — baseline present + cost within 1.15× → exit 0
# ---------------------------------------------------------------------------


class TestRelativePass:
    def test_cost_within_multiplier_passes(
        self, workspace: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["cost_usd_per_run_p95"] = 0.11
        report.write_text(_build_report(metrics), encoding="utf-8")
        baseline_metrics = _full_pass_report()
        baseline_metrics["cost_usd_per_run_p95"] = 0.10
        baseline = tmp_path / "baseline.md"
        baseline.write_text(_build_report(baseline_metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report",
            str(report),
            "--acceptance",
            str(yml),
            "--output",
            str(out),
            "--baseline",
            str(baseline),
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "PASS"
        cost = next(
            g for g in payload["soft_gates"] if g["id"] == "cost_usd_per_run_p95"
        )
        assert cost["pass"] is True
        assert abs(cost["computed_threshold"] - 0.115) < 1e-9


# ---------------------------------------------------------------------------
# T6-G11 — hard + soft fail → exit 1 (hard takes priority)
# ---------------------------------------------------------------------------


class TestHardOverridesSoft:
    def test_hard_and_soft_fail_returns_one(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["WMR"] = 0.05  # hard fail
        metrics["OA"] = 0.80  # soft fail
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 1
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"] == "FAIL"
        wmr = next(g for g in payload["hard_gates"] if g["id"] == "WMR")
        oa = next(g for g in payload["soft_gates"] if g["id"] == "OA")
        assert wmr["pass"] is False
        assert oa["pass"] is False


# ---------------------------------------------------------------------------
# Real committed yaml smoke
# ---------------------------------------------------------------------------


class TestCommittedYaml:
    def test_committed_thresholds_yaml_validates(self) -> None:
        path = REPO_ROOT / "tests/eval/manifests/acceptance_thresholds.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        thresholds = AcceptanceThresholds.model_validate(payload)
        ids_hard = {g.id for g in thresholds.hard_gates}
        # SRSR is present (follow-up) and WDR is INTENTIONALLY absent.
        assert "SRSR" in ids_hard
        assert "WDR" not in ids_hard
        ids_soft = {g.id for g in thresholds.soft_gates}
        assert "cost_usd_per_run_p95" in ids_soft
        rel = next(g for g in thresholds.soft_gates if g.id == "cost_usd_per_run_p95")
        assert rel.kind == "relative"
        assert rel.multiplier == 1.15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_parse_metric_table_round_trip(self) -> None:
        md = "| OA | 0.92 |\n| Note | N/A |\n"
        out = parse_metric_table(md)
        assert out["OA"] == 0.92
        assert out["Note"] == "N/A"

    def test_operator_passes_eq(self) -> None:
        assert _operator_passes("==", 1.0, 1.0)
        assert not _operator_passes("==", 1.0, 0.0)

    def test_operator_passes_ge_le(self) -> None:
        assert _operator_passes(">=", 0.95, 0.9)
        assert _operator_passes("<=", 0.02, 0.02)
        assert not _operator_passes("<=", 0.03, 0.02)

    def test_operator_passes_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            _operator_passes("!=", 1.0, 1.0)

    def test_derive_verdict_priority(self) -> None:
        hard_pass = GateResult(
            id="WMR",
            kind=GateKind.ABSOLUTE,
            value=0.0,
            threshold=0.0,
            **{"pass": True},  # type: ignore[arg-type]
        )
        hard_fail = GateResult(
            id="WMR",
            kind=GateKind.ABSOLUTE,
            value=0.05,
            threshold=0.0,
            **{"pass": False},  # type: ignore[arg-type]
        )
        soft_fail = GateResult(
            id="OA",
            kind=GateKind.ABSOLUTE,
            value=0.8,
            threshold=0.92,
            **{"pass": False},  # type: ignore[arg-type]
        )
        assert _derive_verdict((hard_pass,), ())[1] == 0
        assert _derive_verdict((hard_fail,), (soft_fail,)) == (GateVerdict.FAIL, 1)
        assert _derive_verdict((hard_pass,), (soft_fail,)) == (
            GateVerdict.NEEDS_REVIEW,
            2,
        )

    def test_cmd_gate_missing_report_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = cmd_gate(
            report_path=tmp_path / "nope.md",
            acceptance_yaml=tmp_path / "nope.yaml",
            baseline_path=None,
            output=tmp_path / "out.json",
            version="x",
        )
        assert rc == 1
        assert "report not found" in capsys.readouterr().err


class TestSkipPaths:
    """Phase 6 P2-2 carry-forward: absent metric → SKIP path coverage."""

    def test_absolute_gate_absent_metric_skipped(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        del metrics["WMR"]
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 0
        wmr = next(
            g
            for g in json.loads(out.read_text(encoding="utf-8"))["hard_gates"]
            if g["id"] == "WMR"
        )
        assert wmr["pass"] is None
        assert "not numeric" in wmr["skipped_reason"]

    def test_relative_gate_absent_metric_skipped(
        self, workspace: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        del metrics["cost_usd_per_run_p95"]
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _full_pass_thresholds())
        baseline = tmp_path / "baseline.md"
        baseline.write_text(_build_report(_full_pass_report()), encoding="utf-8")
        rc = _run_gate(
            "--report",
            str(report),
            "--acceptance",
            str(yml),
            "--output",
            str(out),
            "--baseline",
            str(baseline),
        )
        assert rc == 0
        cost = next(
            g
            for g in json.loads(out.read_text(encoding="utf-8"))["soft_gates"]
            if g["id"] == "cost_usd_per_run_p95"
        )
        assert cost["pass"] is None
        assert "not numeric" in cost["skipped_reason"]


# ---------------------------------------------------------------------------
# BCP (build-check pass rate, metrics.md §8.5) — enforced soft gate
# ---------------------------------------------------------------------------


def _bcp_thresholds() -> dict[str, Any]:
    payload = _full_pass_thresholds()
    payload["soft_gates"].append(
        {
            "id": "BCP",
            "kind": "absolute",
            "threshold": 1.0,
            "operator": "==",
            "source": "configured build_check runs",
        }
    )
    return payload


class TestBCP:
    def test_bcp_pass_at_one(self, workspace: tuple[Path, Path, Path]) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["BCP"] = 1.0
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _bcp_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 0
        bcp = next(
            g for g in json.loads(out.read_text())["soft_gates"] if g["id"] == "BCP"
        )
        assert bcp["pass"] is True

    def test_bcp_soft_fail_below_one(self, workspace: tuple[Path, Path, Path]) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["BCP"] = 0.5  # one configured run failed to build
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _bcp_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        # Soft breach → NEEDS_REVIEW / exit 2, not a hard fail.
        assert rc == 2
        bcp = next(
            g for g in json.loads(out.read_text())["soft_gates"] if g["id"] == "BCP"
        )
        assert bcp["pass"] is False

    def test_bcp_skips_when_na(self, workspace: tuple[Path, Path, Path]) -> None:
        report, yml, out = workspace
        metrics = _full_pass_report()
        metrics["BCP"] = "N/A (no run executed build_check)"
        report.write_text(_build_report(metrics), encoding="utf-8")
        _write_yaml(yml, _bcp_thresholds())
        rc = _run_gate(
            "--report", str(report), "--acceptance", str(yml), "--output", str(out)
        )
        assert rc == 0  # SKIP never fails the verdict
        bcp = next(
            g for g in json.loads(out.read_text())["soft_gates"] if g["id"] == "BCP"
        )
        assert bcp["pass"] is None
        assert "not numeric" in bcp["skipped_reason"]
