"""Tests for ``scripts.eval.summarize`` — Verifier T5-S1..T5-S5."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.eval import summarize as summarize_mod
from scripts.eval.summarize import (
    RECALL_LABELS,
    _build_context,
    _compute_metrics,
    _failure_rows,
    main,
)


REPORT_FIXTURE_LOSS: dict[str, Any] = {
    "tier": 1,
    "samples": [
        {
            "sample_id": "t1-0001",
            "category": "C",
            "loss_class": None,
            "expected_human": False,
            "system_decision": {
                "strategy": "SEMANTIC_MERGE",
                "risk": "AUTO_RISKY",
                "human": False,
            },
            "match": "MISMATCH",
            "label": "WRONG_MERGE",
            "missed_lines": 3,
            "extra_lines": 1,
            "rationale_length": 42,
            "discarded_content_present": True,
            "is_security_sensitive": True,
        }
    ],
    "meta": {
        "semantic_engine": "fallback-bytes",
        "generated_at": "2026-05-15T00:00:00+00:00",
    },
}


def _write_diff(path: Path, payload: dict[str, Any] = REPORT_FIXTURE_LOSS) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_run_meta(
    runs_dir: Path,
    sample_id: str,
    *,
    concurrency: int = 1,
    wall_time: float = 12.0,
    cost_usd: float = 0.01,
    status: str = "success",
    extras: dict[str, Any] | None = None,
) -> Path:
    target = runs_dir / sample_id
    target.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "sample_id": sample_id,
        "run_id": f"r-{sample_id}",
        "seed": 0,
        "concurrency": concurrency,
        "cache_disabled": False,
        "wall_time_seconds": wall_time,
        "cost_usd": cost_usd,
        "git_sha": "deadbeef",
        "model_matrix": {"planner": "claude-opus-4-7"},
        "status": status,
        "memory_clean_check": "passed",
        "exit_code": 0 if status == "success" else 1,
    }
    if extras:
        payload.update(extras)
    meta_path = target / "run_meta.json"
    meta_path.write_text(json.dumps(payload), encoding="utf-8")
    return meta_path


def _run_summarize(*args: str) -> int:
    return main(list(args))


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "diff.json", tmp_path / "runs", tmp_path / "report.md"


# ---------------------------------------------------------------------------
# T5-S1 — 18 metric anchors present
# ---------------------------------------------------------------------------


class TestEighteenMetricAnchors:
    def test_hard_and_soft_anchors_in_output(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        _write_run_meta(runs, "t1-0001")
        rc = _run_summarize(
            "--diff", str(diff), "--runs", str(runs), "--output", str(out)
        )
        assert rc == 0
        body = out.read_text(encoding="utf-8")
        # Hard 9 (acceptance.md §1)
        for anchor in (
            "OA",
            "WMR",
            "MMR",
            "WDR",
            "SSER",
            "DCRR",
            "SRSR",
            "RR",
            "RCR",
        ):
            assert re.search(rf"\b{anchor}\b", body), anchor
        # Recall_M1..M6
        for label in RECALL_LABELS:
            assert f"Recall_{label}" in body
        # Soft 9 (acceptance.md §2)
        for anchor in (
            "CRA",
            "OverEscalationRate",
            "JA",
            "DET",
            "CPC",
            "cost_usd_per_run_p95",
            "wall_time_seconds_p95",
            "plan_revision_rounds_p95",
        ):
            assert anchor in body, anchor


# ---------------------------------------------------------------------------
# T5-S2 / T5-S3 — concurrency banner
# ---------------------------------------------------------------------------


class TestConcurrencyBanner:
    def test_serial_run_omits_banner(self, workspace: tuple[Path, Path, Path]) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        _write_run_meta(runs, "t1-0001", concurrency=1)
        _run_summarize("--diff", str(diff), "--runs", str(runs), "--output", str(out))
        assert "wall_time/cost not authoritative" not in out.read_text(encoding="utf-8")

    def test_parallel_run_inserts_banner(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        _write_run_meta(runs, "t1-0001", concurrency=4)
        _run_summarize("--diff", str(diff), "--runs", str(runs), "--output", str(out))
        body = out.read_text(encoding="utf-8")
        assert "wall_time/cost not authoritative" in body
        assert "max=4" in body


# ---------------------------------------------------------------------------
# T5-S4 — missing run_meta returns non-zero
# ---------------------------------------------------------------------------


class TestMissingRunMeta:
    def test_missing_run_meta_returns_two(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        # Create the run dir but skip run_meta.json.
        (runs / "t1-0001").mkdir(parents=True)
        rc = _run_summarize(
            "--diff", str(diff), "--runs", str(runs), "--output", str(out)
        )
        assert rc == 2
        assert "run_meta.json" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T5-S5 — failure list sorted by sample_id
# ---------------------------------------------------------------------------


class TestFailureSortOrder:
    def test_failures_emitted_in_sample_id_order(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        diff, runs, out = workspace
        payload = json.loads(json.dumps(REPORT_FIXTURE_LOSS))
        payload["samples"] = [
            _sample_template("z-0001"),
            _sample_template("a-0001"),
            _sample_template("m-0001"),
        ]
        _write_diff(diff, payload)
        for sid in ("z-0001", "a-0001", "m-0001"):
            _write_run_meta(runs, sid)
        _run_summarize("--diff", str(diff), "--runs", str(runs), "--output", str(out))
        body = out.read_text(encoding="utf-8")
        # Locate the failure table rows by leading "| <id> |".
        a_idx = body.index("| a-0001 |")
        m_idx = body.index("| m-0001 |")
        z_idx = body.index("| z-0001 |")
        # Only assert order within the failure section (lower in body).
        # First the per-tier table (which appears in dataset order); we
        # check that the *last* occurrence of each id is in sorted order.
        a_last = body.rindex("| a-0001 |")
        m_last = body.rindex("| m-0001 |")
        z_last = body.rindex("| z-0001 |")
        assert a_last < m_last < z_last
        # Sanity: first occurrence covers them all too.
        assert a_idx >= 0 and m_idx >= 0 and z_idx >= 0


def _sample_template(sample_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "category": "C",
        "loss_class": None,
        "expected_human": False,
        "system_decision": {
            "strategy": "SEMANTIC_MERGE",
            "risk": "AUTO_RISKY",
            "human": False,
        },
        "match": "MISMATCH",
        "label": "WRONG_MERGE",
        "missed_lines": 0,
        "extra_lines": 0,
        "rationale_length": 40,
        "discarded_content_present": False,
        "is_security_sensitive": False,
    }


# ---------------------------------------------------------------------------
# ci_summary wrapping (Phase 3 P2-2 carry-forward)
# ---------------------------------------------------------------------------


class TestCiSummaryWrappingAwareness:
    def test_invalid_json_flagged_in_known_issues(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        _write_run_meta(runs, "t1-0001")
        # Mimic the runner's {"invalid_json": True} envelope.
        ci_path = runs / "t1-0001" / "ci_summary.json"
        ci_path.write_text(json.dumps({"invalid_json": True}), encoding="utf-8")
        _run_summarize("--diff", str(diff), "--runs", str(runs), "--output", str(out))
        assert "non-JSON stdout" in out.read_text(encoding="utf-8")

    def test_raw_value_flagged_in_known_issues(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        _write_run_meta(runs, "t1-0001")
        ci_path = runs / "t1-0001" / "ci_summary.json"
        ci_path.write_text(json.dumps({"raw_value": [1, 2, 3]}), encoding="utf-8")
        _run_summarize("--diff", str(diff), "--runs", str(runs), "--output", str(out))
        assert "non-dict top-level value" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI arg validation
# ---------------------------------------------------------------------------


class TestArgValidation:
    def test_missing_diff_file_returns_one(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        diff, runs, out = workspace
        runs.mkdir()
        rc = _run_summarize(
            "--diff", str(diff), "--runs", str(runs), "--output", str(out)
        )
        assert rc == 1
        assert "diff file not found" in capsys.readouterr().err

    def test_baseline_path_propagated_to_report(
        self, workspace: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        diff, runs, out = workspace
        _write_diff(diff)
        _write_run_meta(runs, "t1-0001")
        baseline = tmp_path / "old_report.md"
        baseline.write_text("baseline body containing OA marker", encoding="utf-8")
        _run_summarize(
            "--diff",
            str(diff),
            "--runs",
            str(runs),
            "--output",
            str(out),
            "--baseline",
            str(baseline),
        )
        body = out.read_text(encoding="utf-8")
        assert "No baseline supplied" not in body


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_compute_metrics_empty_samples_returns_na_keys(self) -> None:
        from scripts.eval._schemas import DiffEntry as _DE

        metrics = _compute_metrics(tuple(), {})
        for k in (
            "OA",
            "WMR",
            "MMR",
            "WDR",
            "SSER",
            "DCRR",
            "RR",
            "RCR",
            "CRA",
            "OverEscalationRate",
        ):
            assert metrics[k] == "N/A"
        # Silence the import-only-for-name lint when ruff inspects this.
        _ = _DE

    def test_failure_rows_sorted_by_sample_id(self) -> None:
        from scripts.eval._schemas import (
            DiffEntry,
            MatchStatus,
            MismatchLabel,
            SystemDecision,
        )

        sd = SystemDecision(strategy="TAKE_TARGET", risk="AUTO_RISKY", human=False)
        s_z = DiffEntry(
            sample_id="z",
            category="C",
            expected_human=False,
            system_decision=sd,
            match=MatchStatus.MISMATCH,
            label=MismatchLabel.WRONG_MERGE,
        )
        s_a = DiffEntry(
            sample_id="a",
            category="C",
            expected_human=False,
            system_decision=sd,
            match=MatchStatus.MISMATCH,
            label=MismatchLabel.WRONG_MERGE,
        )
        s_ok = DiffEntry(
            sample_id="m",
            category="C",
            expected_human=False,
            system_decision=sd,
            match=MatchStatus.EXACT,
        )
        rows = _failure_rows((s_z, s_a, s_ok))
        assert [r["sample_id"] for r in rows] == ["a", "z"]

    def test_build_context_uses_mixed_marker_when_git_sha_differs(
        self, tmp_path: Path
    ) -> None:
        from scripts.eval._schemas import DiffReport, DiffReportMeta, RunMeta

        diff = DiffReport(
            tier=1,
            samples=tuple(),
            meta=DiffReportMeta(semantic_engine="fallback-bytes"),
        )
        m1 = RunMeta(
            sample_id="a",
            run_id="r1",
            seed=0,
            concurrency=1,
            wall_time_seconds=0.0,
            cost_usd=0.0,
            git_sha="aaa",
        )
        m2 = RunMeta(
            sample_id="b",
            run_id="r2",
            seed=0,
            concurrency=1,
            wall_time_seconds=0.0,
            cost_usd=0.0,
            git_sha="bbb",
        )
        ctx = _build_context(
            diff,
            {"a": m1, "b": m2},
            tmp_path,
            baseline_path=None,
            dataset_lock_sha="x",
        )
        assert ctx["git_sha"] == "<mixed>"

    def test_percentile_single_value_returns_value(self) -> None:
        from scripts.eval.summarize import _percentile

        assert _percentile([7.5], 95) == 7.5

    def test_percentile_empty_returns_na(self) -> None:
        from scripts.eval.summarize import _percentile

        assert _percentile([], 95) == "N/A"

    def test_load_ci_summary_missing_returns_empty(self, tmp_path: Path) -> None:
        from scripts.eval.summarize import _load_ci_summary

        assert _load_ci_summary(tmp_path, "missing") == {}

    def test_load_ci_summary_non_dict_wrapped(self, tmp_path: Path) -> None:
        from scripts.eval.summarize import _load_ci_summary

        sample = tmp_path / "abc"
        sample.mkdir()
        (sample / "ci_summary.json").write_text("[1,2,3]", encoding="utf-8")
        result = _load_ci_summary(tmp_path, "abc")
        assert result == {"raw_value": [1, 2, 3]}

    def test_module_constants(self) -> None:
        assert summarize_mod.RATIONALE_EXCERPT_LIMIT > 0
        assert "M3" in RECALL_LABELS

    def test_sser_real_formula_no_sensitive_samples_is_one(self) -> None:
        from scripts.eval._schemas import DiffEntry, MatchStatus, SystemDecision
        from scripts.eval.summarize import _compute_sser

        sd = SystemDecision(strategy="TAKE_TARGET", risk="AUTO_SAFE", human=False)
        s = DiffEntry(
            sample_id="t1-0001",
            category="C",
            expected_human=False,
            system_decision=sd,
            match=MatchStatus.EXACT,
            is_security_sensitive=False,
        )
        assert _compute_sser((s,)) == 1.0

    def test_sser_real_formula_sensitive_escalated_is_one(self) -> None:
        from scripts.eval._schemas import DiffEntry, MatchStatus, SystemDecision
        from scripts.eval.summarize import _compute_sser

        sd_human = SystemDecision(
            strategy="ESCALATE_HUMAN", risk="HUMAN_REQUIRED", human=True
        )
        s = DiffEntry(
            sample_id="t1-0001",
            category="C",
            expected_human=True,
            system_decision=sd_human,
            match=MatchStatus.EXACT,
            is_security_sensitive=True,
        )
        assert _compute_sser((s,)) == 1.0

    def test_sser_real_formula_sensitive_not_escalated_is_zero(self) -> None:
        from scripts.eval._schemas import DiffEntry, MatchStatus, SystemDecision
        from scripts.eval.summarize import _compute_sser

        sd_auto = SystemDecision(strategy="TAKE_TARGET", risk="AUTO_SAFE", human=False)
        s = DiffEntry(
            sample_id="t1-0001",
            category="C",
            expected_human=False,
            system_decision=sd_auto,
            match=MatchStatus.EXACT,
            is_security_sensitive=True,
        )
        assert _compute_sser((s,)) == 0.0

    def test_rr_real_formula_three_artifacts_present(self, tmp_path: Path) -> None:
        from scripts.eval.summarize import _compute_rr

        sample_dir = tmp_path / "t1-0001"
        sample_dir.mkdir()
        (sample_dir / "merge_report_FIXTURE.json").write_text("{}", encoding="utf-8")
        (sample_dir / "merge_report_FIXTURE.md").write_text("ok", encoding="utf-8")
        (sample_dir / "plan_review_FIXTURE.md").write_text("ok", encoding="utf-8")
        assert _compute_rr(tmp_path, ["t1-0001"]) == 1.0

    def test_rr_real_formula_missing_plan_review_is_zero(self, tmp_path: Path) -> None:
        from scripts.eval.summarize import _compute_rr

        sample_dir = tmp_path / "t1-0001"
        sample_dir.mkdir()
        (sample_dir / "merge_report_FIXTURE.json").write_text("{}", encoding="utf-8")
        (sample_dir / "merge_report_FIXTURE.md").write_text("ok", encoding="utf-8")
        assert _compute_rr(tmp_path, ["t1-0001"]) == 0.0

    def test_rr_real_formula_runs_dir_none_is_one(self) -> None:
        from scripts.eval.summarize import _compute_rr

        assert _compute_rr(None, ["t1-0001"]) == 1.0
