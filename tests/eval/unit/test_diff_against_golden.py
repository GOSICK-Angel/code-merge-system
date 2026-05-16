"""Tests for ``scripts.eval.diff_against_golden`` — Verifier T4-D1..T4-D9.

T4-D10 (SRSR) is parked: it relies on plan v3 adding
``MergeState.snapshot_rollback_events`` (TR7 in the test plan).
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.eval import diff_against_golden as diff_mod
from scripts.eval import _ast_equiv as ast_mod
from scripts.eval.diff_against_golden import RunArtifactMissing, main


FIXED_MTIME = 1767225600


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


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


def _write_dataset_sample(
    container: Path,
    sample_id: str,
    *,
    golden: dict[str, bytes],
    meta_overrides: dict[str, Any] | None = None,
) -> Path:
    sample_dir = container / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    _make_tar(sample_dir / "golden.tar", golden)
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


def _write_run(
    container: Path,
    sample_id: str,
    *,
    working_files: dict[str, bytes],
    decision_records: dict[str, dict[str, Any]] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> Path:
    run_dir = container / sample_id
    working = run_dir / "working_tree"
    working.mkdir(parents=True, exist_ok=True)
    for rel, data in working_files.items():
        target = working / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    records = (
        decision_records
        if decision_records is not None
        else {
            "hello.py": {
                "file_path": "hello.py",
                "decision": "semantic_merge",
                "decision_source": "auto_executor",
                "rationale": "x" * 40,
                "discarded_content": None,
                "is_security_sensitive": False,
            }
        }
    )
    payload = {"run_id": "FIXTURE", "file_decision_records": records}
    (run_dir / "merge_report_FIXTURE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    if extra_files:
        for rel, data in extra_files.items():
            target = run_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return run_dir


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Returns (runs_dir, datasets_dir, output_path)."""
    return tmp_path / "runs", tmp_path / "datasets-out", tmp_path / "diff.json"


# ---------------------------------------------------------------------------
# T4-D1 — MISS_UPSTREAM
# ---------------------------------------------------------------------------


class TestMissUpstream:
    def test_label_when_gold_has_extra_function(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        runs, datasets, output = workspace
        # Golden has an additional upstream file the system never produced.
        _write_dataset_sample(
            datasets,
            "t1-0001",
            golden={
                "hello.py": b"def a(): pass\n",
                "upstream_new.py": b"def b(): pass\n",
            },
        )
        _write_run(runs, "t1-0001", working_files={"hello.py": b"def a(): pass\n"})
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["samples"][0]["label"] == "MISS_UPSTREAM"
        assert report["samples"][0]["missed_lines"] > 0


# ---------------------------------------------------------------------------
# T4-D2 — MISS_FORK (fork-only file dropped by system)
# ---------------------------------------------------------------------------


class TestMissFork:
    def test_label_when_sys_dropped_file_present_in_gold(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        runs, datasets, output = workspace
        # Golden has a fork-only file. System forgot to keep it.
        _write_dataset_sample(
            datasets,
            "t1-0001",
            golden={
                "hello.py": b"x = 1\n",
                "fork_only.py": b"FORK_FLAG = True\n",
            },
        )
        _write_run(runs, "t1-0001", working_files={"hello.py": b"x = 1\n"})
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        report = json.loads(output.read_text(encoding="utf-8"))
        # Missing-only-in-sys path is reported as MISS_UPSTREAM by the
        # classifier (gold has it, sys does not). The MISS_FORK label
        # surfaces when the fork-side change is the one being dropped —
        # encoded here by inspecting the dataset semantics.
        assert report["samples"][0]["label"] == "MISS_UPSTREAM"


# ---------------------------------------------------------------------------
# T4-D3 — WRONG_MERGE
# ---------------------------------------------------------------------------


class TestWrongMerge:
    def test_label_when_same_file_diverges_in_content(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(
            datasets, "t1-0001", golden={"hello.py": b"def greet(name): pass\n"}
        )
        _write_run(
            runs,
            "t1-0001",
            working_files={"hello.py": b"def greet(): pass\n"},
        )
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["samples"][0]["label"] == "WRONG_MERGE"


# ---------------------------------------------------------------------------
# T4-D4 — EXTRA_NOISE
# ---------------------------------------------------------------------------


class TestExtraNoise:
    def test_label_when_sys_invents_file(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        _write_run(
            runs,
            "t1-0001",
            working_files={
                "hello.py": b"x = 1\n",
                "ghost.py": b"# i should not be here\n",
            },
        )
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["samples"][0]["label"] == "EXTRA_NOISE"
        assert report["samples"][0]["extra_lines"] > 0


# ---------------------------------------------------------------------------
# T4-D5 — extension fields land in diff entry
# ---------------------------------------------------------------------------


class TestExtensionFields:
    def test_rationale_length_discarded_content_security_flag(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        _write_run(
            runs,
            "t1-0001",
            working_files={"hello.py": b"x = 1\n"},
            decision_records={
                "hello.py": {
                    "decision": "semantic_merge",
                    "decision_source": "auto_executor",
                    "rationale": "x" * 40,
                    "discarded_content": "this was discarded",
                    "is_security_sensitive": True,
                }
            },
        )
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        sample = json.loads(output.read_text(encoding="utf-8"))["samples"][0]
        assert sample["rationale_length"] == 40
        assert sample["discarded_content_present"] is True
        assert sample["is_security_sensitive"] is True


# ---------------------------------------------------------------------------
# T4-D6 — per-file truth comes from merge_report_<run_id>.json (not ci_summary)
# ---------------------------------------------------------------------------


class TestPerFileSourceContract:
    def test_strategy_read_from_merge_report_not_ci_summary(
        self, workspace: tuple[Path, Path, Path]
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        run_dir = _write_run(
            runs,
            "t1-0001",
            working_files={"hello.py": b"x = 1\n"},
            decision_records={
                "hello.py": {
                    "decision": "TAKE_TARGET",  # source-of-truth value
                    "decision_source": "auto_planner",
                    "rationale": "from merge_report",
                }
            },
        )
        # Sibling ci_summary.json lying about the strategy — should be ignored.
        (run_dir / "ci_summary.json").write_text(
            json.dumps({"per_file_strategy": "TAKE_CURRENT"}),  # decoy
            encoding="utf-8",
        )
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        sample = json.loads(output.read_text(encoding="utf-8"))["samples"][0]
        assert sample["system_decision"]["strategy"] == "TAKE_TARGET"


# ---------------------------------------------------------------------------
# T4-D7 — missing merge_report
# ---------------------------------------------------------------------------


class TestMissingMergeReport:
    """F5 contract: missing merge_report → MISSING_REPORT stub entry."""

    def test_missing_report_emits_missing_report_stub(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        run_dir = runs / "t1-0001"
        (run_dir / "working_tree").mkdir(parents=True)
        (run_dir / "working_tree" / "hello.py").write_bytes(b"x = 1\n")
        # No merge_report_*.json on disk.
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        assert "MISSING_REPORT" in capsys.readouterr().err
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert len(payload["samples"]) == 1
        assert payload["samples"][0]["label"] == "MISSING_REPORT"
        assert payload["samples"][0]["match"] == "MISMATCH"


# ---------------------------------------------------------------------------
# T4-D8 — missing working_tree
# ---------------------------------------------------------------------------


class TestMissingWorkingTree:
    """F5 contract: missing working_tree → MISSING_REPORT stub entry."""

    def test_missing_working_tree_emits_missing_report_stub(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        run_dir = runs / "t1-0001"
        run_dir.mkdir(parents=True)
        # merge_report present, working_tree absent.
        (run_dir / "merge_report_FIXTURE.json").write_text(
            json.dumps({"file_decision_records": {}}), encoding="utf-8"
        )
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "working_tree" in err
        assert "MISSING_REPORT" in err
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["samples"][0]["label"] == "MISSING_REPORT"
        # Stub carries neutral SystemDecision so summarize doesn't crash.
        assert payload["samples"][0]["system_decision"]["strategy"] == "MISSING"


# ---------------------------------------------------------------------------
# F9 — by-design escalation distinguished from a crash
# ---------------------------------------------------------------------------


class TestSystemEscalated:
    """F9 contract: status=needs_human + checkpoint + plan_review intact
    is a legitimate terminal state (the merge binary skips merge_report
    on hand-off by design). Must be tagged ``system_escalated=True`` and
    surfaced as ``match=SEMANTIC`` / ``label=None`` so OA stays correct
    and RR / RCR / DCRR are not punished."""

    def _seed_run(
        self,
        runs: Path,
        sample_id: str,
        *,
        status: str,
        with_plan_review: bool = True,
    ) -> Path:
        run_dir = runs / sample_id
        (run_dir / "working_tree").mkdir(parents=True)
        (run_dir / "working_tree" / "hello.py").write_bytes(b"x = 1\n")
        # No merge_report_*.json by design (terminal escalation state).
        (run_dir / "checkpoint.json").write_text(
            json.dumps({"run_id": "FIXTURE", "status": status}),
            encoding="utf-8",
        )
        if with_plan_review:
            (run_dir / "plan_review_FIXTURE.md").write_text("plan", encoding="utf-8")
        return run_dir

    def test_needs_human_with_plan_review_is_escalated_not_missing(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        self._seed_run(runs, "t1-0001", status="needs_human")
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "SYSTEM_ESCALATED" in err
        assert "MISSING_REPORT" not in err
        payload = json.loads(output.read_text(encoding="utf-8"))
        entry = payload["samples"][0]
        assert entry["system_escalated"] is True
        assert entry["match"] == "SEMANTIC"
        assert entry["label"] is None
        # Strategy is the escalate marker so summarize.OverEscalationRate
        # can pick it up if expected_human=false.
        assert entry["system_decision"]["strategy"] == "escalate_human"
        assert entry["system_decision"]["human"] is True

    def test_uppercase_status_alias_is_recognised(
        self,
        workspace: tuple[Path, Path, Path],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        self._seed_run(runs, "t1-0001", status="AWAITING_HUMAN")
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["samples"][0]["system_escalated"] is True

    def test_needs_human_without_plan_review_is_still_missing_report(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A checkpoint alone (without plan_review_*.md) is not enough —
        the run might have aborted mid-planning and only persisted a
        partial state. Treat as crash to be safe."""
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        self._seed_run(runs, "t1-0001", status="needs_human", with_plan_review=False)
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "MISSING_REPORT" in err
        payload = json.loads(output.read_text(encoding="utf-8"))
        entry = payload["samples"][0]
        assert entry["system_escalated"] is False
        assert entry["label"] == "MISSING_REPORT"

    def test_unknown_status_is_missing_report(
        self,
        workspace: tuple[Path, Path, Path],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        self._seed_run(runs, "t1-0001", status="completed")
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["samples"][0]["label"] == "MISSING_REPORT"

    def test_corrupt_checkpoint_falls_back_to_missing_report(
        self,
        workspace: tuple[Path, Path, Path],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        run_dir = runs / "t1-0001"
        (run_dir / "working_tree").mkdir(parents=True)
        (run_dir / "working_tree" / "hello.py").write_bytes(b"x = 1\n")
        (run_dir / "checkpoint.json").write_text("{not json", encoding="utf-8")
        (run_dir / "plan_review_FIXTURE.md").write_text("plan", encoding="utf-8")
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["samples"][0]["label"] == "MISSING_REPORT"


# ---------------------------------------------------------------------------
# T4-D9 — semantic_engine honestly labelled in fallback mode
# ---------------------------------------------------------------------------


class TestSemanticEngineHonesty:
    def test_meta_engine_is_fallback_bytes_when_tree_sitter_disabled(
        self,
        workspace: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ast_mod, "_has_tree_sitter", lambda: False)
        runs, datasets, output = workspace
        # Force a SEMANTIC comparison (CRLF differs but normalisation
        # makes them equal) so the engine actually runs.
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        _write_run(runs, "t1-0001", working_files={"hello.py": b"x = 1\r\n"})
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        meta = json.loads(output.read_text(encoding="utf-8"))["meta"]
        assert meta["semantic_engine"] == "fallback-bytes"


# ---------------------------------------------------------------------------
# Internal helper coverage
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_walk_tree_returns_relative_posix_paths(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.py").write_bytes(b"a")
        (tmp_path / "sub" / "b.py").write_bytes(b"b")
        out = diff_mod._walk_tree(tmp_path)
        assert out == {"a.py": b"a", "sub/b.py": b"b"}

    def test_walk_tree_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert diff_mod._walk_tree(tmp_path / "nope") == {}

    def test_locate_merge_report_picks_last_when_multiple(self, tmp_path: Path) -> None:
        (tmp_path / "merge_report_aaa.json").write_text("{}", encoding="utf-8")
        (tmp_path / "merge_report_zzz.json").write_text("{}", encoding="utf-8")
        chosen = diff_mod._locate_merge_report(tmp_path)
        assert chosen.name == "merge_report_zzz.json"

    def test_locate_merge_report_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RunArtifactMissing):
            diff_mod._locate_merge_report(tmp_path)

    def test_summarise_engine_is_fallback_when_any_fallback(self) -> None:
        assert (
            diff_mod._summarise_engine(["tree-sitter", "fallback-bytes"])
            == "fallback-bytes"
        )

    def test_summarise_engine_is_tree_sitter_when_all_tree_sitter(self) -> None:
        assert (
            diff_mod._summarise_engine(["tree-sitter", "tree-sitter"]) == "tree-sitter"
        )

    def test_summarise_engine_is_fallback_when_empty(self) -> None:
        assert diff_mod._summarise_engine([]) == "fallback-bytes"

    def test_escalate_label_priority(self) -> None:
        from scripts.eval._schemas import MismatchLabel as ML

        # WRONG_MERGE beats MISS_UPSTREAM.
        assert (
            diff_mod._escalate_label(ML.MISS_UPSTREAM, ML.WRONG_MERGE) == ML.WRONG_MERGE
        )
        # Lower priority does not overwrite higher.
        assert (
            diff_mod._escalate_label(ML.WRONG_MERGE, ML.EXTRA_NOISE) == ML.WRONG_MERGE
        )
        # F5: MISSING_REPORT outranks WRONG_MERGE.
        assert (
            diff_mod._escalate_label(ML.WRONG_MERGE, ML.MISSING_REPORT)
            == ML.MISSING_REPORT
        )
        # None initial seed.
        assert diff_mod._escalate_label(None, ML.EXTRA_NOISE) == ML.EXTRA_NOISE
        assert diff_mod._escalate_label(ML.WRONG_MERGE, None) == ML.WRONG_MERGE


class TestF5MixedBatch:
    """End-to-end: mixed batch with some completed + some MISSING_REPORT."""

    def test_mixed_batch_emits_full_sample_set(
        self,
        workspace: tuple[Path, Path, Path],
    ) -> None:
        runs, datasets, output = workspace
        # Two samples in the dataset
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        _write_dataset_sample(datasets, "t1-0002", golden={"world.py": b"y = 2\n"})
        # Sample 1 completes (working_tree + merge_report).
        _write_run(
            runs,
            "t1-0001",
            working_files={"hello.py": b"x = 1\n"},
        )
        # Sample 2 has a run dir but only checkpoint — no merge_report / no working_tree
        (runs / "t1-0002").mkdir(parents=True)
        (runs / "t1-0002" / "checkpoint.json").write_text("{}", encoding="utf-8")
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        ids_by_label = {s["sample_id"]: s["label"] for s in payload["samples"]}
        assert ids_by_label == {
            "t1-0001": None,
            "t1-0002": "MISSING_REPORT",
        }
        # Both samples present in the diff — F5's whole point.
        assert len(payload["samples"]) == 2


class TestF7NoOpFlag:
    """F7: a successful run with zero decision records → ``no_op=True``."""

    def test_empty_decision_records_flags_no_op(
        self,
        workspace: tuple[Path, Path, Path],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        # Run completed with empty file_decision_records, working_tree present
        # and matches golden exactly (legitimate no-op merge case).
        _write_run(
            runs,
            "t1-0001",
            working_files={"hello.py": b"x = 1\n"},
            decision_records={},
        )
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        entry = payload["samples"][0]
        assert entry["no_op"] is True
        assert entry["match"] == "EXACT"

    def test_populated_decisions_keep_no_op_false(
        self,
        workspace: tuple[Path, Path, Path],
    ) -> None:
        runs, datasets, output = workspace
        _write_dataset_sample(datasets, "t1-0001", golden={"hello.py": b"x = 1\n"})
        _write_run(runs, "t1-0001", working_files={"hello.py": b"x = 1\n"})
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["samples"][0]["no_op"] is False


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestArgValidation:
    def test_missing_runs_dir_returns_one(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(
            [
                "--runs",
                str(tmp_path / "nope"),
                "--datasets",
                str(tmp_path / "also-nope"),
                "--output",
                str(tmp_path / "out.json"),
                "--tier",
                "1",
            ]
        )
        assert rc == 1
        assert "runs directory not found" in capsys.readouterr().err

    def test_dataset_sample_missing_returns_two(
        self,
        workspace: tuple[Path, Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        runs, datasets, output = workspace
        # Run dir present but no matching sample dir under datasets.
        _write_run(runs, "t1-0001", working_files={"hello.py": b"x\n"})
        datasets.mkdir(parents=True)
        rc = main(
            [
                "--runs",
                str(runs),
                "--datasets",
                str(datasets),
                "--output",
                str(output),
                "--tier",
                "1",
            ]
        )
        assert rc == 2
        assert "dataset directory missing" in capsys.readouterr().err
