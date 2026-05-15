"""Tests for ``scripts.eval.run`` — Verifier matrix T3-R1..T3-R8."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts.eval import lock as lock_mod
from scripts.eval import run as run_mod
from scripts.eval.run import (
    MEMORY_DB_RELATIVE,
    MemoryLeakDetected,
    _assert_clean_memory,
    main,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FAKE_MERGE_BIN = REPO_ROOT / "tests/eval/fixtures/fake_merge_bin/fake_merge.sh"
DUMMY_RUN_FIXTURE = REPO_ROOT / "tests/eval/fixtures/dummy_run"
FIXED_MTIME = 1767225600


# ---------------------------------------------------------------------------
# Sample / dataset fixtures
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


def _whole_file_patch(old: str, new: str, path: str = "hello.py") -> bytes:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    out = [f"--- a/{path}\n", f"+++ b/{path}\n"]
    out.append(f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@\n")
    out.extend(("-" + line) for line in old_lines)
    out.extend(("+" + line) for line in new_lines)
    return "".join(out).encode("utf-8")


def _write_sample(container: Path, sample_id: str) -> Path:
    sample_dir = container / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    base = b"def greet(): pass\n"
    fork = b"# fork comment\ndef greet(): pass\n"
    golden = b"# fork comment\ndef greet(name): pass\n"
    _make_tar(sample_dir / "base.tar", {"hello.py": base})
    _make_tar(sample_dir / "golden.tar", {"hello.py": golden})
    (sample_dir / "fork.patch").write_bytes(
        _whole_file_patch(base.decode(), fork.decode())
    )
    (sample_dir / "upstream.patch").write_bytes(
        _whole_file_patch(base.decode(), b"def greet(name): pass\n".decode())
    )
    (sample_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
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
def datasets_two_samples(tmp_path: Path) -> Path:
    """Two-sample tier-1 dataset for T3-R3 / T3-R5 / T3-R8."""
    root = tmp_path / "datasets"
    _write_sample(root / "tier1" / "samples", "t1-0001")
    _write_sample(root / "tier1" / "samples", "t1-0002")
    (root / "tier2" / "replays").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def datasets_one_sample(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"
    _write_sample(root / "tier1" / "samples", "t1-0001")
    (root / "tier2" / "replays").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def manifests_dir(tmp_path: Path) -> Path:
    target = tmp_path / "manifests"
    target.mkdir()
    return target


def _refresh_lock(datasets: Path, manifests: Path) -> None:
    rc = lock_mod.main(
        ["--update", "--datasets", str(datasets), "--manifests", str(manifests)]
    )
    assert rc == 0


def _common_args(
    datasets: Path,
    manifests: Path,
    workdir: Path,
    *,
    tier: int = 1,
    concurrency: int = 1,
    merge_bin: Path = FAKE_MERGE_BIN,
) -> list[str]:
    return [
        "--tier",
        str(tier),
        "--workdir",
        str(workdir),
        "--concurrency",
        str(concurrency),
        "--merge-bin",
        str(merge_bin),
        "--datasets",
        str(datasets),
        "--manifests",
        str(manifests),
    ]


# ---------------------------------------------------------------------------
# T3-R1 — happy path produces all required artifacts
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_run_produces_seven_artifact_families(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        workdir = tmp_path / "workdir"
        rc = main(_common_args(datasets_one_sample, manifests_dir, workdir))
        assert rc == 0
        sample_out = workdir / "runs" / "t1-0001"
        assert (sample_out / "run_meta.json").is_file()
        assert (sample_out / "ci_summary.json").is_file()
        assert (sample_out / "checkpoint.json").is_file()
        assert (sample_out / "working_tree").is_dir()
        # merge_report_*.json/.md and plan_review_*.md should be copied
        assert any(sample_out.glob("merge_report_*.json"))
        assert any(sample_out.glob("merge_report_*.md"))
        assert any(sample_out.glob("plan_review_*.md"))


# ---------------------------------------------------------------------------
# T3-R2 — env strips MERGE_DEV
# ---------------------------------------------------------------------------


class TestEnvIsolation:
    def test_subprocess_env_does_not_contain_merge_dev(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        monkeypatch.setenv("FAKE_DUMP_ENV", "1")
        monkeypatch.setenv("MERGE_DEV", "1")  # would leak if not stripped
        workdir = tmp_path / "workdir"
        rc = main(_common_args(datasets_one_sample, manifests_dir, workdir))
        assert rc == 0
        env_dump = list((workdir / "runs" / "t1-0001").glob("*"))
        # The fake script writes _env.json into the per-run subdirectory of cwd,
        # which run.py then copies up. Look in the per-run dir AND the cwd dir.
        cwd_env = list((workdir / "runs" / "t1-0001" / "_cwd").rglob("_env.json"))
        assert cwd_env, f"_env.json not found in {env_dump}"
        env_payload = json.loads(cwd_env[0].read_text(encoding="utf-8"))
        assert "MERGE_DEV" not in env_payload
        assert env_payload.get("ANTHROPIC_API_KEY") == "DUMMY-EVAL-KEY"
        assert env_payload.get("OPENAI_API_KEY") == "DUMMY-EVAL-KEY"


# ---------------------------------------------------------------------------
# T3-R3 — independent cwds + memory.db absent
# ---------------------------------------------------------------------------


class TestCwdIsolation:
    def test_each_sample_gets_unique_cwd_and_no_memory_db(
        self,
        datasets_two_samples: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_two_samples, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        monkeypatch.setenv("FAKE_DUMP_ENV", "1")
        workdir = tmp_path / "workdir"
        rc = main(_common_args(datasets_two_samples, manifests_dir, workdir))
        assert rc == 0
        cwd_files: dict[str, Path] = {}
        for sample_id in ("t1-0001", "t1-0002"):
            cwd_dir = workdir / "runs" / sample_id / "_cwd"
            assert cwd_dir.is_dir()
            cwd_txt = next(cwd_dir.rglob("_cwd.txt"), None)
            assert cwd_txt is not None
            cwd_files[sample_id] = cwd_txt
            recorded_cwd = Path(cwd_txt.read_text(encoding="utf-8").strip())
            # cwd path must end with /runs/<sample_id>/_cwd
            assert recorded_cwd.parent.name == sample_id
            # memory.db must NOT exist (the fake script did not create one)
            assert not (cwd_dir / MEMORY_DB_RELATIVE).exists()
        assert cwd_files["t1-0001"].read_text() != cwd_files["t1-0002"].read_text()


# ---------------------------------------------------------------------------
# T3-R4 — RunMeta has the required fields including concurrency
# ---------------------------------------------------------------------------


class TestRunMeta:
    def test_run_meta_contains_required_fields(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        workdir = tmp_path / "workdir"
        rc = main(
            _common_args(datasets_one_sample, manifests_dir, workdir, concurrency=2)
        )
        assert rc == 0
        meta = json.loads(
            (workdir / "runs" / "t1-0001" / "run_meta.json").read_text(encoding="utf-8")
        )
        required = {
            "wall_time_seconds",
            "cost_usd",
            "model_matrix",
            "git_sha",
            "seed",
            "concurrency",
            "cache_disabled",
        }
        assert required.issubset(meta.keys())
        assert meta["concurrency"] == 2
        assert meta["status"] == "success"
        assert meta["memory_clean_check"] == "passed"


# ---------------------------------------------------------------------------
# T3-R5 — failed merge subprocess is isolated; partial result still landed
# ---------------------------------------------------------------------------


class TestFailedSampleIsolation:
    def test_failed_sample_marked_failed_and_other_continues(
        self,
        datasets_two_samples: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_two_samples, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        monkeypatch.setenv("FAKE_EXIT_CODE", "1")
        workdir = tmp_path / "workdir"
        rc = main(_common_args(datasets_two_samples, manifests_dir, workdir))
        # Both samples should have failed because we forced exit code 1 globally.
        assert rc == 1
        for sample_id in ("t1-0001", "t1-0002"):
            meta = json.loads(
                (workdir / "runs" / sample_id / "run_meta.json").read_text(
                    encoding="utf-8"
                )
            )
            assert meta["status"] == "failed"
            assert meta["exit_code"] == 1
        # ci_summary.json still landed for each sample (captures stderr message).
        assert (workdir / "runs" / "t1-0001" / "ci_summary.json").is_file()


# ---------------------------------------------------------------------------
# T3-R6 — workdir not writable yields non-zero exit
# ---------------------------------------------------------------------------


class TestWorkdirReadOnly:
    def test_unwritable_workdir_raises(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        readonly_root = tmp_path / "ro_root"
        readonly_root.mkdir()
        # Strip write bits.
        readonly_root.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            workdir = readonly_root / "workdir"
            with pytest.raises((PermissionError, OSError)):
                main(_common_args(datasets_one_sample, manifests_dir, workdir))
        finally:
            readonly_root.chmod(stat.S_IRWXU)  # restore for cleanup


# ---------------------------------------------------------------------------
# T3-R7 — concurrency=0 rejected
# ---------------------------------------------------------------------------


class TestConcurrencyValidation:
    def test_zero_concurrency_returns_two(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        workdir = tmp_path / "workdir"
        rc = main(
            _common_args(datasets_one_sample, manifests_dir, workdir, concurrency=0)
        )
        assert rc == 2
        assert "concurrency must be >= 1" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# T3-R8 — memory leak detection (positive + negative)
# ---------------------------------------------------------------------------


class TestMemoryLeakGuard:
    def test_assert_clean_memory_passes_when_absent(self, tmp_path: Path) -> None:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        # No raise.
        _assert_clean_memory(cwd, "t1-0001")

    def test_assert_clean_memory_raises_when_present(self, tmp_path: Path) -> None:
        cwd = tmp_path / "cwd"
        (cwd / ".merge").mkdir(parents=True)
        (cwd / MEMORY_DB_RELATIVE).write_bytes(b"x")
        with pytest.raises(MemoryLeakDetected) as exc:
            _assert_clean_memory(cwd, "t1-0001")
        assert exc.value.sample_id == "t1-0001"

    def test_run_with_pre_existing_memory_db_fails_that_sample(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        workdir = tmp_path / "workdir"
        # Pre-create the cwd with a stale memory.db before run.py spawns.
        sample_cwd = workdir / "runs" / "t1-0001" / "_cwd"
        (sample_cwd / ".merge").mkdir(parents=True)
        (sample_cwd / MEMORY_DB_RELATIVE).write_bytes(b"x")
        rc = main(_common_args(datasets_one_sample, manifests_dir, workdir))
        assert rc == 1  # leak detected, partial failure

    def test_fake_merge_writing_memory_lands_only_in_its_own_cwd(
        self,
        datasets_two_samples: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When fake-merge writes ``.merge/memory.db`` in its cwd, the file
        must be **inside that sample's `_cwd/.merge/memory.db` only** —
        never in the sibling sample's `_cwd/` or the workdir root.

        With FAKE_TOUCH_MEMORY=1 the fake script writes memory.db into
        each sample's own cwd; the cross-sample isolation guarantee is
        that one sample's memory.db never appears under another sample's
        cwd path.
        """
        _refresh_lock(datasets_two_samples, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        monkeypatch.setenv("FAKE_TOUCH_MEMORY", "1")
        workdir = tmp_path / "workdir"
        rc = main(
            _common_args(datasets_two_samples, manifests_dir, workdir, concurrency=1)
        )
        assert rc == 0
        cwd1 = workdir / "runs" / "t1-0001" / "_cwd"
        cwd2 = workdir / "runs" / "t1-0002" / "_cwd"
        # Each sample wrote its own memory.db (fake created it during
        # the subprocess). The two paths must be distinct files.
        db1 = cwd1 / MEMORY_DB_RELATIVE
        db2 = cwd2 / MEMORY_DB_RELATIVE
        assert db1.is_file()
        assert db2.is_file()
        assert db1.resolve() != db2.resolve()
        # Workdir-level guard: there is no memory.db at workdir root.
        assert not (workdir / MEMORY_DB_RELATIVE).exists()


# ---------------------------------------------------------------------------
# Stdout JSON edge cases
# ---------------------------------------------------------------------------


class TestStdoutCapture:
    def test_invalid_json_stdout_recorded_as_invalid(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        monkeypatch.setenv("FAKE_BAD_JSON", "1")
        workdir = tmp_path / "workdir"
        rc = main(_common_args(datasets_one_sample, manifests_dir, workdir))
        assert rc == 0
        ci = json.loads(
            (workdir / "runs" / "t1-0001" / "ci_summary.json").read_text(
                encoding="utf-8"
            )
        )
        assert ci.get("invalid_json") is True
        assert "raw_stdout" in ci

    def test_empty_stdout_records_empty_dict(
        self,
        datasets_one_sample: Path,
        manifests_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _refresh_lock(datasets_one_sample, manifests_dir)
        monkeypatch.setenv("FAKE_FIXTURE_DIR", str(DUMMY_RUN_FIXTURE))
        monkeypatch.setenv("FAKE_SAMPLE_ID", "t1-0001")
        monkeypatch.setenv("FAKE_NO_OUTPUT", "1")
        workdir = tmp_path / "workdir"
        rc = main(_common_args(datasets_one_sample, manifests_dir, workdir))
        assert rc == 0
        ci = json.loads(
            (workdir / "runs" / "t1-0001" / "ci_summary.json").read_text(
                encoding="utf-8"
            )
        )
        assert ci == {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_locate_merge_run_dir_returns_none_when_missing(
        self, tmp_path: Path
    ) -> None:
        assert run_mod._locate_merge_run_dir(tmp_path / ".merge") is None

    def test_locate_merge_run_dir_returns_single(self, tmp_path: Path) -> None:
        runs = tmp_path / ".merge" / "runs"
        single = runs / "rXYZ"
        single.mkdir(parents=True)
        assert run_mod._locate_merge_run_dir(tmp_path / ".merge") == single

    def test_locate_merge_run_dir_returns_none_when_multiple(
        self, tmp_path: Path
    ) -> None:
        runs = tmp_path / ".merge" / "runs"
        (runs / "rA").mkdir(parents=True)
        (runs / "rB").mkdir(parents=True)
        assert run_mod._locate_merge_run_dir(tmp_path / ".merge") is None

    def test_persist_ci_summary_handles_dict(self, tmp_path: Path) -> None:
        dest = tmp_path / "ci.json"
        payload = run_mod._persist_ci_summary('{"status":"ok"}', dest)
        assert payload == {"status": "ok"}
        assert json.loads(dest.read_text(encoding="utf-8")) == {"status": "ok"}

    def test_persist_ci_summary_handles_non_dict_json(self, tmp_path: Path) -> None:
        dest = tmp_path / "ci.json"
        payload = run_mod._persist_ci_summary("[1,2,3]", dest)
        assert payload == {"raw_value": [1, 2, 3]}

    def test_copy_working_tree_skips_merge_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("a", encoding="utf-8")
        (src / ".merge").mkdir()
        (src / ".merge" / "memory.db").write_text("x", encoding="utf-8")
        dest = tmp_path / "dest"
        run_mod._copy_working_tree(src, dest)
        assert (dest / "a.py").read_text() == "a"
        assert not (dest / ".merge").exists()

    def test_git_sha_returns_string(self) -> None:
        sha = run_mod._git_sha()
        assert isinstance(sha, str)
        assert sha != ""


# Suppress unused-import warning for shutil/os in ruff if no other usage.
_ = shutil, os
