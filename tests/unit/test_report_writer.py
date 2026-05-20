"""Regression tests for plan-review report fidelity and run-log discoverability.

Two independent fixes are pinned here:

  * ``PhaseFileBatch.original_file_paths`` — a frozen snapshot of batch
    membership so the plan-review report renders the plan *as the human
    signed off on it*, not the drained post-``auto_merge`` state (which
    showed "Files (0)" for already-applied batches).
  * The orchestrator co-locates ``run.log`` next to ``checkpoint.json`` so
    the Web UI's "check the merge process logs" hint is actionable.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.core.orchestrator import Orchestrator
from src.models.config import MergeConfig
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState
from src.tools.report_writer import write_plan_review_report


def _config(**overrides) -> MergeConfig:
    defaults = {"upstream_ref": "upstream/main", "fork_ref": "fork/main"}
    defaults.update(overrides)
    return MergeConfig(**defaults)


def _plan_with_batch(file_paths: list[str]) -> MergePlan:
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        merge_base_commit="abc123",
        phases=[
            PhaseFileBatch(
                batch_id="b1",
                phase=MergePhase.AUTO_MERGE,
                file_paths=list(file_paths),
                risk_level="auto_safe",
            )
        ],
        risk_summary=RiskSummary(
            total_files=len(file_paths),
            auto_safe_count=len(file_paths),
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="test project",
    )


class TestOriginalFilePathsSnapshot:
    def test_snapshot_captured_at_construction(self) -> None:
        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["a.go", "b.go", "c.go"],
            risk_level="auto_safe",
        )
        assert batch.original_file_paths == ["a.go", "b.go", "c.go"]

    def test_snapshot_survives_draining_file_paths(self) -> None:
        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["a.go", "b.go", "c.go"],
            risk_level="auto_safe",
        )
        # auto_merge drains file_paths as files are applied.
        batch.file_paths = []
        assert batch.file_paths == []
        assert batch.original_file_paths == ["a.go", "b.go", "c.go"]

    def test_explicit_snapshot_not_overwritten(self) -> None:
        batch = PhaseFileBatch(
            batch_id="b1",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["a.go"],
            risk_level="auto_safe",
            original_file_paths=["a.go", "b.go"],
        )
        assert batch.original_file_paths == ["a.go", "b.go"]


class TestPlanReviewReportFidelity:
    def test_report_shows_original_files_after_drain(self, tmp_path) -> None:
        plan = _plan_with_batch(["x.go", "y.go", "z.go"])
        state = MergeState(config=_config(), merge_plan=plan)
        # Simulate auto_merge having applied + drained the batch.
        plan.phases[0].file_paths = []

        report_path = write_plan_review_report(state, str(tmp_path))
        text = report_path.read_text(encoding="utf-8")

        assert "Files (3)" in text or "(3)" in text
        for fp in ("x.go", "y.go", "z.go"):
            assert fp in text
        assert "Files (0)" not in text


class TestRunLogCoLocation:
    def test_run_log_written_next_to_run_dir(self, tmp_path, monkeypatch) -> None:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        sys_log = tmp_path / "syslogs"
        run_dir = repo / ".merge" / "runs" / "run-xyz"

        monkeypatch.setattr(
            "src.core.orchestrator.get_system_log_dir", lambda repo_path=".": sys_log
        )
        monkeypatch.setattr(
            "src.core.orchestrator.get_run_dir",
            lambda repo_path=".", run_id="": run_dir,
        )

        config = _config(repo_path=str(repo))
        orch = Orchestrator(config)
        try:
            orch._setup_run_logger("run-xyz")
            logging.getLogger("src.core.orchestrator").info("hello-from-test")
            for h in (orch._log_handler, orch._run_dir_log_handler):
                if h is not None:
                    h.flush()

            co_located = run_dir / "run.log"
            assert co_located.exists()
            assert "hello-from-test" in co_located.read_text(encoding="utf-8")
        finally:
            orch._teardown_run_logger()

        assert orch._run_dir_log_handler is None
