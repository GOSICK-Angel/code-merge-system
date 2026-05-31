"""Meta tests for ``.github/workflows/ci.yml`` — Verifier T9-W1..W7.

These tests do NOT run CI — they statically parse the workflow yaml and
assert the eval-impl rollout invariants (step names, cov source,
non-blocking guarantee, manual-only ``eval-tier1`` job, etc.) so a
regression in CI configuration is caught at unit-test time rather than
after a merge.
"""

from __future__ import annotations

import io
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_workflow(text: str | None = None) -> dict[Any, Any]:
    """Parse ci.yml (or an in-memory variant) into a python dict.

    ``yaml.safe_load`` will interpret the literal key ``on`` as the
    boolean ``True`` under YAML 1.1, so the top-level keys are typed as
    ``Any`` rather than ``str``.
    """
    if text is None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(io.StringIO(text))
    assert isinstance(parsed, dict)
    return parsed


def _step_names(job: dict[str, Any]) -> list[str]:
    return [s.get("name") for s in job.get("steps", []) if s.get("name")]


def _step_by_name(job: dict[str, Any], name: str) -> dict[str, Any]:
    for s in job.get("steps", []):
        if s.get("name") == name:
            step: dict[str, Any] = s
            return step
    raise AssertionError(f"step {name!r} not found in job (have: {_step_names(job)})")


def _workflow_on(data: dict[Any, Any]) -> dict[str, Any]:
    """Return the ``on:`` mapping.

    ``yaml.safe_load`` interprets the literal key ``on`` as the boolean
    ``True`` under YAML 1.1; new-style "on" can also appear as a string
    key in stricter parsers. Handle both.
    """
    section: Any = data.get(True, data.get("on"))
    if isinstance(section, dict):
        return section
    return {}


# ---------------------------------------------------------------------------
# T9-W1 — 5 必备 step 存在
# ---------------------------------------------------------------------------


REQUIRED_EVAL_STEP_NAMES = (
    "Lint eval scripts (ruff)",
    "Type check eval scripts (mypy)",
    "Eval unit + e2e tests",
    "Verify dataset locks",
    "Fork name purity check",
)


class TestRequiredEvalSteps:
    def test_all_five_steps_present(self) -> None:
        data = _load_workflow()
        test_job = data["jobs"]["test"]
        names = _step_names(test_job)
        missing = [s for s in REQUIRED_EVAL_STEP_NAMES if s not in names]
        assert not missing, f"missing eval CI steps: {missing}"


# ---------------------------------------------------------------------------
# T9-W2 — cov source 独立（--cov=scripts/eval 而非 --cov=src）
# ---------------------------------------------------------------------------


class TestCovSourceIndependent:
    def test_eval_step_uses_scripts_eval_cov(self) -> None:
        data = _load_workflow()
        step = _step_by_name(data["jobs"]["test"], "Eval unit + e2e tests")
        run_cmd = step.get("run", "")
        assert "--cov=scripts/eval" in run_cmd
        # Must not accidentally inherit the src-only cov from the prior step.
        assert "--cov=src" not in run_cmd

    def test_unit_tests_step_uses_src_cov(self) -> None:
        data = _load_workflow()
        step = _step_by_name(data["jobs"]["test"], "Unit tests")
        run_cmd = step.get("run", "")
        # Defensive — guarantees we did NOT merge the two cov sources.
        assert "--cov=src" in run_cmd
        assert "--cov=scripts/eval" not in run_cmd


# ---------------------------------------------------------------------------
# T9-W3 — mypy scripts/eval tests/eval 独立 step
# ---------------------------------------------------------------------------


class TestMypyEvalStepIndependent:
    def test_eval_mypy_step_does_not_share_with_src_mypy(self) -> None:
        data = _load_workflow()
        eval_step = _step_by_name(
            data["jobs"]["test"], "Type check eval scripts (mypy)"
        )
        eval_cmd = eval_step.get("run", "")
        assert "mypy scripts/eval tests/eval" in eval_cmd

        src_step = _step_by_name(data["jobs"]["test"], "Type check (mypy)")
        src_cmd = src_step.get("run", "")
        # src mypy must remain `mypy src` and NOT pull in scripts/eval.
        assert "scripts/eval" not in src_cmd
        assert "mypy src" in src_cmd


# ---------------------------------------------------------------------------
# T9-W4 — 缺任一 eval step 时测试 fail
# ---------------------------------------------------------------------------


class TestMissingStepDetected:
    def test_removing_verify_dataset_locks_fails_required_check(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        # Remove the step header + its run command. Two-line yaml stanza is
        # narrow enough that a regex is safer than yaml round-trip.
        mutated = re.sub(
            r"      - name: Verify dataset locks\n        run: .*\n\n?",
            "",
            text,
            count=1,
        )
        assert "Verify dataset locks" not in mutated, (
            "regex failed to strip the step; update the test"
        )
        data = _load_workflow(mutated)
        names = _step_names(data["jobs"]["test"])
        missing = [s for s in REQUIRED_EVAL_STEP_NAMES if s not in names]
        assert "Verify dataset locks" in missing


# ---------------------------------------------------------------------------
# T9-W5 — eval-tier1 manual job + workflow_dispatch trigger
# ---------------------------------------------------------------------------


class TestEvalTier1ManualTrigger:
    def test_eval_tier1_job_exists(self) -> None:
        data = _load_workflow()
        assert "eval-tier1" in data["jobs"]

    def test_workflow_dispatch_or_schedule_trigger(self) -> None:
        data = _load_workflow()
        on_section = _workflow_on(data)
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        # Accept any of: workflow_dispatch in `on:`, or a schedule entry,
        # or a "nightly placeholder, not blocking" comment.
        has_dispatch = "workflow_dispatch" in on_section
        has_schedule = "schedule" in on_section
        has_placeholder_comment = "nightly placeholder, not blocking" in text
        assert has_dispatch or has_schedule or has_placeholder_comment


# ---------------------------------------------------------------------------
# T9-W6 — unit suite 时长回归 (≤ 25s 本地; CI 跳过自递归)
# ---------------------------------------------------------------------------


class TestUnitSuiteRuntime:
    @pytest.mark.skipif(
        os.getenv("CI") is not None, reason="self-recursive when CI=true"
    )
    def test_unit_suite_under_threshold(self) -> None:
        import subprocess
        import sys

        started = time.perf_counter()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/eval/unit/",
                "-q",
                "-p",
                "no:cov",
                "--no-header",
                "--ignore=tests/eval/unit/test_ci_workflow_meta.py",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.perf_counter() - started
        assert result.returncode == 0, result.stdout + result.stderr
        # 25s leaves 5s headroom below the plan Phase 9 GO §1 30s budget.
        assert elapsed <= 25.0, f"unit suite took {elapsed:.2f}s (> 25s budget)"


# ---------------------------------------------------------------------------
# T9-W7 — eval-tier1 不阻塞 PR (3 OR 条件之一成立)
# ---------------------------------------------------------------------------


class TestEvalTier1NonBlocking:
    def test_one_of_three_non_blocking_conditions_holds(self) -> None:
        data = _load_workflow()
        job = data["jobs"]["eval-tier1"]
        if_clause = str(job.get("if", ""))
        continue_on_error = bool(job.get("continue-on-error", False))
        # Per-job ``on:`` is invalid GitHub-Actions syntax; for the "on does
        # not include pull_request" branch we look at the workflow-level on.
        on_section = _workflow_on(data)
        triggers_only_dispatch_or_schedule = "pull_request" not in on_section and (
            "workflow_dispatch" in on_section or "schedule" in on_section
        )
        assert (
            "github.event_name != 'pull_request'" in if_clause
            or continue_on_error
            or triggers_only_dispatch_or_schedule
        ), "eval-tier1 must guarantee non-blocking via one of the 3 mechanisms"
